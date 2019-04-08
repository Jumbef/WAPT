#!/usr/bin/python
# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------
#    This file is part of WAPT
#    Copyright (C) 2013  Tranquil IT Systems http://www.tranquil.it
#    WAPT aims to help Windows systems administrators to deploy
#    setup and update applications on users PC.
#
#    WAPT is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    WAPT is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with WAPT.  If not, see <http://www.gnu.org/licenses/>.
#
# -----------------------------------------------------------------------
from __future__ import absolute_import
from waptutils import __version__

import os
import sys
import codecs
import base64
import hashlib
import glob
import subprocess
import logging
import time
import urlparse
import datetime

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization,hashes
from cryptography.hazmat.primitives.asymmetric import padding,utils,rsa,AsymmetricVerificationContext,AsymmetricVerificationContext
from cryptography.x509.extensions import ExtensionNotFound,AccessDescription,DistributionPoint
from cryptography.x509.verification import CertificateVerificationContext, InvalidCertificate, InvalidSigningCertificate
from cryptography.x509.verification import CertificateRevocationListVerificationContext, InvalidCertificateRevocationList
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates

from OpenSSL import crypto
from OpenSSL import SSL

import certifi
import ssl

from waptutils import BaseObjectClass,wgets,jsondump,ensure_unicode,ensure_list,isodate2datetime


logger = logging.getLogger()

class EWaptCryptoException(Exception):
    pass

class SSLVerifyException(EWaptCryptoException):
    pass

class EWaptEmptyPassword(EWaptCryptoException):
    pass

class EWaptMissingPrivateKey(EWaptCryptoException):
    pass

class EWaptMissingCertificate(EWaptCryptoException):
    pass

class EWaptBadCertificate(EWaptCryptoException):
    pass

class EWaptCertificateBadSignature(EWaptBadCertificate):
    pass

class EWaptCertificateUnknownIssuer(EWaptBadCertificate):
    pass

class EWaptCertificateUntrustedIssuer(EWaptBadCertificate):
    pass

class EWaptCertificateExpired(EWaptBadCertificate):
    pass

class EWaptCertificateRevoked(EWaptBadCertificate):
    pass

class EWaptBadKeyPassword(EWaptCryptoException):
    pass

def check_key_password(key_filename,password=None):
    """Check if provided password is valid to read the PEM private key

    Args:
        password (str): or None if key is not encrypted.

    """
    try:
        if isinstance(password,unicode):
            password = password.encode('utf8')
        with open(key_filename,'rb') as key_pem:
            serialization.load_pem_private_key(key_pem.read(),password or None,default_backend())
    except (TypeError,ValueError) as e:
        return False
    return True


def is_pem_key_encrypted(pem_filename):
    if pem_filename and os.path.isfile(pem_filename):
        pem_content = open(pem_filename,'r').read()
        return 'PRIVATE KEY' in pem_content and 'ENCRYPTED' in pem_content
    else:
        return False


def read_in_chunks(f, chunk_size=1024*128):
    """Lazy function (generator) to read a file piece by piece.
    Default chunk size: 128k."""
    while True:
        data = f.read(chunk_size)
        if not data:
            break
        yield data


def hexdigest_for_file(fname, block_size=2**20,md='sha256'):
    digest = hashlib.new(md)
    with open(fname,'rb') as f:
        while True:
            data = f.read(block_size)
            if not data:
                break
            digest.update(data)
        return digest.hexdigest()

def hash_for_file(fname, block_size=2**20,md='sha256'):
    digest = hashlib.new(md)
    with open(fname,'rb') as f:
        while True:
            data = f.read(block_size)
            if not data:
                break
            digest.update(data)
        return digest.hexdigest()

def sha1_for_file(fname, block_size=2**20):
    return hexdigest_for_file(fname, block_size=2**20,md='sha1')

def sha256_for_file(fname, block_size=2**20):
    return hexdigest_for_file(fname, block_size=2**20,md='sha256')

def hexdigest_for_data(data,md='sha256'):
    digest = hashlib.new(md)
    assert(isinstance(data,str))
    digest.update(data)
    return digest.hexdigest()

def sha256_for_data(data):
    return hexdigest_for_data(data,md='sha256')

def sha1_for_data(data):
    return hexdigest_for_data(data,md='sha1')

def serialize_content_for_signature(content,pre_py3=False):
    result = content
    if isinstance(result,unicode):
        result = result.encode('utf8')
    elif isinstance(result,(list,dict)):
        if pre_py3:
            result = jsondump(result)
        else:
            result = jsondump(result,sort_keys=True,separators=(',', ':'))
    return result


def default_pwd_callback(*args):
    """Default password callback for opening private keys.
    """
    import getpass
    print('Please type the password to decrypt the private key %s' % (args and args[0] or '',))
    pwd = getpass.getpass().encode('ascii')
    if pwd:
        return pwd
    else:
        return None

def NOPASSWORD_CALLBACK(*args):
    pass


def get_hash_algo(md='sha256'):
    return  {'sha1':hashes.SHA1(),
             'sha256':hashes.SHA256(),
            }.get(md,hashes.SHA256())

class SSLCABundle(BaseObjectClass):
    BEGIN_KEY = '-----BEGIN ENCRYPTED PRIVATE KEY-----'
    END_KEY = '-----END ENCRYPTED PRIVATE KEY-----'
    BEGIN_CERTIFICATE = '-----BEGIN CERTIFICATE-----'
    END_CERTIFICATE = '-----END CERTIFICATE-----'
    BEGIN_CRL = '-----BEGIN X509 CRL-----'
    END_CRL = '-----END X509 CRL-----'

    md = 'sha256'

    def __init__(self,cert_pattern_or_dir=None,callback=None,certificates=None):
        """Handle certificates checks giving a list of trusted certificates.
        Can load and save PEM encoded CA certificates from directory and from supplied certificates list.

        Args:
            cert_pattern_or_dir (str): Loads CA trusted certs from here. Path to a directory or files pattern like c:/wapt/ssl/*.crt
            callback (func):  callback to decrypt keys in supplied PEM.
            certificates (list) : list of *trusted* SSLCertificate to include.

        """
        self._keys = []
        self._certificates = []
        self._certs_subject_hash_idx = {}
        self._certs_fingerprint_idx = {}
        self.crls = []
        # store url : last failed http get datetime
        self._crls_negative_cache = {}
        # store check certificate chain check result with expiration
        self._cert_chains_cache = {}
        self.check_cache_ttl = 10 # minutes

        if callback is None:
            callback = default_pwd_callback
        self.callback = callback
        if cert_pattern_or_dir is not None:
            self.add_pems(cert_pattern_or_dir,load_keys=False)
        if certificates is not None:
            self.add_certificates(certificates)


    def clear(self):
        del self._keys[:]
        del self._certificates[:]
        self._certs_subject_hash_idx.clear()
        self._certs_fingerprint_idx.clear()
        del self.crls[:]
        self._crls_negative_cache.clear()
        self._cert_chains_cache.clear()

    def add_pems(self,cert_pattern_or_dir=u'*.crt',load_keys=False):
        if cert_pattern_or_dir:
            if os.path.isdir(cert_pattern_or_dir):
                # load pems from provided directory
                for fn in glob.glob(os.path.join(cert_pattern_or_dir,u'*.crt'))+glob.glob(os.path.join(cert_pattern_or_dir,u'*.pem')):
                    self.add_certificates_from_pem(pem_filename = fn,load_keys=load_keys)
            else:
                # load pems based on file wildcards
                for fn in glob.glob(cert_pattern_or_dir):
                    self.add_certificates_from_pem(pem_filename = fn,load_keys=load_keys)
        return self

    def add_certificates(self,certificates):
        """Add a list of certificates to the bundle and index them.

        Args:
            certificates (list): certificates (SSLCertificate instances) to add to the bundle.

        Returns:
            list of SSLCertificates actually added
        """
        self._cert_chains_cache.clear()
        if not isinstance(certificates,list):
            certificates = [certificates]
        result = []
        for cert in certificates:
            try:
                if not cert.fingerprint in self._certs_fingerprint_idx:
                    self._certs_subject_hash_idx[cert.subject_hash] = cert
                    self._certs_fingerprint_idx[cert.fingerprint] = cert
                    self._certificates.append(cert)
                    result.append(cert)
            except Exception as e:
                logger.warning(u'Error adding certificate %s: %s' % (cert.subject,e))
        return result

    def add_certificates_from_pem(self,pem_data=None,load_keys=False,pem_filename=None):
        """Parse a PEM encoded bundle with multiple certificates, CRL and keys.
        If key needs to be decrypted, password callback property must be assigned.

        Returns:
            SSLCABundle : self
        """
        if pem_data is None:
            if os.path.isfile(pem_filename):
                pem_data = open(pem_filename,'rb').read()
            else:
                raise EWaptCryptoException(u'PEM file %s does not exist'%pem_filename)

        lines = pem_data.splitlines()
        inkey = False
        incert = False
        incrl = False
        tmplines = []
        result = []
        keys = []
        crls = []

        for line in lines:
            if line == self.BEGIN_CERTIFICATE:
                tmplines = [line]
                incert = True
            elif line == self.END_CERTIFICATE:
                tmplines.append(line)
                cert = SSLCertificate(crt_string = str('\n'.join(tmplines)))
                cert._public_cert_filename = pem_filename
                result.append(cert)
                incert = False
                tmplines = []
            elif line == self.BEGIN_CRL:
                tmplines = [line]
                incrl = True
            elif line == self.END_CRL:
                tmplines.append(line)
                crl = SSLCRL (pem_data = str('\n'.join(tmplines)))
                crl.filename = pem_filename
                crls.append(crl)
                incrl = False
                tmplines = []
            elif line == self.BEGIN_KEY:
                tmplines = [line]
                inkey = True
            elif line == self.END_KEY:
                tmplines.append(line)
                if load_keys:
                    key_pem_data = str('\n'.join(tmplines))
                    key = SSLPrivateKey(pem_data = key_pem_data,callback=self.callback)
                    key.private_key_filename = pem_filename
                    keys.append(key)
                inkey = False
                tmplines = []
            else:
                if inkey or incert or incrl:
                    tmplines.append(line)

        self.add_certificates(result)

        for crl in crls:
            self.add_crl(crl)

        self._keys.extend(keys)

        return self

    def key(self,modulus,password):
        for k in self._keys:
            if k.modulus == modulus:
                return k
        return None

    def certificate(self,fingerprint):
        """Returns the certificate matching the supplied sha256 fingerprint

        Args:
            fingerprint (str): hex encoded sha256 certificate fingerprint to lookup

        Returns:
            SSLCertificate
        """
        if not isinstance(fingerprint,(str,unicode)):
            raise EWaptCryptoException(u'A certificate fingerprint as bytes str is expected, %s supplied' % fingerprint)
        return self._certs_fingerprint_idx.get(fingerprint,None)

    def certificate_for_cn(self,cn):
        """Handles wildcards cn..."""
        for cert in self._certificates:
            if (cert.cn == cn) or (cn and cert.cn and glob.fnmatch.fnmatch(cn,cert.cn)):
                return cert
        return None

    def certificate_for_subject_key_identifier(self,subject_key_identifier):
        for cert in self._certificates:
            if (cert.subject_key_identifier == subject_key_identifier):
                return cert
        return None

    def certificate_for_subject_hash(self,subject_hash):
        return self._certs_subject_hash_idx.get(subject_hash,None)

    def keys(self):
        return self._keys

    def certificates(self,valid_only=False):
        return [crt for crt in self._certificates if not valid_only or crt.is_valid()]

    def matching_certs(self,key,ca=None,code_signing=None,valid=True):
        return [
            crt for crt in self._certificates if
                (valid is None or crt.is_valid() == valid) and
                (code_signing is None or crt.is_code_signing == code_signing) and
                (ca is None or crt.is_ca == ca) and
                crt.match_key(key)
                ]

    def certificate_chain(self,certificate=None,fingerprint=None):
        """return certificates chain from certificate, without checking certificate signatures and validity

        Returns:
            list: list of certificates starting with leaf up to root CA.
        """
        result = []
        if not certificate and fingerprint:
            certificate = self.certificate(fingerprint = fingerprint)

        if not certificate:
            raise EWaptCryptoException('certificate_chain: certificate not found')

        issuer_cert = self.issuer_cert_for(certificate)
        # we include the certificate in the chain if it is itself in the cabundle evane if we have not found the issuer
        if issuer_cert or self.certificate(fingerprint = fingerprint):
            result.append(certificate)
        while issuer_cert:
            # TODO : verify  certificate.signature with issuercert public key
            if issuer_cert and not issuer_cert.is_ca:
                logger.debug(u'Certificate %s issued by non CA certificate %s' % (certificate,issuer_cert))
                break
            result.append(issuer_cert)

            # halt on top self signed certificate
            if issuer_cert.subject_hash == issuer_cert.issuer_subject_hash:
                break
            issuer_cert = self.issuer_cert_for(issuer_cert)
        return result

    def is_known_issuer(self,certificate,include_self=True):
        """Check if certificate is issued by one of this certificate bundle CA
        and check certificate signature. Return top most CA.

        Top most CA should be trusted somewhere...

        Args:
            certificate: certificate to check
            include_self: if certificate is in bunclde, accept it (pining)

        Return:
            SSLCertificate: issuer certificate or None
        """
        if include_self and isinstance(certificate,SSLCertificate) and certificate.fingerprint in self._certs_fingerprint_idx:
            return certificate

        cert_chain  = certificate.verify_signature_with(self)
        if cert_chain:
            return cert_chain[-1]
        else:
            return None


    def is_valid_certificate(self,certificate,check_revoke=True):
        """Check if certificate valid using ssl store context

        Args:
            certificate: certificate to check
            include_self: if certificate is in bundle, accept it (pining)

        Returns:
            SSLCertificate: issuer certificate or None
        """
        flags = (crypto.X509StoreFlags.CB_ISSUER_CHECK |
            crypto.X509StoreFlags.CHECK_SS_SIGNATURE
            )

        if check_revoke and certificate.crl_urls():
            flags = flags | crypto.X509StoreFlags.CRL_CHECK

        store = crypto.X509Store()
        store.set_flags(flags)
        for cert in self._certificates:
            if cert.is_valid():
                store.add_cert(cert.as_X509())

        for crl in self.crls:
            crlcert = crypto.load_crl(crypto.FILETYPE_ASN1,crl.as_der())
            store.add_crl(crlcert)

        store_ctx = crypto.X509StoreContext(store,cert.as_X509())
        try:
            verify =  store_ctx.verify_certificate()
            return True
        except crypto.X509StoreContextError as e:
            logger.critical(u'Error for certificate %s. Faulty certificate is %s: %s' % (certificate,e.certificate.get_subject(),e))
            raise

    def check_certificates_chain(self,cert_chain,verify_expiry=True,verify_revoke=True,allow_pinned=True):
        """Check if first certificate in cert_chain is approved
        by one of the CA certificate from this bundle.

        If intermediate issuers can not be found in this ca bundle, try to get them from
        supplied cert_chain.

        Args:
            cert_chain (list) : list of certificates. first one is starting point. The other ones are used if None can be found in cabundle
            verify_expiry (bool) : Check if certificates expiry dates are okay relative to today.
            verify_revoke (bool) : Check if certificate is not in the CRLs (if certificate contains crl location URL)
                                     CRL must have been already retrieved using update_crl.
            allow_pinned (bool) : If True, accept certificate if it is in trusted certificates, even if we don't know the issuer.

        Returns:
            (list) : SSLCertificate chain of trusted cert
        """
        def check_cert(cert):
            if verify_expiry and not cert.is_valid():
                raise EWaptCertificateExpired(u'Certificate %s is expired' % cert)
            if verify_revoke:
                self.check_if_revoked(cert)
            return cert

        def add_chain_cache(cache_key,chain,reason):
            logger.debug('Stores cert chain check in cache')
            self._cert_chains_cache[cache_key] = (time.time() + self.check_cache_ttl * 60,chain,reason)

        if isinstance(cert_chain,SSLCABundle):
            cert_chain = cert_chain._certificates
        if isinstance(cert_chain,SSLCertificate):
            cert_chain = [cert_chain]
        if not cert_chain:
            raise Exception('No certificates to check')

        cert = cert_chain[0]

        # try to get a cached result
        cache_key = (cert.fingerprint,verify_expiry,verify_revoke,allow_pinned)
        (cache_expiration_date,cached_chain,reason) = self._cert_chains_cache.get(cache_key,(None,None,None))
        if not cache_expiration_date or cache_expiration_date < time.time():
            # build an index of certificates in chain for intermediates CA
            idx = dict([crt.subject_key_identifier,crt] for crt in cert_chain)
            check_cert(cert)
            result= [cert]
            while cert:
                try:
                    # trust the cert itself if it is the bundle, even if issuer is unknown at this stage.
                    if allow_pinned and cert in self._certificates:
                        reason = u'Certificate "%s" is trusted by himself' % cert.cn
                        add_chain_cache(cache_key,result,reason)
                        return result

                    # append chain of trusted upstream CA certificates
                    issuer_chain = cert.verify_signature_with(self)
                    for issuer in issuer_chain:
                        if allow_pinned and issuer in self._certificates:
                            result.append(issuer)
                            break
                        issuer.verify_signature_with(self)
                        result.append(issuer)

                    reason = u'Certificate "%s" is trusted' % cert.cn
                    add_chain_cache(cache_key,result,reason)
                    return result
                except SSLVerifyException as e:
                    # try to use intermediate from supplied list
                    issuer = idx.get(cert.authority_key_identifier,None)
                    reason = u'None of certificates (%s) are trusted.' % (','.join(['"%s"' % c.cn for c in cert_chain]))
                    if not issuer:
                        add_chain_cache(cache_key,[],reason)
                        raise EWaptCertificateUnknownIssuer(reason)

                    if issuer == cert:
                        add_chain_cache(cache_key,[],reason)
                        raise EWaptCertificateUnknownIssuer(reason)

                    if cert.verify_signature_with(issuer):
                        check_cert(issuer)
                        if cert != issuer:
                            result.append(issuer)
                            cert = issuer

        # return cached checked chain
        elif cached_chain:
            return cached_chain

        #reason = u'None of certificates (%s) are trusted.' % (','.join(['"%s"' % c.cn for c in cert_chain]))
        # store negative caching
        if cached_chain is None:
            add_chain_cache(cache_key,[],reason)

        raise EWaptCertificateUntrustedIssuer(reason)

    def add_crl(self,crl):
        """Replace or Add pem encoded CRL"""
        self._cert_chains_cache.clear()
        oldcrl = self.crl_for_authority_key_identifier(crl.authority_key_identifier)
        if oldcrl is None:
            # check with alternative method
            oldcrl = self.crl_for_issuer_subject_hash(crl.issuer_subject_hash)

        if (oldcrl and crl > oldcrl) or not oldcrl:
            if oldcrl:
                self.crls.remove(oldcrl)
            self.crls.append(crl)

    def crl_for_authority_key_identifier(self,authority_key_identifier):
        for crl in self.crls:
            if crl.authority_key_identifier == authority_key_identifier:
                return crl
        return None

    def crl_for_issuer_subject_hash(self,issuer_subject_hash):
        for crl in self.crls:
            if crl.issuer_subject_hash == issuer_subject_hash:
                return crl
        return None

    def download_issuer_certs(self,force=False,for_certificates=None):
        """Download and add CA certs using authorityInfoAccess access_location
        No check is attempted on cert signatures.

        Returns:
            list: of missing downloaded SSLCertificates

        """
        result = []
        if for_certificates is None:
            for_certificates = self._certificates
        if isinstance(for_certificates,SSLCertificate):
            for_certificates = [for_certificates]

        for cert in for_certificates:
            issuer_cert = self.issuer_cert_for(cert)
            if not issuer_cert:
                issuer_urls = cert.issuer_cert_urls()
                for url in issuer_urls:
                    try:
                        logger.debug(u'Download certificate %s' % (url,))
                        cert_data = wgets(url,timeout=(0.3,2.0))
                        issuer_cert = SSLCertificate(crt_string = cert_data)
                        self.add_certificates(issuer_cert)
                        result.append(issuer_cert)
                        if self.issuer_cert_for(issuer_cert) is None:
                            result.extend(self.download_issuer_certs(force=False,for_certificates=issuer_cert))
                        break
                    except Exception as e:
                        logger.warning(u'Unable to download certificate from %s: %s' % (url,repr(e)))
                        pass
        return result

    def issuer_cert_for(self,certificate):
        return self.certificate_for_subject_key_identifier(certificate.authority_key_identifier) or self.certificate_for_subject_hash(certificate.issuer_subject_hash)


    def update_crl(self,force=False,for_certificates=None,cache_dir=None,timeout=2.0,proxies=None):
        """Download and update all crls for certificates in this bundle or
        for certificates in for_certificates list

        Returns:
            list: list of downloaded / updated CRL
        """
        # TODO : to be moved to an abstracted wapt https client
        result = []
        if for_certificates is None:
            for_certificates = self._certificates
        if isinstance(for_certificates,SSLCertificate):
            for_certificates = [ for_certificates ]

        for cert in for_certificates:
            crl_urls = cert.crl_urls()
            for url in crl_urls:
                ssl_crl = self.crl_for_authority_key_identifier(cert.authority_key_identifier)
                if ssl_crl is None:
                    # check with alternative method
                    ssl_crl = self.crl_for_issuer_subject_hash(cert.issuer_subject_hash)

                if force or not ssl_crl or ssl_crl.next_update < datetime.datetime.utcnow():
                    try:
                        if not force:
                            self._check_url_in_negative_cache(url)
                        logger.debug(u'Download CRL %s' % (url,))
                        if cache_dir:
                            crl_filename =  os.path.join(cache_dir,urlparse.urlparse(url).path.split('/')[-1])
                        else:
                            crl_filename = None

                        # try to find CRL in cache dir
                        crl_data = None
                        if cache_dir and os.path.isfile(crl_filename):
                            try:
                                crl_data = open(crl_filename,'rb').read()
                                ssl_crl = SSLCRL(der_data = crl_data)
                            except Exception as e:
                                crl_data = None
                                ssl_crl = None

                        # get it from remote location
                        if not crl_data:
                            crl_data = wgets(url,timeout=timeout,proxies=proxies)
                        try:
                            ssl_crl = SSLCRL(der_data = crl_data)
                        except Exception as e:
                            logger.debug('trying PEM format...')
                            ssl_crl = SSLCRL(pem_data = crl_data)

                        ssl_crl.verify_signature_with(self)

                        self.add_crl(ssl_crl)
                        result.append(ssl_crl)
                    except Exception as e:
                        self._crls_negative_cache[url] = datetime.datetime.utcnow()
                        logger.warning(u'Unable to download CRL from %s: %s' % (url,repr(e)))
                        pass
                elif ssl_crl:
                    logger.debug(u'CRL %s does not yet need to be refreshed from location %s' % (ssl_crl,url))
        return result

    def _check_url_in_negative_cache(self,url):
        last = self._crls_negative_cache.get(url,None)
        if last:
            if datetime.datetime.utcnow() - last < datetime.timedelta(hours = 1):
                raise Exception('Url in negative cache')
            else:
                del self._crls_negative_cache[url]

    def check_if_revoked(self,cert):
        """Raise exception if certificate has been revoked before now"""
        crl = self.crl_for_authority_key_identifier(cert.authority_key_identifier)
        if crl is None:
            # check with alternative method
            crl = self.crl_for_issuer_subject_hash(cert.issuer_subject_hash)
        if crl:
            if crl.next_update < datetime.datetime.utcnow():
                raise Exception(u'CRL is too old, revoke test failed for %s'% cert)
            revoked_on = crl.is_revoked(cert)
            if revoked_on and revoked_on < datetime.datetime.utcnow():
                raise EWaptCertificateRevoked(u'Certificate %s has been revoked on %s' % (cert.cn,revoked_on))
        else:
            return False

    def as_pem(self,with_keys=True,password=None):
        if isinstance(password,unicode):
            password = password.encode('utf8')
        # reorder by longest path to have leaf first
        roots = [crt for crt in self._certificates]
        return " \n".join([key.as_pem(password=password) for key in self._keys]) + \
                " \n".join(["# CN: %s\n# Issuer CN: %s\n%s" % (crt.cn,crt.issuer_cn,crt.as_pem()) for crt in reversed(self._certificates)]) + \
                " \n".join(["# CRL Issuer CN: %s\n%s" % (crl.issuer_cn,crl.as_pem()) for crl in self.crls])


    def save_as_pem(self,filename,with_keys=True,password=None):
        """Save the RSA  private key as a PEM encoded file

        Optionnally, encrypt the key with a password.

        Args:
            filename (str) : filename of pem file to create. If not provided
                             use the filename from self.
            password (str) : password. If None, don't encrypt the key.
                             if password is unicode, it is encoded in utf8 first.

        """
        # get before opening file to be sure to not overwrite a file if pem data can not decrypted...

        pem_data = self.as_pem(with_keys=with_keys,password=password)
        with open(filename,'wb') as f:
            f.write(pem_data)


    def __repr__(self):
        if len(self._certificates)<20:
            return "<SSLCABundle %s crls:%s>" % (repr(self._certificates),self.crls)
        else:
            return "<SSLCABundle %s certificates, %s crls>" % (len(self._certificates),len(self.crls))


    def __add__(self,otherbundle):
        return SSLCABundle(certificates = self._certificates+otherbundle._certificates)

    def __substract__(self,otherbundle):
        certificates = self._certificates
        for cert in otherbundle._certificates:
            if not cert.fingerprint in self._certs_fingerprint_idx:
                certificates.append(cert)
        return SSLCABundle(certificates=certificates)

    def certificates_sha256_fingerprints(self):
        """Returns csv of sha256 fingerprints

        Returns:
            str
        """
        return ','.join([cert.fingerprint for cert in self.certificates()])


def get_peer_cert_chain_from_server(url):
    """Returns list of SSLCertificates from initial handshake of https server
    Add certificates to current SSLCAchain
    First certificate is certificate for URL's FQDN, next are intermediate ones.

    """
    def verify_cb(conn, cert, errnum, depth, ok):
        return ok
    url = str(url)
    location = urlparse.urlparse(url)
    client_ctx = SSL.Context(SSL.SSLv23_METHOD)
    client_ctx.set_verify(SSL.VERIFY_NONE, verify_cb)
    client = SSL.Connection(client_ctx, SSL.socket.socket())
    client.set_connect_state()
    # for SNI
    client.set_tlsext_host_name(location.hostname)
    client.connect((location.hostname,location.port or 443))
    client.do_handshake()
    result = []
    chain = client.get_peer_cert_chain()
    for cert in chain:
        pem_data = crypto.dump_certificate(crypto.FILETYPE_PEM,cert)
        result.append(SSLCertificate(crt_string=pem_data))
    return result

def get_pem_server_certificate(url,save_to_file=None):
    """Retrieve single certificate from ssl server for further checks

    Returns:
        str: pem encoded data
    """
    url = str(url)
    url = urlparse.urlparse(url)
    if url.scheme == 'https':
        # try a connection to get server certificate
        pem_data = str(ssl.get_server_certificate((url.hostname, url.port or 443)))
        if save_to_file:
            open(save_to_file,'wb').write(pem_data)
        return pem_data
    else:
        return None

def get_cert_chain_as_pem(certificates_chain):
    """Build a x509 encoded PEM string from a list of certificates

    Args:
        certificates_chain (list) : list of SSLCertificates
    Returns
        str: x509 pem encoded (utf8)
    """
    if certificates_chain is None:
        return None
    return (u" \n".join(["# CN: %s\n# Issuer CN: %s\n%s" % (crt.cn,crt.issuer_cn,crt.as_pem()) for crt in certificates_chain])).encode('utf-8')


class SSLPrivateKey(BaseObjectClass):
    def __init__(self,filename=None,pem_data=None,callback=None,password = None,rsa=None):
        """Args:
            filename (str) : Filename Path to PEM encoded Private Key
            pem_data (str)
            callback (func) : Called to provide password for the key if needed.
                              If password is set (not None), this parameter is ignored
                              else if None, default is default_pwd_callback.
            password (str) : passpharse to decrypt private key.
                             If '', no decryption and no password is asked. RSA key loadind will fail.
            rsa (cryptography.hazmat.backends.openssl.rsa._RSAPrivateKey)

        """
        self.private_key_filename = filename
        if password == '':
            callback = NOPASSWORD_CALLBACK
        else:
            if password is None and callback is None:
                callback = default_pwd_callback
        self.password_callback = callback
        if isinstance(password,unicode):
            password = password.encode('utf8')
        self._rsa = rsa
        self.pem_data = pem_data
        if not self.pem_data and self.private_key_filename and os.path.isfile(self.private_key_filename):
            self._load_pem_data_from_file()
        # decrypt immediately if possible...
        if self.pem_data and (not self._is_encrypted or password is not None):
            self.load_key_data(self.pem_data,password)

    def create(self,bits=2048):
        """Create a RSA key pair"""
        self._rsa = rsa.generate_private_key(
            public_exponent=65537,
            key_size=bits,
            backend=default_backend())
        return self

    def _is_encrypted(self):
        return 'ENCRYPTED' in self.pem_data

    def as_pem(self,password=None):
        """Return private key as a PEM str

        Args;
            password (str): password to use to encrypt the key.

        Returns:
            str: pem encoded RSA Private key.
        """
        if isinstance(password,unicode):
            password = password.encode('utf8')

        if password is not None:
            enc = serialization.BestAvailableEncryption(password)
        else:
            enc = serialization.NoEncryption()
        pem = self.rsa.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=enc,
        )
        return pem

    def save_as_pem(self,filename=None,password=None):
        """Save the RSA  private key as a PEM encoded file

        Optionnaly, encypt the key with a password.

        Args:
            filename (str) : filename of pem file to create. If not provided
                             use the filename from self.
            password (str) : password. If None, don't encrypt the key.
                             if password is unicode, it is encoded in utf8 first.

        """
        if filename is None:
            filename = self.private_key_filename
        if isinstance(password,unicode):
            password = password.encode('utf8')
        # get before opening file to be sure to not overwrite a file if pem data can not decrypted...

        pem_data = self.as_pem(password=password)
        with open(filename,'wb') as f:
            f.write(pem_data)
        self.private_key_filename = filename

    def load_key_data(self,pem_data,password=None):
        """Load RSA structure with the provided pem_data


        Args;
            pem_data (str) : base64 PEM style encoded RSA private key
            password (str) : try with this password first. If dails to decrypt, use password_callback if provided
        Returns:
            None

        """
        retry_cnt=3
        while retry_cnt>0:
            try:
                self._rsa = serialization.load_pem_private_key(
                    str(pem_data),
                    password = password,
                    backend = default_backend())
                break
            except (TypeError,ValueError) as e:
                if "Password was not given but private key is encrypted" in e.message or\
                        "Bad decrypt. Incorrect key passphrase ?" in e.message and self.password_callback is not None:
                    retry_cnt -= 1
                    password = self.password_callback(self.private_key_filename)
                    if password == '':
                        password = None
                    if isinstance(password,unicode):
                        password = password.encode('utf8')
                else:
                    raise

    def _load_pem_data_from_file(self):
        with open(self.private_key_filename,'rb') as pem_file:
            self.pem_data = pem_file.read()


    @property
    def rsa(self):
        """access to RSA keys

        >>> key = SSLPrivateKey('c:/private/tranquilit2.pem')
        >>> key.rsa
        Please type the password to decrypt the private key
        <cryptography.hazmat.backends.openssl.rsa._RSAPrivateKey object at 0x040ECE70>
        >>> key.rsa.public_key()
        <bound method _RSAPrivateKey.public_key of <cryptography.hazmat.backends.openssl.rsa._RSAPrivateKey object at 0x040ECE70>>
        >>> key.rsa.private_bytes()
        <bound method _RSAPrivateKey.private_bytes of <cryptography.hazmat.backends.openssl.rsa._RSAPrivateKey object at 0x040ECE70>>

        """
        if not self._rsa:
            if not self.pem_data and self.private_key_filename and os.path.isfile(self.private_key_filename):
                self._load_pem_data_from_file()
            self.load_key_data(self.pem_data)
        if not self._rsa:
            raise EWaptEmptyPassword(u'Unable to load key %s'%self.private_key_filename)
        return self._rsa

    def sign_content(self,content,md='sha256',block_size=2**20,pre_py3=True):
        """ Sign content with the private_key, return the signature

        If content is not a raw string, it is first encoded in json or utf8

        Args:
            content (str, list or dict): content to sign
            md (str): lessage digest type to use
            clock_size (int) : unused
            pre_py3 (bool) : if True serialization is not compatible with python3
                                (keys are sorted in the undeterministic python2 order
                                    and there are spaces in json seperators)

        Returns:
            bytes: signature
        """
        #apadding = padding.PSS(
        #                mgf=padding.MGF1(hashes.SHA256()),
        #                salt_length=padding.PSS.MAX_LENGTH)
        apadding = padding.PKCS1v15()
        algo = get_hash_algo(md)
        content = serialize_content_for_signature(content,pre_py3=pre_py3)
        if not isinstance(content,str):
            raise Exception(u'Bad content type for sign_content, should be str')
        signature = self.rsa.sign(content,apadding,algo)
        return signature

    def match_cert(self,crt):
        """Check if provided public certificate matches the current private key"""
        if not isinstance(crt,SSLCertificate):
            crt = SSLCertificate(crt)
        return crt.modulus == self.modulus


    def matching_certs(self,cert_dir=None,ca=None,code_signing=None,valid=None):
        """Returns list of certificates in cert_dir with public key matching this
        private_key.

        Args:
            cert_dir (str): directory path where to search for .crt, .der or .pem X509 certificates files
            ca (bool): if not None, returns only certificates which ahev the CA constraint set or not
            code_signing (bool) : if not None, return only certificates which have the code_signing attribute or not
            valid (bool) : if True, return only certificates which are calid (not before / not after attibutes check)

        Returns:
            list: list of SSLCertificate matching the key

        """

        if cert_dir is None and self.private_key_filename:
            cert_dir = os.path.dirname(self.private_key_filename)
        result = []
        for fn in glob.glob(os.path.join(cert_dir,'*.crt'))+glob.glob(os.path.join(cert_dir,'*.cer'))+glob.glob(os.path.join(cert_dir,'*.pem')):
            try:
                crt = SSLCertificate(fn)
                if (valid is None or crt.is_valid() == valid) and\
                   (code_signing is None or crt.is_code_signing == code_signing) and\
                   (ca is None or crt.is_ca == ca) and\
                   crt.match_key(self):
                        result.append(crt)
            except (TypeError,ValueError) as e:
                logger.debug(u'Certificate %s can not be read. Skipping. Error was:%s' % (fn,repr(e)))
        return result

    def decrypt(self,content):
        """Decrypt a message encrypted with the public key.
        For small messages only.
        """
        apadding = padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA1()),
            algorithm=hashes.SHA1(),
            label=None)
        return self.rsa.decrypt(content,apadding)

    def decrypt_fernet(self,crypted_data):
        """Decrypt bytes which has been crypted by SSLCertificate.encrypt_fernet
        the fernet symetric key is decrypted using RSA
        payload is decrypted using fernet key

        Args:
            crypted_data (bytes) : starts with 'RSAFERNET', then rsa key length (base10) on 3 chars
                                   then rsa encrypted fernet key, then fernet encrypted data
        Returns:
            bytes : decrypted data
        """
        pos = 0
        head_signature = 'RSAFERNET'
        pos += len(head_signature)
        head = crypted_data[0:pos]
        if head != head_signature:
            raise EWaptCryptoException('Bad encrypted data, header not found')
        key_length = int(crypted_data[pos:pos+3])
        pos += 3
        rsa_symkey = crypted_data[pos:pos+key_length]
        fernet_key = self.decrypt(rsa_symkey)
        pos += key_length
        f = Fernet(fernet_key,default_backend())
        return f.decrypt(crypted_data[pos:])

    @property
    def modulus(self):
        return format(self.rsa.private_numbers().public_numbers.n, "x")

    def as_PKey(self):
        return crypto.PKey().from_cryptography_key(self.rsa)

    def __cmp__(self,key):
        return cmp(self.modulus,key.modulus)

    def __repr__(self):
        return '<SSLPrivateKey %s>' % repr(self.private_key_filename)

    def sign_claim(self,claim,attributes=None,signer_certificate_chain=None):
        """Sign a set of attributes of a dict

        Args:
            claim (dict) : data structure to sign
            attributes (list): list of attributes to include in signature
                               if None, include all attributes.
            certificate (list) : certificate chain of signer

        Returns:
            dict: same as claim with additional signature attributes
                    'signed_attributes','signer','signature_date','signer_certificate'

        """
        if attributes is None:
            attributes = claim.keys()
        if not isinstance(signer_certificate_chain,list):
            signer_certificate_chain = [signer_certificate_chain]

        if not signer_certificate_chain:
            raise EWaptCryptoException('sign_claim: No certificate provided for signature')

        signature_attributes = ['signed_attributes','signer','signature_date','signer_certificate']
        for att in signature_attributes+['signature']:
            if att in attributes:
                attributes.remove(att)

        reclaim = {att:claim.get(att,None) for att in attributes if att not in signature_attributes and att != 'signature'}
        reclaim['signer'] = signer_certificate_chain[0].fingerprint
        reclaim['signature_date'] = datetime.datetime.utcnow().isoformat()
        #if signer_certificate_chain[0].issuer != signer_certificate_chain[0]:
        reclaim['signer_certificate'] = '\n'.join(cert.as_pem() for cert in signer_certificate_chain)
        #else:
        #    # avoid passing something we know already as we will check
        #    reclaim['signer_certificate'] = ''

        reclaim['signed_attributes'] = attributes+signature_attributes
        signature = base64.b64encode(self.sign_content(reclaim))
        reclaim['signature'] = signature
        return reclaim


    def build_sign_certificate(self,
            ca_signing_key=None,
            ca_signing_cert=None,
            cn=None,
            organizational_unit=None,
            organization=None,
            locality=None,
            country=None,
            dnsname=None,
            email=None,
            is_ca=None,
            is_code_signing=None,
            is_client_auth=None,
            key_usages=['digital_signature','content_commitment','key_cert_sign','data_encipherment'],
            crl_url = None,
            issuer_cert_url = None ):
        """Build a certificate with self public key and supplied attributes,
        and sign it with supplied ca_signing_key.

        To self sign the certificate, put None for ca_signing_key and ca_signing_cert

        Args:
            ca_signing_key (SSLPrivateKey):
            ca_signing_cert (SSLCertificate):

            is_ca (bool) : certificate is a CA root or intermediate or self-signed
                           if None, default to True is ca_signing_cert is None
            is_code_signing (bool): subject can sign code
                           if None, default to (not is_ca)
            dnsname (str): Witll be added as an DNS SubjectAlternativeName.
            key_usages (list of str) : list of certificate / key usage targets.

        Returns:
            self
        """

        if is_ca is None:
            is_ca = ca_signing_cert is None

        if is_code_signing is None:
            is_code_signing = not is_ca

        map = [
            [x509.NameOID.COUNTRY_NAME,country or None],
            [x509.NameOID.LOCALITY_NAME,locality or None],
            [x509.NameOID.ORGANIZATION_NAME,organization or None],
            [x509.NameOID.COMMON_NAME,cn or None],
            [x509.NameOID.EMAIL_ADDRESS,email or None],
            [x509.NameOID.ORGANIZATIONAL_UNIT_NAME,organizational_unit or None],
            ]
        att = []
        for (oid,value) in map:
            if value is not None:
                att.append(x509.NameAttribute(oid,ensure_unicode(value)))

        subject = x509.Name(att)

        extensions = []

        extensions.append(dict(
            extension=x509.BasicConstraints(ca=is_ca,path_length=None),
            critical=True))

        if is_ca and not 'crl_sign' in key_usages:
            key_usages.append('crl_sign')

        extended_key_usage = []
        if is_code_signing:
            extended_key_usage.append(x509.OID_CODE_SIGNING)
        if is_client_auth:
            extended_key_usage.append(x509.OID_CLIENT_AUTH)
        if extended_key_usage:
            extensions.append(dict(
                extension=x509.ExtendedKeyUsage(extended_key_usage),
                critical=True))

        extensions.append(dict(
                    extension=x509.SubjectKeyIdentifier.from_public_key(self.public_key()),
                    critical = False))


        if dnsname is not None:
            extensions.append(dict(
                    extension=x509.SubjectAlternativeName([x509.DNSName(ensure_unicode(dnsname))]),
                    critical=False))

        if issuer_cert_url is not None:
            extensions.append(dict(
                    extension=x509.AuthorityInformationAccess([
                         AccessDescription(
                            access_method = x509.AuthorityInformationAccessOID.CA_ISSUERS,
                            access_location = x509.UniformResourceIdentifier(ensure_unicode(issuer_cert_url)))]),
                    critical=True))

        if crl_url is not None and ca_signing_cert is not None:
            extensions.append(dict(
                    extension=x509.CRLDistributionPoints([
                        DistributionPoint(
                            full_name = [x509.UniformResourceIdentifier(ensure_unicode(crl_url))],
                            crl_issuer = None,
                            relative_name = None, reasons = None)]),
                    critical=True))

        for key_usage in key_usages:
            kwargs = {}
            for key in [ 'content_commitment','crl_sign','data_encipherment','decipher_only',
                        'digital_signature', 'encipher_only', 'key_agreement', 'key_cert_sign',
                        'key_encipherment']:
                kwargs[key] = key in key_usages

        extensions.append(dict(
                extension=x509.KeyUsage(**kwargs),
                critical=True))

        public_key = self.public_key()

        if not isinstance(public_key,rsa.RSAPublicKey):
            raise TypeError('public_key must be an instance of rsa.RSAPublicKey')

        serial_number = x509.random_serial_number()

        if ca_signing_key is None:
            ca_signing_key = self
            ca_signing_cert = None

        if ca_signing_cert is None:
            # self signed or root certificate
            issuer = subject
        else:
            issuer = ca_signing_cert.crt.subject
            extensions.append(
                dict(extension=x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                    ca_signing_cert.crt.extensions.get_extension_for_oid(x509.OID_SUBJECT_KEY_IDENTIFIER)),
                critical=False))

        builder = x509.CertificateBuilder().serial_number(
            serial_number
        ).issuer_name(
            issuer
        ).subject_name(
            subject
        ).public_key(
            public_key
        ).not_valid_before(
            datetime.datetime.utcnow(),
        ).not_valid_after(
            datetime.datetime.utcnow()+datetime.timedelta(days=3650)
        )

        for ext in extensions:
            builder = builder.add_extension(
                ext.get('extension'), ext.get('critical')
            )

        crypto_crt = builder.sign(ca_signing_key.rsa,algorithm=hashes.SHA256(), backend=default_backend())
        return SSLCertificate(crt = crypto_crt)

    def build_csr(self,
            cn=None,
            organizational_unit=None,
            organization=None,
            locality=None,
            country=None,
            dnsname=None,
            email=None,
            is_ca=False,
            is_code_signing=None,
            is_client_auth=None,
            key_usages=['digital_signature','content_commitment','key_cert_sign','data_encipherment'],
            ):
        """Build a certificate signing request with self public key and supplied attributes,

        Args:
            is_ca (bool) : certificate is a CA root or intermediate or self-signed
                           if None, default to True is ca_signing_cert is None
            is_code_signing (bool): subject can sign code
                           if None, default to (not is_ca)
            dnsname (str): Witll be added as an DNS SubjectAlternativeName.
            key_usages (list of str) : list of certificate / key usage targets.

        Returns:
            SSLCertificateSigningRequest
        """

        if is_code_signing is None:
            is_code_signing = not is_ca

        map = [
            [x509.NameOID.COUNTRY_NAME,country or None],
            [x509.NameOID.LOCALITY_NAME,locality or None],
            [x509.NameOID.ORGANIZATION_NAME,organization or None],
            [x509.NameOID.COMMON_NAME,cn or None],
            [x509.NameOID.EMAIL_ADDRESS,email or None],
            [x509.NameOID.ORGANIZATIONAL_UNIT_NAME,organizational_unit or None],
            ]
        att = []
        for (oid,value) in map:
            if value is not None:
                att.append(x509.NameAttribute(oid,ensure_unicode(value)))

        subject = x509.Name(att)

        extensions = []

        extensions.append(dict(
            extension=x509.BasicConstraints(ca=is_ca,path_length=None),
            critical=True))

        if is_ca and not 'crl_sign' in key_usages:
            key_usages.append('crl_sign')

        extended_key_usage = []
        if is_code_signing:
            extended_key_usage.append(x509.OID_CODE_SIGNING)
        if is_client_auth:
            extended_key_usage.append(x509.OID_CLIENT_AUTH)
        if extended_key_usage:
            extensions.append(dict(
                extension=x509.ExtendedKeyUsage(extended_key_usage),
                critical=True))

        extensions.append(dict(
                    extension=x509.SubjectKeyIdentifier.from_public_key(self.public_key()),
                    critical = False))


        if dnsname is not None:
            if isinstance(dnsname,(str,unicode)):
                dnsname = [dnsname]
            extensions.append(dict(
                    extension=x509.SubjectAlternativeName([x509.DNSName(ensure_unicode(name)) for name in dnsname]),
                    critical=False))

        for key_usage in key_usages:
            kwargs = {}
            for key in [ 'content_commitment','crl_sign','data_encipherment','decipher_only',
                        'digital_signature', 'encipher_only', 'key_agreement', 'key_cert_sign',
                        'key_encipherment']:
                kwargs[key] = key in key_usages

        extensions.append(dict(
                extension=x509.KeyUsage(**kwargs),
                critical=True))

        public_key = self.public_key()

        if not isinstance(public_key,rsa.RSAPublicKey):
            raise TypeError('public_key must be an instance of rsa.RSAPublicKey')

        builder = x509.CertificateSigningRequestBuilder(subject_name=subject)
        for ext in extensions:
            builder = builder.add_extension(
                ext.get('extension'), ext.get('critical')
            )

        crypto_csr = builder.sign(self.rsa,algorithm=hashes.SHA256(), backend=default_backend())
        return SSLCertificateSigningRequest(csr=crypto_csr)


    def public_key(self):
        """Return the RSA public key object

        Returns:
            RSAPublicKey
        """

        return self.rsa.public_key()

    def public_key_as_pem(self):
        """Return public key as a PEM str

        Returns:
            str: pem encoded RSA public key.
        """
        pem = self.public_key().public_bytes(encoding=serialization.Encoding.PEM,format=serialization.PublicFormat.PKCS1)
        return pem

    def public_key_as_openssh(self):
        """Return public key as a PEM str

        Returns:
            str: pem encoded RSA public key.
        """
        pem = self.public_key().public_bytes(encoding=serialization.Encoding.OpenSSH,format=serialization.PublicFormat.OpenSSH)
        return pem


class SSLCertificateSigningRequest(BaseObjectClass):
    def __init__(self,csr=None,csr_filename=None,csr_pem_string=None):
        self._rsa = None
        self.csr_filename = csr_filename
        if csr:
            self.csr = csr
        elif csr_pem_string:
            self.csr = x509.load_pem_x509_csr(str(csr_pem_string),default_backend())
        elif csr_filename:
            with open(csr_filename, "rb") as f:
                self.csr = x509.load_pem_x509_csr(f.read(),default_backend())

    def as_pem(self):
        return self.csr.public_bytes(serialization.Encoding.PEM)

    def save_as_pem(self,filename):
        with open(filename, "wb") as f:
            f.write(self.as_pem())

    def is_signature_valid(self):
        return self.csr.is_signature_valid

    @property
    def rsa(self):
        """Return public RSA key"""
        if not self._rsa:
            self._rsa = self.csr.public_key()
        return self._rsa

    @property
    def modulus(self):
        return format(self.rsa.public_numbers().n, "x")

    def _subject_attribute(self,oid):
        att = self.csr.subject.get_attributes_for_oid(oid)
        if att:
            return att[0].value
        else:
            return None

    @property
    def subject_dn(self):
        return self._subject_attribute(x509.NameOID.DN_QUALIFIER)

    @property
    def cn(self):
        return self._subject_attribute(x509.NameOID.COMMON_NAME)

    @property
    def subject(self):
        """Returns subject of the certificate as a Dict"""
        subject = self.csr.subject
        result = {}
        for attribute in subject:
            result[attribute.oid._name]= attribute.value
        return result

    @property
    def subject_key_identifier(self):
        """Identify the certificate by its subject

        Returns:
            bytes

        >>> c.subject_key_identifier
        '\xf2\x99\xd7\xfao\n\xf1\x1e\x03?\xd0\xf2\xff6\xfe\xe8\x8cv\xab\x1a'
        """
        keyid = self.extensions.get('subjectKeyIdentifier',None)
        if keyid:
            return keyid.digest
        else:
            return None

    @property
    def authority_key_identifier(self):
        """Identify the authority by its subject Id

        Returns:
            bytes

        >>> c.authority_key_identifier
        'L\xb3XL\xc3\x91\xc1\xe3hMc\xcec\x0c6\xf2\x9b\x9d\x95\xd0'
        """
        keyid = self.extensions.get('authorityKeyIdentifier',None)
        if keyid:
            return keyid.digest
        else:
            return None

    @property
    def key_usage(self):
        keyusage = self.extensions.get('keyUsage',None)
        if keyusage:
            result = []
            for att in ('digital_signature','content_commitment','key_encipherment',
                'data_encipherment','key_agreement','key_cert_sign','crl_sign','encipher_only','decipher_only'):
                if hasattr(keyusage,att) and getattr(keyusage,att):
                    result.append(att)
            return result
        else:
            return None


    @property
    def subject_alt_names(self):
        """Other names of the subject (in addition to cn)"""
        names = self.extensions.get('subjectAltName',None)
        if names:
            return [n.value for n in names]
        else:
            return None

    def verify_content(self,content,signature,md='sha256',block_size=2**20):
        """Check that the signature matches the content

        Args:
            content (str) : content to check. if not str, the structure will be converted to json first
            signature (str) : ssl signature of the content

        Returns:
            str: subject (CN) of current certificate or raise an exception if no match

        Raises SSLVerifyException
        """
        for pre_py3 in (True,False):
            serialized_content = serialize_content_for_signature(content,pre_py3=pre_py3)

            if not isinstance(content,str):
                raise InvalidSignature('Bad content type for verify_content, should be either str or file like')

            # todo : recommended for new projects...
            #apadding = padding.PSS(
            #    mgf=padding.MGF1(get_hash_algo(md)),
            #    salt_length=padding.PSS.MAX_LENGTH)

            # compatible with openssl sign
            apadding = padding.PKCS1v15()

            try:
                self.rsa.verify(signature,serialized_content,apadding,get_hash_algo(md))
                return self.cn
            except InvalidSignature as e:
                if pre_py3:
                    continue
                raise SSLVerifyException(u'SSL signature verification failed for CSR %s ' % (self.subject))
        raise SSLVerifyException(u'SSL signature verification failed for CSR %s ' % (self.subject))

    @property
    def extensions(self):
        """Returns certificates extensions as a dict

        Returns:
            dict

        """
        return dict([(e.oid._name,e.value) for e in self.csr.extensions])

    @property
    def is_ca(self):
        """Return Tue if certificate has CA:TRUE baisc contraints"""
        return 'basicConstraints' in self.extensions and self.extensions['basicConstraints'].ca

    @property
    def is_code_signing(self):
        """Return True if certificate has 'Code Signing' in its extendedKeyUsage"""
        ext_key_usages = 'extendedKeyUsage' in self.extensions and self.extensions['extendedKeyUsage']
        if ext_key_usages:
            return len([usage for usage in ext_key_usages if usage._name == 'codeSigning'])>0
        else:
            return False

    @property
    def is_client_auth(self):
        """Return True if certificate has 'Code Signing' in its extendedKeyUsage"""
        ext_key_usages = 'extendedKeyUsage' in self.extensions and self.extensions['extendedKeyUsage']
        if ext_key_usages:
            return len([usage for usage in ext_key_usages if usage._name == 'clientAuth'])>0
        else:
            return False

    def has_usage(self,usage):
        """Return usage if certificate has the requested usage

        Args:
            usage (str): ca or code_signing

        """
        if usage == 'ca' and self.is_ca:
            return usage
        elif usage == 'code_signing' and self.is_code_signing:
            return usage
        elif usage == 'client_auth' and self.is_client_auth:
            return usage
        else:
            return ''

class SSLCertificate(BaseObjectClass):
    """Hold a X509 public certificate

    Global usage:

    >>> cakey = SSLPrivateKey().create()
    >>> cacert = cakey.build_sign_certificate(cn='testca')
    >>> cacert.is_ca
    True

    >>> mykey = SSLPrivateKey().create()
    >>> mycsr = mykey.build_csr(cn='Myself')
    >>> mycert = cacert.build_certificate_from_csr(mycsr,cakey)
    >>> mycert.cn
    u'Myself'

    >>> mycert.issuer
    {'commonName': u'testca'}

    >>> ca = SSLCABundle(certificates=[cacert])
    >>> ca.check_certificates_chain([mycert])
    [<SSLCertificate cn=u'Myself' issuer=u'testca' validity=2018-01-30 - 2019-01-30 Code-Signing=True CA=False>,
    <SSLCertificate cn=u'testca' issuer=u'testca' validity=2018-01-30 - 2028-01-28 Code-Signing=False CA=True>]

    >>> ca = SSLCABundle(certificates=[])
    >>> ca.check_certificates_chain([mycert,cacert])
    Traceback (most recent call last):
    EWaptCertificateUnknownIssuer: Unknown issuer testca for certificate testca

    """
    def __init__(self,crt_filename=None,crt=None,crt_string=None,ignore_validity_checks=False):
        """\
        Args:
            public_cert (str): File Path to X509 encoded certificate
            crt : cryptography.x509.Certificate
            crt_string (str): X09 PEM encoded string
        """
        self._public_cert_filename = crt_filename
        self._crt = None
        self._rsa = None
        self._key = None
        if crt:
            self._crt = crt
        elif crt_string:
            self._load_cert_data(crt_string)
        self.ignore_validity_checks = ignore_validity_checks

    def _load_cert_data(self,pem_data):
        try:
            self._crt = x509.load_pem_x509_certificate(str(pem_data),default_backend())
        except ValueError:
            self._crt = x509.load_der_x509_certificate(str(pem_data),default_backend())

    def _load_cert_file(self,filename):
        with open(filename,'rb') as crt_file:
            self._load_cert_data(crt_file.read())

    def as_pem(self):
        return self.crt.public_bytes(serialization.Encoding.PEM)

    def save_as_pem(self,filename=None):
        if filename is None:
            filename = self.public_cert_filename
        pem_data = self.as_pem()
        with open(filename,'wb') as f:
            f.write(pem_data)
        self._public_cert_filename = filename

    def as_X509(self):
        """Return pycrypto style X509 object"""
        return crypto.load_certificate(crypto.FILETYPE_PEM,self.as_pem())

    def from_X509(self,x509_cert):
        """Initialize certificate from pycrypto style X509 object"""
        assert(isinstance(x509_cert,SSL.X509))
        self._load_cert_data(crypto.dump_certificate(crypto.FILETYPE_PEM,x509_cert))

    @property
    def public_cert_filename(self):
        """Return filename if certificate was/will be loaded from a file"""
        return self._public_cert_filename

    @public_cert_filename.setter
    def public_cert_filename(self,value):
        if value != self._public_cert_filename:
            self._public_cert_filename = value
            self._crt = None
            self._rsa = None
            self._key = None
            self._crt = None

    @property
    def crt(self):
        """Return cryptopgraphy.Certificate instance"""
        if self._crt is None:
            if not self._public_cert_filename:
                raise EWaptMissingCertificate('Public certificate filename not provided')
            if not os.path.isfile(self._public_cert_filename):
                raise EWaptMissingCertificate(u'Public certificate %s not found' % self._public_cert_filename)
            self._load_cert_file(self._public_cert_filename)
        return self._crt

    @crt.setter
    def crt(self,value):
        if value != self._crt:
            self._crt = value
            self._rsa = None
            self._key = None

    @property
    def rsa(self):
        """Return public RSA key"""
        if not self._rsa:
            self._rsa = self.crt.public_key()
        return self._rsa

    @property
    def modulus(self):
        return format(self.rsa.public_numbers().n, "x")

    def _subject_attribute(self,oid):
        att = self.crt.subject.get_attributes_for_oid(oid)
        if att:
            return att[0].value
        else:
            return None

    @property
    def subject_dn(self):
        return self._subject_attribute(x509.NameOID.DN_QUALIFIER)

    @property
    def organisation(self):
        return self._subject_attribute(x509.NameOID.ORGANIZATION_NAME)

    @property
    def cn(self):
        return self._subject_attribute(x509.NameOID.COMMON_NAME)

    @property
    def subject(self):
        """Returns subject of the certificate as a Dict"""
        subject = self.crt.subject
        result = {}
        for attribute in subject:
            result[attribute.oid._name]= attribute.value
        return result

    def get_fingerprint(self,md='sha256'):
        """Get raw bytes fingerprint"""
        return self.crt.fingerprint(get_hash_algo(md))

    @property
    def fingerprint(self):
        """Get hex endoded sha256 fingerprint"""
        return self.get_fingerprint(md='sha256').encode('hex')

    def digest(self,md='sha256'):
        """Return a fingerprint in human redeable hexadecimal

        Args:
            md: hash algorithm for fingerprint

        Returns:
            str : hex encoded digest of fingerprint of certificate
        """
        hexdigest = self.get_fingerprint(md).encode('hex')
        return ':'.join(hexdigest[i:i+2] for i in range(0, len(hexdigest), 2))

    @property
    def issuer(self):
        """Return issuer as a dict of x509 name attributes

        Returns:
            dict

        >>> c = SSLCertificate('c:/private/htouvet-tis-wapt.crt')
        >>> c.issuer
        {'commonName': u'TRANQUIL IT SYSTEMS',
         'countryName': u'FR',
         'localityName': u'SAINT SEBASTIEN SUR LOIRE',
         'organizationName': u'TRANQUIL IT SYSTEMS',
         'postalCode': u'44230',
         'stateOrProvinceName': u'Loire Atlantique',
         'streetAddress': u'12 avenue Jules Verne'}
        """
        data = self.crt.issuer
        result = {}
        for attribute in data:
            result[attribute.oid._name] = attribute.value
        return result

    @property
    def issuer_subject_hash(self):
        return sha1_for_data(self.crt.issuer.public_bytes(default_backend()))

    @property
    def issuer_dn(self):
        return u','.join([u"%s=%s"%(attribute.oid._name,attribute.value) for attribute in self.crt.issuer])

    @property
    def issuer_cn(self):
        return self.issuer.get('commonName',None)

    @property
    def issuer_hash(self):
        return self.issuer.get('commonName',None)

    @property
    def subject_hash(self):
        return sha1_for_data(self.crt.subject.public_bytes(default_backend()))

    @property
    def authority_key_identifier(self):
        """Identify the authority which has signed the certificate

        Returns:
            bytes

        >>> c = SSLCertificate('c:/private/htouvet-tis-wapt.crt')
        >>> c.authority_key_identifier
        '\xa7Yx!\x0f\xe8\xe5x\x9c\x9br\x7f|\x7f\x90\x04\xea\x10\x19\xe8'
        """
        keyid = self.extensions.get('authorityKeyIdentifier',None)
        if keyid:
            return keyid.key_identifier
        else:
            return None

    @property
    def subject_key_identifier(self):
        """Identify the certificate by its subject

        Returns:
            bytes

        >>> c.subject_key_identifier
        '\xf2\x99\xd7\xfao\n\xf1\x1e\x03?\xd0\xf2\xff6\xfe\xe8\x8cv\xab\x1a'
        """
        keyid = self.extensions.get('subjectKeyIdentifier',None)
        if keyid:
            return keyid.digest
        else:
            return None

    @property
    def key_usage(self):
        keyusage = self.extensions.get('keyUsage',None)
        if keyusage:
            result = []
            for att in ('digital_signature','content_commitment','key_encipherment',
                'data_encipherment','key_agreement','key_cert_sign','crl_sign','encipher_only','decipher_only'):
                if hasattr(keyusage,att) and getattr(keyusage,att):
                    result.append(att)
            return result
        else:
            return None


    @property
    def subject_alt_names(self):
        """Other names of the subject (in addition to cn)"""
        names = self.extensions.get('subjectAltName',None)
        if names:
            return [n.value for n in names]
        else:
            return None


    @property
    def serial_number(self):
        """Serial number of the certificate, which is used by revocation process"""
        return self.crt.serial_number


    def verify_content(self,content,signature,md='sha256',block_size=2**20):
        """Check that the signature matches the content

        Args:
            content (str) : content to check. if not str, the structure will be converted to json first
            signature (str) : ssl signature of the content

        Returns:
            str: subject (CN) of current certificate or raise an exception if no match

        Raises SSLVerifyException
        """
        prev_serialized_content = None
        for pre_py3 in (True,False):
            serialized_content = serialize_content_for_signature(content,pre_py3=pre_py3)
            # don't recheck. If we are here, signature has not been verified properly on previous round
            if prev_serialized_content is not None and prev_serialized_content == serialized_content:
                break
            # todo : recommended for new projects...
            #apadding = padding.PSS(
            #    mgf=padding.MGF1(get_hash_algo(md)),
            #    salt_length=padding.PSS.MAX_LENGTH)

            # compatible with openssl sign
            apadding = padding.PKCS1v15()

            try:
                self.rsa.verify(signature,serialized_content,apadding,get_hash_algo(md))
                return self.cn
            except InvalidSignature as e:
                # backward compatibility with pre17 json serialization
                if pre_py3:
                    prev_serialized_content = serialized_content
                    continue
                raise SSLVerifyException(u'SSL signature verification failed for certificate %s issued by %s' % (self.subject,self.issuer_cn))

        raise SSLVerifyException(u'SSL signature verification failed for certificate %s issued by %s' % (self.subject,self.issuer_cn))

    def match_key(self,key):
        """Check if certificate matches the given private key"""
        if not isinstance(key,SSLPrivateKey):
            key = SSLPrivateKey(key)
        return self.modulus == key.modulus

    def matching_key_in_dirs(self,directories=None,password_callback=None,private_key_password=None):
        """Return the first SSLPrivateKey matching this certificate

        Args:
            directories (list): list of directories to look for pem encoded private key files
                                if None, look in the same directory as certificate file.

        Returns:
            SSLPrivateKey : or None if nothing found.

        >>> crt = SSL
        """
        if directories is None:
            directories = os.path.abspath(os.path.dirname(self.public_cert_filename))
        directories = ensure_list(directories)

        for adir in directories:
            for akeyfile in glob.glob(os.path.join(adir,u'*.pem')):
                try:
                    key = SSLPrivateKey(os.path.abspath(akeyfile),callback = password_callback,password = private_key_password)
                    if key.match_cert(self):
                        logger.debug(u'SUCCESS key %s match certificate %s'% (akeyfile,self.public_cert_filename or self.subject_dn))
                        return key
                    logger.debug(u'NO MATCH key %s does not match certificate %s'% (akeyfile,self.public_cert_filename or self.subject_dn))
                except Exception as e:
                    logger.debug(u'ERROR for %s: %s'%(akeyfile,e))
        return None

    @property
    def not_before(self):
        result = self.crt.not_valid_before
        return result

    @property
    def not_after(self):
        result = self.crt.not_valid_after
        return result

    def is_valid(self):
        """Check validity of certificate
        not before / not after
        """
        if self.ignore_validity_checks:
            return True
        nb,na = self.not_before,self.not_after
        now = datetime.datetime.utcnow()
        return \
            now >= nb and now <= na

    def crl_urls(self):
        """retruns list of URL where to get CRL for the Authority which has signed this certificate"""
        return [d.full_name[0].value for d in self.extensions.get('cRLDistributionPoints',[])]

    def issuer_cert_urls(self):
        """returns URL where to get Issuer cert directly"""
        return [d.access_location.value for d in self.extensions.get('authorityInfoAccess',[]) if d.access_method._name == 'caIssuers']

    def __iter__(self):
        for k in ['issuer_dn','fingerprint','subject_dn','cn','is_code_signing','is_ca']:
            yield k,getattr(self,k)

    def __str__(self):
        return u'SSLCertificate cn=%s'%self.cn

    def __repr__(self):
        return '<SSLCertificate cn=%s fingerprint=%s  issuer=%s validity=%s - %s Code-Signing=%s CA=%s>'%\
            (repr(self.cn),
            self.fingerprint[0:6],
            repr(self.issuer.get('commonName','?')),
            self.not_before.strftime('%Y-%m-%d'),
            self.not_after.strftime('%Y-%m-%d'),
            self.is_code_signing,self.is_ca)

    def __cmp__(self,crt):
        if isinstance(crt,SSLCertificate):
            return cmp((self.is_valid(),self.is_code_signing,self.not_before,self.not_after,self.get_fingerprint()),
                            (crt.is_valid(),crt.is_code_signing,crt.not_before,crt.not_after,crt.get_fingerprint()))
        elif isinstance(crt,dict):
            return cmp(self.subject,crt)
        else:
            raise ValueError(u'Can not compare SSLCertificate with %s'%(type(crt)))

    def encrypt(self,content):
        """Encrypt a (small) message will can be decrypted with the private key

        """
        apadding = padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA1()),
            algorithm=hashes.SHA1(),
            label=None)
        return self.rsa.encrypt(content,apadding)

    def encrypt_fernet(self,content):
        """Encrypt content with fernet symetric algo

        create a fernet key, encrypt it using RSA
        encrypt data using fernet key
        return bytes with header, encrypted fernet key, and encrypted fernet data

        fernet :  128-bit AES in CBC mode and PKCS7 padding, with HMAC using SHA256 for authentication

        Args:
            content (bytes): data to encrypt

        Returns:
            crypted_data (bytes): starts with 'RSAFERNET', then rsa key length (base10) on 3 chars
                                   then rsa encrypted fernet key, then fernet encrypted data
        """
        symkey = Fernet.generate_key()
        rsa_symkey = self.encrypt(symkey)
        f = Fernet(symkey)
        return 'RSAFERNET%03d%s%s' % (len(rsa_symkey),rsa_symkey,f.encrypt(str(content)))

    @property
    def extensions(self):
        """Returns certificates extensions as a dict

        Returns:
            dict

        """
        return dict([(e.oid._name,e.value) for e in self.crt.extensions])

    @property
    def is_ca(self):
        """Return Tue if certificate has CA:TRUE baisc contraints"""
        return 'basicConstraints' in self.extensions and self.extensions['basicConstraints'].ca

    @property
    def is_code_signing(self):
        """Return True if certificate has 'Code Signing' in its extendedKeyUsage"""
        ext_key_usages = 'extendedKeyUsage' in self.extensions and self.extensions['extendedKeyUsage']
        if ext_key_usages:
            return len([usage for usage in ext_key_usages if usage._name == 'codeSigning'])>0
        else:
            return False

    @property
    def is_client_auth(self):
        """Return True if certificate has 'Code Signing' in its extendedKeyUsage"""
        ext_key_usages = 'extendedKeyUsage' in self.extensions and self.extensions['extendedKeyUsage']
        if ext_key_usages:
            return len([usage for usage in ext_key_usages if usage._name == 'clientAuth'])>0
        else:
            return False

    def has_usage(self,usage):
        """Return usage if certificate has the requested usage

        Args:
            usage (str): ca or code_signing

        """
        if usage == 'ca' and self.is_ca:
            return usage
        elif usage == 'code_signing' and self.is_code_signing:
            return usage
        elif usage == 'client_auth' and self.is_client_auth:
            return usage
        else:
            return ''

    def verify_old(self,CAfile,check_errors=True):
        """Check validity of certificate against list of CA and validity

        Raise error if not OK
        """
        wapt_basedir = os.path.abspath(os.path.join(os.path.dirname(__file__)))
        openssl_bin = os.path.join(wapt_basedir,'openssl.exe')
        certfile = self.public_cert_filename
        print '"%(openssl_bin)s" verify -CAfile "%(CAfile)s" "%(certfile)s"' % locals()
        p = subprocess.Popen('"%(openssl_bin)s" verify -CAfile "%(CAfile)s" "%(certfile)s"' % locals(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        check_output = p.communicate()[0]

        errors = []
        result = False
        for output in check_output.splitlines():
            if output.startswith('error'):
                error = output.rsplit(':',1)[1]
                if check_errors and 'certificate has expired' in error:
                    raise EWaptCertificateExpired(u'Certificate %s error: %s'%(self.public_cert_filename,error))
                elif check_errors and 'unable to get local issuer certificate' in error:
                    raise EWaptCertificateUnknownIssuer(u'Certificate %s error: %s'%(self.public_cert_filename,error))
                else:
                    raise EWaptBadCertificate(u'Certificate %s error: %s'%(self.public_cert_filename,error))
                errors.append(errors)
            if output=='OK':
                result = True
        logger.debug(check_output)
        if not result:
            raise EWaptCertificateUnknownIssuer(u'Unknown issuer for %s' % (self.public_cert_filename))
        return result

    def verify_signature_with(self,cabundle=None):
        """Check validity of certificates signature along the whole certificates chain

        Issuer certificates must have the CA constraint.
        Issuer is found using hash of issuer_subject and subject bytes.

        Args;
            cabundle: bundle of CA certificates, or SSLCertificate od list of certificates
                      if None, get bindle from certifi default list.

        Returns:
            list : certificate chain
        """
        chain = []
        certificate = self
        if cabundle is None:
            cabundle = SSLCABundle(certifi.where())
        elif isinstance(cabundle,SSLCertificate):
            cabundle = SSLCABundle(certificates = [cabundle])
        elif isinstance(cabundle,list):
            cabundle = SSLCABundle(certificates = cabundle)

        issuer = cabundle.issuer_cert_for(certificate)

        if not issuer:
            raise SSLVerifyException(u'Issuer CA certificate %s can not be found in supplied bundle'%self.issuer_dn)

        while issuer:
            try:
                verifier = CertificateVerificationContext(issuer.crt)
                verifier.update(certificate.crt)
                verifier.verify()
                chain.append(issuer)
                if issuer.subject_hash == issuer.issuer_subject_hash:
                    break
                certificate = issuer
                issuer = cabundle.issuer_cert_for(certificate)
            except Exception as e:
                logger.critical(u"Certificate validation error for issuer %s when validating chain for %s : %s" % (repr(issuer),repr(self),e))
                raise

        return chain

    def verify_claim(self,claim,max_age_secs=None,required_attributes=[]):
        """Verify a simple dict signed with SSLPrivateKey.sign_claim

        Args:
            claim (dict) : with keys signature,signed_attributes,signer,signature_date

        Returns:
            dict: signature_date,signer,verified_by(cn),signer_fingerprint

        Raise:
            SSLVerifyException


        >>> key = SSLPrivateKey('c:/private/150.pem')
        >>> crt = SSLCertificate('c:/private/150.crt')
        >>> action = dict(action='install',package='tis-7zip')
        >>> action_signed
            {'action': None,
             'package': None,
             'signature': 'jSJbX3sPmiEBRxN3Sue4fTSlJ2Q6llUSOIkleCm4NyFQlSc0KvLKbtlmHxvYV7mPW3TDYjfhkuQSG0ZfQQmo0r+zcA9ZL075P/vNLkxwElOYacMtBBObsxhPU7DKc4AdQMorgSfSEpW4a/Zq5VPJy9q6vBJxSzZjnHGmuPYlfQKuedP1dY6ifCrcAelKEZOKZl5LJl6e0NHeiXy3+3e4bm8V2VtDPCbvVKtIMRgA5qtDDrif3IauwzUyzEpnC0d229ynz6LAj5WdZR32HtV0g5aJ5ye5rQ+IAcGJSbxQ3EJZQhZy1wZ6WUVsF9/mXLbR/d1xRl9M0CqI+8eUvQWD2g==',
             'signature_date': '20170606-163401',
             'signed_attributes': ['action', 'package'],
             'signer': '150',
             'signer_fingerprint': '88654A5A946B8BFFFAC7F61A2E21B7F02168D5E4'}
        >>> action_signed = key.sign_claim(action,signer_certificate_chain=crt)
        >>> print crt.verify_claim(action_signed)
        {'signer': '150', 'verified_by': '150', 'signature_date': '20170606-163401'}
        """
        attributes = claim['signed_attributes']

        for att in ['signed_attributes','signer','signature_date']:
            if not att in required_attributes:
                required_attributes.append(att)

        for att in required_attributes:
            if not att in attributes:
                raise SSLVerifyException(u'Missing required attribute "%s" in signed claim' % att)

        reclaim = {att:claim.get(att,None) for att in attributes if att != 'signature' }
        signature = claim['signature'].decode('base64')

        if max_age_secs is not None:
            signature_date = isodate2datetime(claim['signature_date'])
            delta = abs(datetime.datetime.utcnow() - signature_date)
            if delta > datetime.timedelta(seconds=max_age_secs):
                raise SSLVerifyException(u'Data too old or in the futur age : %ss...' % delta.seconds)
        self.verify_content(reclaim,signature)
        return dict(
            signature_date=claim['signature_date'],
            signer=claim['signer'],
            verified_by=self.cn,
            )

    def build_certificate_from_csr(self,csr,ca_signing_key,validity_duration=365):
        """Build a certificate by signing a CSR with CA certificate (self) and provided key

        Args:
            self : CA certificate for issuer.
            csr (SSLCertificateSigningRequest): The certificate signing request to be signed.
            ca_signing_key (SSLPrivateKey): CA Key to sign the resulting certificate.

        Returns:
            SSLCertificate

        >>>
        """
        if not csr.csr.is_signature_valid:
            raise EWaptCryptoException('CSR signature check failed')

        extensions = []

        issuer = self.crt.subject
        extensions.append(
            dict(extension=x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                self.crt.extensions.get_extension_for_oid(x509.OID_SUBJECT_KEY_IDENTIFIER)),
            critical=False))

        serial_number = x509.random_serial_number()

        builder = x509.CertificateBuilder().serial_number(
            serial_number
        ).issuer_name(
            issuer
        ).subject_name(
            csr.csr.subject
        ).public_key(
            csr.csr.public_key()
        ).not_valid_before(
            datetime.datetime.utcnow(),
        ).not_valid_after(
            datetime.datetime.utcnow()+datetime.timedelta(days=validity_duration)
        )

        for ext in csr.csr.extensions:
            builder = builder.add_extension(ext.value, ext.critical)

        for ext in extensions:
            builder = builder.add_extension(
                ext.get('extension'), ext.get('critical')
            )

        crypto_crt = builder.sign(ca_signing_key.rsa,algorithm=hashes.SHA256(), backend=default_backend())
        return SSLCertificate(crt = crypto_crt)

    def build_crl(self,ca_signing_key,revoked_serial_numbers=[],validity_duration=30):
        revoked_certificates = []
        for serial in revoked_serial_numbers:
            revoked = x509.RevokedCertificateBuilder(serial,datetime.datetime.utcnow())
            revoked_certificates.append(revoked.build(backend=default_backend()))

        extensions = []
        issuer = self.crt.subject
        extensions.append(
            dict(extension=x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(
                self.crt.extensions.get_extension_for_oid(x509.OID_SUBJECT_KEY_IDENTIFIER)),
            critical=False))

        crl_builder = x509.CertificateRevocationListBuilder(self._crt.subject,
            last_update=datetime.datetime.utcnow(),
            next_update=datetime.datetime.utcnow() + datetime.timedelta(days=validity_duration),
            revoked_certificates=revoked_certificates)

        for ext in extensions:
            crl_builder = crl_builder.add_extension(
                ext.get('extension'), ext.get('critical')
            )

        crypto_crl = crl_builder.sign(ca_signing_key.rsa,algorithm=hashes.SHA256(), backend=default_backend())
        return SSLCRL(crl=crypto_crl)

class SSLCRL(BaseObjectClass):
    """

    >>> cakey = SSLPrivateKey().create()
    >>> cacert = cakey.build_sign_certificate(cn='testca')
    >>> mykey = SSLPrivateKey().create()
    >>> mycsr = mykey.build_csr(cn='Myself')
    >>> mycert = cacert.build_certificate_from_csr(mycsr,cakey)
    >>> mycert.serial_number
    261540517078156283789121921716039590055012744199L

    """

    def __init__(self,filename=None,pem_data=None,der_data=None,crl=None):
        self._crl = crl
        self.filename = filename
        if pem_data is not None:
            self._load_pem_data(pem_data)
        elif der_data is not None:
            self._load_der_data(der_data)

    def _load_pem_data(self,data):
        self._crl = x509.load_pem_x509_crl(data,default_backend())

    def _load_der_data(self,data):
        self._crl = x509.load_der_x509_crl(data,default_backend())

    @property
    def crl(self):
        if self._crl is None:
            if os.path.isfile(self.filename):
                try:
                    with open(self.filename,'rb') as der:
                        self._load_der_data(der.read())
                except Exception as e:
                    with open(self.filename,'rb') as pem:
                        self._load_pem_data(pem.read())
            else:
                self._crl = None

        return self._crl

    def revoked_certs(self):
        result = [dict(serial_number=cert.serial_number,revocation_date=cert.revocation_date) for cert in self.crl]
        return result

    def is_revoked(self,cert):
        if cert.authority_key_identifier is None and cert.issuer_subject_hash is None :
            raise Exception(u'Neither Authority key identifier extension nor Issuer Subject to identify CA of certificate %s' % cert)

        if (cert.authority_key_identifier is not None and cert.authority_key_identifier == self.authority_key_identifier) or \
            (cert.issuer_subject_hash is not None and cert.issuer_subject_hash == self.issuer_subject_hash):
            for rev_cert in self.crl:
                if rev_cert.serial_number == cert.serial_number:
                    return rev_cert.revocation_date
        return False

    @property
    def extensions(self):
        """CRL extensions

        Returns:
            dict

        >>> c = SSLCertificate('c:/private/htouvet.crt')
        >>> c.extensions
        {'basicConstraints': <BasicConstraints(ca=True, path_length=None)>,
         'extendedKeyUsage': <ExtendedKeyUsage([<ObjectIdentifier(oid=1.3.6.1.5.5.7.3.3, name=codeSigning)>])>,
         'keyUsage': <KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False, data_encipherment=True, key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=None, decipher_only=None)>,
         'subjectKeyIdentifier': <SubjectKeyIdentifier(digest='\x83h\x93\x1f%\xc8:\xb5E\xf5\x07b\xb6\x92m\x87Y\x96~\x03')>}
            """
        return dict([(e.oid._name,e.value) for e in self.crl.extensions])

    @property
    def authority_key_identifier(self):
        """Identify the authority which has signed the CRL

        Returns:
            bytes
        """
        keyid = self.extensions.get('authorityKeyIdentifier',None)
        if keyid:
            return keyid.key_identifier
        else:
            return None

    @property
    def last_update(self):

        return self.crl.last_update

    @property
    def next_update(self):
        return self.crl.next_update

    @property
    def issuer(self):
        data = self.crl.issuer
        result = {}
        for attribute in data:
            result[attribute.oid._name] = attribute.value
        return result

    @property
    def issuer_subject_hash(self):
        return sha1_for_data(self.crl.issuer.public_bytes(default_backend()))

    @property
    def issuer_dn(self):
        return u','.join([u"%s=%s"%(attribute.oid._name,attribute.value) for attribute in self.crl.issuer])

    @property
    def issuer_cn(self):
        return self.issuer.get('commonName',None)

    def verify_signature_with(self,cabundle=None):
        """Check validity of CRL signature

        Args;
            cabundle: bundle of CA certificates

        Returns:
            list : certificate chain
        """
        chain = []
        crl = self
        if cabundle is None:
            cabundle = SSLCABundle(certifi.where())

        if isinstance(cabundle,SSLCABundle):
            issuer = cabundle.certificate_for_subject_key_identifier(crl.authority_key_identifier) or cabundle.certificate_for_subject_hash(crl.issuer_subject_hash)
        else:
            issuer = cabundle
            assert(isinstance(issuer,SSLCertificate))

        if not issuer:
            raise SSLVerifyException(u'CRL Issuer CA certificate %s can not be found in supplied bundle'%self.issuer)

        try:
            # check CRL signature
            verifier = CertificateRevocationListVerificationContext(issuer.crt)
            verifier.update(crl.crl)
            verifier.verify()
            # append CRL issuer chain
            chain.extend(issuer.verify_signature_with(cabundle))
            return chain
        except Exception as e:
            logger.critical(u"CRL validation error on certificate %s : %s" % (issuer.subject,e))
            raise


    def as_pem(self):
        return self.crl.public_bytes(serialization.Encoding.PEM)

    def as_der(self):
        return self.crl.public_bytes(serialization.Encoding.DER)

    def save_as_pem(self,filename=None):
        if filename is None:
            filename = self.filename
        pem_data = self.as_pem()
        with open(filename,'wb') as f:
            f.write(pem_data)
        self.filename = filename
        return filename

    def __cmp__(self,crl):
        return cmp((self.authority_key_identifier,self.last_update),(crl.authority_key_identifier,crl.last_update))

    def __repr__(self):
        return '<SSLCRL %s>' % self.issuer


class SSLPKCS12(object):
    """Encapsulate a PKCS#12 key/certificate/ca certs store


    """
    def __init__(self,filename=None,password=None):
        """Initialaize and load a p12 file

        Args:
            filename (str)
            password (str)

        """

        self._filename = filename
        self._private_key = None
        self._certificate = None
        self.ca_certificates = []
        if self._filename is not None:
            self.load_from_p12(password=password)

    def load_from_p12(self,filename=None,password=None):
        """Load a pkc#12 file from a file

        """

        self._private_key = None
        self._certificate = None
        self.ca_certificates = []

        if filename is not None:
            self._filename = filename

        if os.path.isfile(self._filename):
            data = open(self._filename,'rb').read()
            (pc_private_key, pc_certificate, pc_additional_certificates) = load_key_and_certificates(data,password,default_backend())
            self._private_key = SSLPrivateKey(rsa = pc_private_key)
            self._certificate = SSLCertificate(crt = pc_certificate)
            for pc_cert in pc_additional_certificates:
                self.add_ca_certificate(SSLCertificate(crt=pc_cert))
        return self

    @property
    def private_key(self):
        """Returns the RSA Private key as a SSLPrivateKey object

        Returns:
            SSLPrivateKey
        """
        return self._private_key

    @private_key.setter
    def private_key(self,key):
        self._private_key = key

    @property
    def certificate(self,certificate):
        """Returns the X509 certificate as a SSLCertificate object

        Returns:
            SSLCertificate
        """
        return self._certificate

    @certificate.setter
    def certificate(self,certificate):
        self._certificate = certificate

    def add_ca_certificate(self,certificate):
        if not certificate in self.ca_certificates:
            self.ca_certificates.append(certificate)

    def save_as_p12(self,filename=None,password=None,friendly_name=None):
        pkcs12 = crypto.PKCS12()
        pkcs12.set_privatekey(crypto.PKey().from_cryptography_key(self._private_key.rsa))
        pkcs12.set_certificate(self._certificate.as_X509())
        if friendly_name:
            pkcs12.set_friendlyname(friendly_name.encode('utf8'))
        pkcs12.set_ca_certificates([certificate.as_X509() for certificate in self.ca_certificates])
        if filename is None:
            filename = self._filename
        if filename is not None:
            open(filename,'wb').write(pkcs12.export(password.encode('utf8')))
        else:
            raise Exception(u'No filename supplied for pkcs12 export')


if __name__ == '__main__':
    import doctest
    import sys
    reload(sys)
    sys.setdefaultencoding("UTF-8")
    import doctest
    doctest.ELLIPSIS_MARKER = '???'
    doctest.testmod(optionflags=doctest.ELLIPSIS)
    sys.exit(0)
