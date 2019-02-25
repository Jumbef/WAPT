#!/opt/wapt/bin/python
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

__all__ = [
    'control_to_dict',
    'md5_for_file',
    'parse_major_minor_patch_build',
    'make_version',
    'PackageVersion',
    'PackageRequest',
    'PackageEntry',
    'HostCapabilities',
    'WaptBaseRepo',
    'WaptLocalRepo',
    'WaptRemoteRepo',
    'update_packages',
    'REGEX_PACKAGE_VERSION',
    'REGEX_PACKAGE_CONDITION',
    'ArchitecturesList',
    'EWaptException',
    'EWaptBadSignature',
    'EWaptCorruptedFiles',
    'EWaptNotSigned',
    'EWaptBadControl',
    'EWaptBadSetup',
    'EWaptNeedsNewerAgent',
    'EWaptDiskSpace',
    'EWaptBadTargetOS',
    'EWaptNotAPackage',
    'EWaptDownloadError',
    'EWaptMissingLocalWaptFile',
    'EWaptNeedsNewerAgent',
    'EWaptConfigurationError',
    'EWaptUnavailablePackage',
    'EWaptNotSourcesDirPackage',
    'EWaptPackageSignError',
    'EWaptConflictingPackage',
    'EWaptInstallPostponed',
    'EWaptInstallError',
    ]


import os
import custom_zip as zipfile
import StringIO
import hashlib
import logging
import glob
import codecs
import re
import time
import json
import ujson
import sys
import types
import requests
import email
import datetime
import tempfile
import email.utils
import shutil
import base64
import copy
import gc
import uuid

from iniparse import RawConfigParser
import traceback

from waptutils import BaseObjectClass,Version,ensure_unicode,ZipFile,force_utf8_no_bom
from waptutils import create_recursive_zip,ensure_list,all_files,list_intersection
from waptutils import datetime2isodate,httpdatetime2isodate,httpdatetime2datetime,fileutcdate,fileisoutcdate,isodate2datetime
from waptutils import default_http_headers,wget,get_language,import_setup,import_code
from waptutils import _disable_file_system_redirection

from waptcrypto import EWaptMissingCertificate,EWaptBadCertificate
from waptcrypto import SSLCABundle,SSLCertificate,SSLPrivateKey,SSLCRL
from waptcrypto import SSLVerifyException,hexdigest_for_data,hexdigest_for_file,serialize_content_for_signature

logger = logging.getLogger()

def md5_for_file(fname, block_size=2**20):
    """Calculate the md5 hash of file.

    Returns:
        str: md5 hash as hexadecimal string.
    """
    f = open(fname,'rb')
    md5 = hashlib.md5()
    while True:
        data = f.read(block_size)
        if not data:
            break
        md5.update(data)
    return md5.hexdigest()

# From Semantic Versioning : http://semver.org/ by Tom Preston-Werner,
# valid : 0.0-0  0.0.0-0 0.0.0.0-0
REGEX_PACKAGE_VERSION = re.compile(r'^(?P<major>[0-9]+)'
                    '(\.(?P<minor>[0-9]+))?'
                    '(\.(?P<patch>[0-9]+))?'
                    '(\.(?P<subpatch>[0-9]+))?'
                    '(\-(?P<packaging>[0-9A-Za-z]+(\.[0-9A-Za-z]+)*))?$')

# tis-exodus(>2.3.4-10)
# changed in 1.6.2.4
REGEX_PACKAGE_CONDITION = re.compile(r'(?P<package>[^()]+)\s*(\(\s*(?P<operator>[<=>]*)\s*(?P<version>\S+)\s*\))?')

REGEX_VERSION_CONDITION = re.compile(r'(?P<operator>[<=>]*)\s*(?P<version>\S+)')


def parse_major_minor_patch_build(version):
    """Parse version to major, minor, patch, pre-release, build parts.
    """
    match = REGEX_PACKAGE_VERSION.match(version)
    if match is None:
        raise ValueError(u'%s is not valid SemVer string' % version)

    verinfo = match.groupdict()

    def int_or_none(name):
        if name in verinfo and verinfo[name] != None :
            return int(verinfo[name])
        else:
            return None
    verinfo['major'] = int_or_none('major')
    verinfo['minor'] = int_or_none('minor')
    verinfo['patch'] = int_or_none('patch')
    verinfo['subpatch'] = int_or_none('subpatch')

    return verinfo


def make_version(major_minor_patch_build):
    p1 = u'.'.join( [ "%s" % major_minor_patch_build[p] for p in ('major','minor','patch','subpatch') if major_minor_patch_build[p] != None])
    if major_minor_patch_build['packaging'] != None:
        return '-'.join([p1,major_minor_patch_build['packaging']])
    else:
        return p1

ArchitecturesList = ('all','x86','x64')

class EWaptException(Exception):
    pass

class EWaptBadSignature(EWaptException):
    pass

class EWaptDownloadError(EWaptException):
    pass

class EWaptCorruptedFiles(EWaptException):
    pass

class EWaptNotSigned(EWaptException):
    pass

class EWaptBadControl(EWaptException):
    pass

class EWaptBadSetup(EWaptException):
    pass

class EWaptNeedsNewerAgent(EWaptException):
    pass

class EWaptDiskSpace(EWaptException):
    pass

class EWaptBadTargetOS(EWaptException):
    pass

class EWaptNotAPackage(EWaptException):
    pass

class EWaptNotSourcesDirPackage(EWaptException):
    pass

class EWaptMissingPackageHook(EWaptException):
    pass


class EWaptPackageSignError(EWaptException):
    pass

class EWaptInstallError(EWaptException):
    """Exception raised during installation of package
    msg is logged in local install database
    if retry_count is None, install will be retried indefinitely until success
    else install is retried at most retry_count times.
    """
    def __init__(self,msg,install_status='ERROR',retry_count=None):
        Exception.__init__(self,msg)
        self.install_status = install_status
        self.retry_count = retry_count


class EWaptInstallPostponed(EWaptInstallError):
    def __init__(self,msg,install_status='POSTPONED',retry_count=5,grace_delay=3600):
        EWaptInstallError.__init__(self,msg,install_status,retry_count)
        self.grace_delay = grace_delay

class EWaptUnavailablePackage(EWaptInstallError):
    pass

class EWaptConflictingPackage(EWaptInstallError):
    pass

class EWaptRemoveError(EWaptException):
    pass

class EWaptConfigurationError(EWaptException):
    pass

class EWaptMissingLocalWaptFile(EWaptException):
    pass



class HostCapabilities(BaseObjectClass):
    __all_attributes = ['uuid', 'language', 'os', 'os_version', 'architecture', 'dn', 'fqdn',
            'site', 'wapt_version', 'wapt_edition', 'packages_trusted_ca_fingerprints',
            'packages_blacklist', 'packages_whitelist', 'packages_locales',
            'packages_maturities', 'use_host_packages','host_packages_names',
            'host_profiles', 'host_certificate_fingerprint','host_certificate_authority_key_identifier']
    def __init__(self,**kwargs):
        self.uuid = None
        self.language = None
        self.os = None
        self.os_version = None
        self.architecture = None
        self.dn = None
        self.fqdn = None
        self.site = None
        self.wapt_version = None
        self.wapt_edition = None
        self.packages_trusted_ca_fingerprints = None
        self.packages_blacklist = None
        self.packages_whitelist = None
        self.packages_locales = None
        self.packages_maturities = None
        self.use_host_packages = None
        self.host_profiles = None
        self.host_packages_names = None
        self.host_certificate_fingerprint = None
        self.host_certificate_authority_key_identifier = None
        for (k,v) in kwargs.iteritems():
            if hasattr(self,k):
                setattr(self,k,v)
            else:
                #raise Exception('HostCapabilities has no attribute %s' % k)
                logger.critical('HostCapabilities has no attribute %s : ignored' % k)

    def __getitem__(self,name):
        if name is str or name is unicode:
            name = name.lower()
        if hasattr(self,name):
            return getattr(self,name)
        else:
            raise Exception(u'%s : No such attribute : %s' % (self.__class__.__name__,name))

    def __iter__(self):
        for key in self.__all_attributes:
            yield (key, getattr(self,key))

    def as_dict(self):
        return dict(self)

    def fingerprint(self):
        return hashlib.sha256(serialize_content_for_signature(self.as_dict())).hexdigest()

    def get_package_request_filter(self):
        """Returns a filter for package search in repositories

        Returns:
            PackageRequest
        """
        return PackageRequest(
            architectures=ensure_list(self.architecture),
            locales=ensure_list(self.packages_locales),
            maturities=self.packages_maturities,
            min_os_version=self.os_version,
            max_os_version=self.os_version,
            )

    def is_matching_package(self,package_entry):
        """Check if package_entry is matching the current capabilities and restrictions

        """
        if self.packages_blacklist is not None:
            for bl in self.packages_blacklist:  # pylint: disable=not-an-iterable
                if glob.fnmatch.fnmatch(package_entry.package,bl):
                    return False

        if self.packages_whitelist is not None:
            allowed = False
            for wl in self.packages_whitelist:  # pylint: disable=not-an-iterable
                if glob.fnmatch.fnmatch(package_entry.package,wl):
                    allowed = True
                    break
            if not allowed:
                return False

        if self.wapt_version is not None and package_entry.min_wapt_version and Version(package_entry.min_wapt_version) > Version(self.wapt_version):
            return False
        if self.os is not None and package_entry.target_os and package_entry.target_os != self.os:
            return False

        package_request = self.get_package_request_filter()
        return package_request.is_matched_by(package_entry)

    def __repr__(self):
        return repr(self.as_dict())

def PackageVersion(package_or_versionstr):
    """Splits a version string 1.2.3.4-567
    software version is clipped to 4 members
    if '-packaging' is not provided, the second member will be None

    Args:
        package_or_versionstr (str): package version string

    Returns:
        tuple: (Version,int) : soft version on 4 members / packaging as an int

    """
    if isinstance(package_or_versionstr,PackageEntry):
        package_or_versionstr = package_or_versionstr.version
    if isinstance(package_or_versionstr,Version):
        return (Version(package_or_versionstr,4),None)
    version_build = package_or_versionstr.split('-',1)
    if len(version_build)>1:
        return (Version(version_build[0],4),int(version_build[1]))
    else:
        return (Version(version_build[0],4),None)

class PackageRequest(BaseObjectClass):
    """Package and version request / condition
    The request is the basic packagename(=version) request
    Additional filters can be sepcified as arguments
    The list filters are ordered from most preferred to least preferred options

    Args:
        request (str): packagename(<=>version)
        architectures (list) : list of x64, x86
        locales (list) : list of 2 letters lki

    """
    _attributes = ['package','version','architectures','locales','maturities','min_os_version','max_os_version']

    def __init__(self,request=None,copy_from=None,**kwargs):
        self.package = None
        self.version = None
        self.architectures = None
        self.locales = None
        self.maturities = None
        # boundaries are included
        self.min_os_version = None
        self.max_os_version = None

        self._request = None
        self._package = None
        self._version_operator = None
        self._version = None
        self._architectures = None
        self._locales = None
        self._maturities = None
        self._min_os_version = None
        self._max_os_version = None

        if copy_from is not None:
            for k in self._attributes:
                setattr(self,k,getattr(copy_from,k))

        self.request = request

        for (k,v) in kwargs.iteritems():
            if hasattr(self,k):
                setattr(self,k,v)
            else:
                raise Exception('PackageRequest has no attribute %s' % k)

    @property
    def request(self):
        return self._request

    @request.setter
    def request(self,value):
        self._request = value
        if value:
            package_version = REGEX_PACKAGE_CONDITION.match(value).groupdict()
            self._package=package_version['package']
            if package_version['operator'] is not None:
                self._version_operator = package_version['operator']
            else:
                self._version_operator = '='

            if package_version['version'] is not None:
                self._version = PackageVersion(package_version['version'])
            else:
                self._version = None
        else:
            self._package = None
            self._version = None
            self._version_operator = None

    @property
    def version(self):
        return self._version

    @version.setter
    def version(self,value):
        if value is None:
            self._version_operator = None
            self._version = None
        else:
            package_version = REGEX_VERSION_CONDITION.match(value).groupdict()
            if package_version['operator'] is not None:
                self._version_operator = package_version['operator']
            else:
                self._version_operator = '='
            if package_version['version'] is not None:
                self._version = PackageVersion(package_version['version'])
            else:
                self._version = None

    @property
    def min_os_version(self):
        return self._min_os_version

    @min_os_version.setter
    def min_os_version(self,value):
        if value is not None and value != '':
            self._min_os_version = Version(value)
        else:
            self._min_os_version = None

    @property
    def max_os_version(self):
        return self._max_os_version

    @max_os_version.setter
    def max_os_version(self,value):
        if value is not None and value != '':
            self._max_os_version = Version(value)
        else:
            self._max_os_version = None

    def _is_matched_version(self,version):
        """Return True if this request is verified by the provided version

        Args:
            version (str or Version): version to check against this request

        Returns:
            bool : True if version is verified by this request
        """

        if self._version is None:
            return True
        else:
            possibilities_dict = {
                '>': (1,),
                '<': (-1,),
                '=': (0,),
                '==': (0,),
                '>=': (0, 1),
                '<=': (-1, 0)
            }
            possibilities = possibilities_dict[self._version_operator]
            if not isinstance(version,tuple):
                version = PackageVersion(version)
            if self._version[1] is None:
                # omit packaging in comparison
                cmp_res = cmp(version[0],self._version[0])
            else:
                cmp_res = cmp(version,self._version)
            return cmp_res in possibilities

    @property
    def package(self):
        return self._package or None

    @package.setter
    def package(self,value):
        if value:
            self._package = value
        else:
            self._package = None

    @property
    def architectures(self):
        """List of accepted architecturs"""
        return self._architectures

    @architectures.setter
    def architectures(self,value):
        if value in ('all','',None):
            self._architectures = None
        else:
            self._architectures = ensure_list(value)

    @property
    def maturities(self):
        """List of accepted maturities"""
        return self._maturities

    @maturities.setter
    def maturities(self,value):
        """List of accepted maturities"""
        if value in ('all','',None):
            self._maturities = None
        else:
            self._maturities = ensure_list(value,allow_none=True)

    @property
    def locales(self):
        return self._locales

    @locales.setter
    def locales(self,value):
        if value in ('all','',None):
            self._locales = None
        else:
            self._locales = ensure_list(value)

    def is_matched_by(self,package_entry):
        """Check if package_entry is matching this request"""
        return  (
                (self.package is None or package_entry.package == self.package) and
                self._is_matched_version(package_entry.version) and
                (self.min_os_version is None or not package_entry.max_os_version or Version(package_entry.max_os_version)>=self.min_os_version) and
                (self.max_os_version is None or not package_entry.min_os_version or Version(package_entry.min_os_version)<=self.max_os_version) and
                (self.architectures is None or package_entry.architecture in ('','all') or package_entry.architecture in self.architectures) and
                (self.locales is None or package_entry.locale in ('','all') or len(list_intersection(ensure_list(package_entry.locale),self.locales))>0) and
                (self.maturities is None or (package_entry.maturity == '' and  (self.maturities is None or 'PROD' in self.maturities)) or package_entry.maturity in self.maturities))

    def __cmp__(self,other):
        if isinstance(other,str) or isinstance(other,unicode):
            other = PackageRequest(request=other)

        if isinstance(other,PackageRequest):
            return cmp((self.package,self.version,self.architectures,self.locales,self.maturities),(other.package,other.version,other.architectures,other.locales,other.maturities))
        elif isinstance(other,PackageEntry):
            if self.is_matched_by(other):
                return 0
            else:
                return cmp((self.package,self.version,self.architectures,self.locales,self.maturities),(other.package,other.version,other.architecture,other.locale,other.maturity))
        else:
            raise Exception('Unsupported comparison between PackageRequest and %s' % other)

    def __repr__(self):
        def or_list(v):
            if isinstance(v,list) or isinstance(v,tuple):
                return u'|'.join(ensure_list(v))
            else:
                return v
        attribs=[]
        attribs.extend(["%s=%s" % (a,repr(getattr(self,a))) for a in self._attributes if getattr(self,a) is not None and getattr(self,a) != '' and getattr(self,a) != 'all'])
        attribs = ','.join(attribs)
        return "PackageRequest(%s)" % attribs


    def __unicode__(self):
        def or_list(v):
            if isinstance(v,list) or isinstance(v,tuple):
                return u','.join(ensure_list(v))
            else:
                return v
        attribs=[]
        attribs.extend([u"%s" % (ensure_unicode(or_list(getattr(self,a)))) for a in ['architectures','locales','maturities']
                                                if getattr(self,a) is not None and getattr(self,a) != '' and getattr(self,a) != 'all'])
        if attribs:
            attribs = u' [%s]' % u'_'.join(attribs)
        return "%s%s" % (self.request,attribs)


    def compare_packages(self,pe1,pe2):
        """Compare packages taken in account the preferences from filter
        """
        def _safe_rev_index(alist,avalue):
            if avalue in ('','all'):
                return -1000
            elif alist and avalue in alist:
                return -alist.index(avalue)
            elif alist is None:
                return avalue
            else:
                return -10000

        t1 = (
            pe1.package,
            (self.version is None and '') or PackageVersion(pe1.version),
            _safe_rev_index(self.architectures,pe1.architecture),
            _safe_rev_index(self.locales,pe1.locale),
            _safe_rev_index(self.maturities,pe1.maturity),
           )

        t2 = (
            pe2.package,
            (self.version is None and '') or PackageVersion(pe2.version),
            _safe_rev_index(self.architectures,pe2.architecture),
            _safe_rev_index(self.locales,pe2.locale),
            _safe_rev_index(self.maturities,pe2.maturity),
           )

        return cmp(t1,t2)

    def __iter__(self):
        for key in self._attributes:
            yield (key, getattr(self,key))


def control_to_dict(control,int_params=('size','installed_size')):
    """Convert a control file like object
    key1: value1
    key2: value2
    ...
    list of lines into a dict

    Multilines strings begins with a space

    Breaks when an empty line is reached (limit between 2 package in Packages indexes)

    Args:
        control (file,str or list): file like object to read control from (until an empty line is reached)
        int_params (list): attributes which must be converted to int

    Returns:
        dict
    """
    result = {}
    (key,value) = ('','')
    linenr = 0

    if isinstance(control,(unicode,str)):
        control = control.splitlines()

    while 1:
        if isinstance(control,list):
            if linenr>=len(control):
                line = None
            else:
                line = control[linenr]
            if not line or not line.strip():
                break
        else:
            line = control.readline()
            if not line or not line.strip():
                break

        if line.startswith(' '):
            # additional lines begin with a space!
            value = result[key]
            value += '\n'
            value += line.strip()
            result[key] = value
        else:
            sc = line.find(':')
            if sc<0:
                raise EWaptBadControl(u'Invalid line (no ":" found) : %s' % line)
            (key,value) = (line[:sc].strip(),line[sc+1:].strip())
            key = key.lower()
            if key in int_params:
                try:
                    value = int(value)
                except:
                    pass
            result[key] = value
        linenr += 1

    return result



class PackageEntry(BaseObjectClass):
    """Manage package attributes coming from either control files in WAPT package, local DB, or developement dir.

    Methods to build, unzip, sign or check a package.
    Methods to sign the control attributes and check them.

    >>> pe = PackageEntry('testgroup','0')
    >>> pe.depends = 'tis-7zip'
    >>> pe.section = 'group'
    >>> pe.description = 'A test package'
    >>> print(pe.ascontrol())
    package           : testgroup
    version           : 0
    architecture      : all
    section           : group
    priority          : optional
    maintainer        :
    description       : A test package
    depends           : tis-7zip
    conflicts         :
    maturity          :
    locale            :
    min_os_version    :
    max_os_version    :
    min_wapt_version  :
    sources           :
    installed_size    :
    signer            :
    signer_fingerprint:
    signature_date    :
    signed_attributes :

    >>>
    """
    # minim attributes for a valid control file
    required_attributes = ['package','version','architecture','section','priority']
    optional_attributes = ['maintainer','description','depends','conflicts','maturity',
        'locale','target_os','min_os_version','max_os_version','min_wapt_version',
        'sources','installed_size','impacted_process','description_fr','description_pl','description_de','description_es','audit_schedule',
        'editor','keywords','licence','homepage','package_uuid']
    # attributes which are added by _sign_control
    signature_attributes = ['signer','signer_fingerprint','signature','signature_date','signed_attributes']

    # these attrbutes are not written to Package control file, but only in Packages repository index
    non_control_attributes = ['localpath','sourcespath','filename','size','repo_url','md5sum','repo']

    # these attributes are not kept when duplicating / editing a package
    not_duplicated_attributes =  signature_attributes

    # there files are not included in manifest file
    manifest_filename_excludes = ['WAPT/signature','WAPT/signature.sha256','WAPT/manifest.sha256','WAPT/manifest.sha1']

    _calculated_attributes = []

    @property
    def all_attributes(self):
        return self.required_attributes + self.optional_attributes + self.signature_attributes + self.non_control_attributes + self._calculated_attributes

    def get_default_signed_attributes(self):
         all = self.required_attributes+self.optional_attributes+self.signature_attributes
         all.remove('signature')
         return all

    def __init__(self,package='',version='0',repo='',waptfile=None, section = 'base',_default_md = 'sha256',**kwargs):
        """Initialize a Package entry with either attributes or an existing package file or directory.

        Args:
            waptfile (str): path to wapt zipped file or wapt development directory.

            package (str) : package name
            version (str) : package version
            section (str): Type of package
                                base : any standard software install or configuration package with setup.py python code
                                restricted : same as base but is hidden by default in self service
                                group : group of packages, without setup.py. Only WAPT/control file.
                                host : host package without setup.py. Only WAPT/control file.
                                unit : AD Organizational unit package. Only WAPT/control file
                                profile : AD Group package. Only WAPT/control file
                                wsus : WAPT WUA Windows updates rules package with WAPT/control and WAPT/waptwua_rules.json file.
            _default_md (str) : sh256 or sha1. hash function for signatures
            any control attribute (str): initialize matching attribute

        Returns:
            None
        """
        # temporary attributes added by join queries from local Wapt database
        self._calculated_attributes=[]
        self._package_content = None
        self._control_updated = None

        # init package attributes
        for key in self.required_attributes:
            setattr(self,key,'')

        for key in self.optional_attributes:
            setattr(self,key,'')

        self.package=package
        self.version=version
        self.architecture='all'
        self.section=section
        self.priority='optional'

        self.maintainer=''
        self.description=''
        self.depends=''
        self.conflicts=''
        self.sources=''
        self.filename=''
        self.size=None
        self.maturity=''

        self.signer=None
        self.signer_fingerprint=None
        self.signature=None
        self.signature_date=None
        self.signed_attributes=None

        self.locale=''
        self.target_os=''
        self.min_os_version=''
        self.max_os_version=''
        self.min_wapt_version=''
        self.installed_size=''

        self.audit_schedule=''

        self.impacted_process=''
        self.keywords=''
        self.editor=''
        self.licence=''

        self.homepage = ''
        self.package_uuid = ''

        self.md5sum=''
        self.repo_url=''
        self.repo=repo

        # directory if unzipped package files
        self.sourcespath=None

        # full filename of package if built
        self.localpath=None
        self._control_updated = False

        if waptfile:
            if os.path.isfile(waptfile):
                self.load_control_from_wapt(waptfile)
            elif os.path.isdir(waptfile):
                self.load_control_from_wapt(waptfile)
            else:
                raise EWaptBadControl(u'Package filename or directory %s does not exist' % waptfile)

        self._default_md = _default_md
        self._md = None


        if kwargs:
            for key,value in kwargs.iteritems():
                if key in self.required_attributes + self.optional_attributes + self.non_control_attributes:
                    setattr(self,key,value)

    def as_key(self):
        return dict(
            package=self.package,
            version=self.version,
            architecture=self.architecture,
            locale=self.locale if (self.locale is not None and self.locale != '' and self.locale != 'all') else '',
            maturity=self.maturity if (self.maturity is not None and self.maturity != '' and self.maturity != 'all') else '',
            )

    def make_uuid(self):
        self.package_uuid = str(uuid.uuid4())
        return self.package_uuid

    def as_package_request(self):
        return PackageRequest(
            package = self.package,
            version=self.version,
            architecture=self.architecture,
            locale=self.locale,
            maturity=self.maturity,
            )

    def parse_version(self):
        """Parse version to major, minor, patch, pre-release, build parts.

        """
        return parse_major_minor_patch_build(self.version)

    def __getitem__(self,name):
        if name is str or name is unicode:
            name = name.lower()
        if hasattr(self,name):
            return getattr(self,name)
        else:
            raise Exception(u'No such attribute : %s' % name)

    def __iter__(self):
        for key in self.all_attributes:
            if not key.startswith('_') or key == '_localized_description':
                yield (key, getattr(self,key))

    def as_dict(self):
        return dict(self)

    """
    def __unicode__(self):
        return self.ascontrol(with_non_control_attributes=True)

    def __str__(self):
        return self.__unicode__()
    """

    def __repr__(self):
        return "PackageEntry(%s,%s %s)" % (repr(self.package),repr(self.version),
            ','.join(["%s=%s"%(key,repr(getattr(self,key))) for key in ('architecture','maturity','locale') if (getattr(self,key) is not None and getattr(self,key) != '' and getattr(self,key) != 'all')]))

    def get(self,name,default=None):
        """Get PackageEntry property.

        Args:
            name (str): property to get. name is forced to lowercase.
            default (any) : value to return in case the property doesn't not exist.

        Returns:
            any : property value
        """
        if name is str or name is unicode:
            name = name.lower()
        if hasattr(self,name):
            return getattr(self,name)
        else:
            return default

    def get_localized_description(self,locale_code=None):
        """locale_code is a 2 chars code like fr or en or de"""
        if locale_code is None:
            return self.description
        else:
            if hasattr(self,'description_%s'%locale_code):
                desc = getattr(self,'description_%s' % locale_code)
                if desc is not None and desc != '':
                    return desc
                else:
                    return self.description
            else:
                return self.description


    def __setitem__(self,name,value):
        """attribute which are not member of all_attributes list are considered _calculated

        >>> p = PackageEntry('test')
        >>> print p._calculated_attributes
        []
        >>> p.install_date = u'2017-06-09 12:00:00'
        >>> print p._calculated_attributes
        []
        """
        setattr(self,name,value)

    def __setattr__(self,name,value):
        if name is str or name is unicode:
            name = name.lower()
        if name not in self.all_attributes:
            self._calculated_attributes.append(name)
        if name in self.required_attributes+self.optional_attributes and self._control_updated is not None and value != getattr(self,name):
            self._control_updated = True
        super(PackageEntry,self).__setattr__(name,value)

    def __len__(self):
        return len(self.all_attributes)

    def __cmp__(self,entry_or_version):
        def nat_cmp(a, b):
            a, b = a or '', b or ''

            def convert(text):
                if text.isdigit():
                    return int(text)
                else:
                    return text.lower()
            alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
            return cmp(alphanum_key(a), alphanum_key(b))

        def compare_by_keys(d1, d2):
            for key in ['major', 'minor', 'patch','subpatch']:
                i1,i2  = d1.get(key), d2.get(key)
                # compare to partial version number (kind of wilcard)
                if i1 is not None and i2 is None and not isinstance(entry_or_version,PackageEntry):
                    if d2.get('packaging') is None:
                        return 0
                    else:
                        # assume None is 0 and compare packaging
                        i2=0
                if i1 is None and i2 is not None and not isinstance(entry_or_version,PackageEntry):
                    i1 = 0
                v = cmp(i1,i2)
                if v:
                    return v
            # package version
            pv1, pv2 = d1.get('packaging'), d2.get('packaging')
            # compare to partial version number (kind of wilcard)
            if pv1 is not None and pv2 is None and not isinstance(entry_or_version,PackageEntry):
                return 0
            else:
                pvcmp = nat_cmp(pv1, pv2)
                return pvcmp or 0
        try:
            if isinstance(entry_or_version,PackageEntry):
                result = cmp(self.package,entry_or_version.package)
                if result == 0:
                    v1, v2 = self.parse_version(), entry_or_version.parse_version()
                    result = compare_by_keys(v1, v2)
                    if result == 0:
                        # when migrating from <1.5.1.21, maturity is None...
                        result = cmp(self.maturity or '',entry_or_version.maturity or '')
                    return result
                else:
                    return result
            else:
                v1, v2 = self.parse_version(), parse_major_minor_patch_build(entry_or_version)
                return compare_by_keys(v1, v2)
        except ValueError as e:
            logger.warning("%s" % e)
            if isinstance(entry_or_version,PackageEntry):
                return cmp((self.package,self.version),(entry_or_version.package,entry_or_version.version))
            else:
                return cmp(self.version,entry_or_version)

    def match(self, match_expr):
        """Return True if package entry match a package string like 'tis-package (>=1.0.1-00)

        """
        if isinstance(match_expr,PackageRequest):
            return match_expr.is_matched_by(self)
        elif isinstance(match_expr,(str,unicode)):
            pcv = REGEX_PACKAGE_CONDITION.match(match_expr).groupdict()
            if pcv['package'] != self.package:
                return False
            else:
                if 'operator' in pcv and pcv['operator']:
                    return self.match_version(pcv['operator']+pcv['version'])
                else:
                    return True
        else:
            raise Exception(u'Unsupported match operand %s' % match_expr)

    def match_version(self, version_expr):
        """Return True if package entry match a version string condition like '>=1.0.1-00'

        """
        prefix = version_expr[:2]
        if prefix in ('>=', '<=', '=='):
            match_version = version_expr[2:]
        elif prefix and prefix[0] in ('>', '<', '='):
            prefix = prefix[0]
            match_version = version_expr[1:]
        else:
            raise ValueError(u"version_expr parameter should be in format <op><ver>, "
                             "where <op> is one of ['<', '>', '==', '<=', '>=']. "
                             "You provided: %r" % version_expr)

        possibilities_dict = {
            '>': (1,),
            '<': (-1,),
            '=': (0,),
            '==': (0,),
            '>=': (0, 1),
            '<=': (-1, 0)
        }

        possibilities = possibilities_dict[prefix]
        cmp_res = self.__cmp__(match_version)

        return cmp_res in possibilities

    def match_search(self,search):
        """Check if entry match search words

        Args:
            search (str): words to search for separated by spaces

        Returns:
            boolean: True if entry contains the words in search in correct order and at word boundaries
        """
        if not search:
            return True
        else:
            found = re.search(r'\b{}'.format(search.replace(' ',r'.*\b')),u'%s %s' % (self.package,self.description),re.IGNORECASE)
            return found is not None


    def load_control_from_dict(self,adict):
        """Fill in members of entry with keys from supplied dict

        adict members which are not a registered control attribute are set too
        and attribute name is put in list of "calculated" attributes.

        Args:
            adict (dict): key,value to put in this entry

        Returns:
            PackageEntry: self
        """
        for k in adict:
            setattr(self,k,adict[k])
            if not k in self.all_attributes:
                self._calculated_attributes.append(k)
        return self

    def _load_control(self,control_text):
        self.load_control_from_dict(control_to_dict(control_text))
        self._control_updated = False

    def load_control_from_wapt(self,fname=None,calc_md5=True):
        """Load package attributes from the control file (utf8 encoded) included in WAPT zipfile fname

        Args:
            fname (str or unicode): Package file/directory path
                                    If None, try to load entry attributes from self.sourcespath or self.localpath
                                    If fname is a file path, it must be Wapt zipped file, and try to load control data from it
                                    If fname is a directory path, it must be root dir of unzipped package file and load control from <fname>/WAPT/control

            calc_md5 (boolean): if True and fname is a zipped file, initialize md5sum attribute with md5 sum of Zipped file/

        Returns:
            PackageEntry: self

        """
        if fname is None:
            if self.sourcespath and os.path.isdir(self.sourcespath):
                fname = self.sourcespath
            elif self.localpath and os.path.isfile(self.localpath):
                fname = self.localpath

        if os.path.isfile(fname):
            with zipfile.ZipFile(fname,'r',allowZip64=True) as waptzip:
                control = waptzip.open(u'WAPT/control').read().decode('utf8')
        elif os.path.isdir(fname):
            try:
                with codecs.open(os.path.join(fname,'WAPT','control'),'r',encoding='utf8') as control_file:
                    control = control_file.read()
            except Exception as e:
                raise EWaptBadControl(e)
        else:
            raise EWaptBadControl(u'Bad or no control found for %s' % (fname,))

        self._load_control(control)

        self.filename = self.make_package_filename()
        self.localpath = ''

        if os.path.isfile(fname):
            if calc_md5:
                self.md5sum = md5_for_file(fname)
            else:
                self.md5sum = ''
            self.size = os.path.getsize(fname)
            self.filename = os.path.basename(fname)
            self.localpath = os.path.abspath(fname)
        elif os.path.isdir(fname):
            self.filename = None
            self.localpath = None
            self.sourcespath = os.path.abspath(fname)
        return self

    def save_control_to_wapt(self,fname=None,force=True):
        """Save package attributes to the control file (utf8 encoded)

        Update self.locapath or self.sourcespath if not already set.

        Args:
            fname (str) : base directoy of waptpackage or filepath of Zipped Packges.
                          If None, use self.sourcespath if exists, or self.localpath if exists

            force (bool) : write control in wapt zip file even if it already exist
        Returns:
            PackageEntry : None if nothing written, or previous PackageEntry if new data written

        Raises:
            Exception: if fname is None and no sourcespath and no localpath
            Exception: if control exists and force is False

        """
        if fname is None:
            if self.sourcespath and os.path.isdir(self.sourcespath):
                fname = self.sourcespath
            elif self.localpath and os.path.isfile(self.localpath):
                fname = self.localpath

        if fname is None:
            raise Exception('Needs a wapt package directory root or WaptPackage filename to save control to')

        fname = os.path.abspath(fname)

        try:
            old_control = PackageEntry(waptfile = fname)
        except EWaptBadControl:
            old_control = None

        # wether data is different
        write_needed = not old_control or (old_control.ascontrol() != self.ascontrol())

        if not force and old_control and write_needed:
            raise Exception(u'control file already exist in WAPT file %s' % fname)

        if write_needed:
            if os.path.isdir(fname):
                if not os.path.isdir(os.path.join(fname,'WAPT')):
                    os.makedirs(os.path.join(fname,'WAPT'))
                with codecs.open(os.path.join(fname,u'WAPT','control'),'w',encoding='utf8') as control_file:
                    control_file.write(self.ascontrol())
                if not self.sourcespath:
                    self.sourcespath = fname
                return old_control
            else:
                waptzip = zipfile.ZipFile(fname,'a',allowZip64=True,compression=zipfile.ZIP_DEFLATED)
                try:
                    try:
                        previous_zi = waptzip.getinfo(u'WAPT/control')
                        waptzip.remove(u'WAPT/control')
                    except Exception as e:
                        logger.debug(u"OK %s" % repr(e))
                    waptzip.writestr(u'WAPT/control',self.ascontrol().encode('utf8'))
                    if not self.localpath:
                        self.localpath = fname
                    return old_control
                finally:
                    if waptzip:
                        waptzip.close()
            self._control_updated = False
        else:
            self._control_updated = False
            return None

    def ascontrol(self,with_non_control_attributes = False,with_empty_attributes=False):
        """Return control attributes and values as stored in control packages file

        Each attribute on a line with key : value
        If value is multiline, new line begin with a space.

        Args:
            with_non_control_attributes (bool) : weither to include all attributes or only those
                                                 relevant for final package content.

            with_empty_attributes (bool) : weither to include attribute with empty value too or only
                                           non empty and/or signed attributes
        Returns:
            str: lines of attr: value
        """
        val = []

        def escape_cr(s):
            # format multi-lines description with a space at each line start
            # format list as csv
            if s and (isinstance(s,str) or isinstance(s,unicode)):
                return re.sub(r'$(\n)(?=^\S)',r'\n ',s,flags=re.MULTILINE)
            elif isinstance(s,list):
                return ','.join([ensure_unicode(item) for item in s])
            else:
                if s is None:
                    return ''
                else:
                    return s

        for att in self.required_attributes+self.optional_attributes+self.signature_attributes:
            # we add to the control file all signed attributes, the non empty ones, and all the other if required
            if att in self.get_default_signed_attributes() or with_empty_attributes or getattr(self,att):
                val.append(u"%-18s: %s" % (att, escape_cr(getattr(self,att))))

        if with_non_control_attributes:
            for att in self.non_control_attributes:
                if getattr(self,att):
                    val.append(u"%-18s: %s" % (att, escape_cr(getattr(self,att))))
        return u'\n'.join(val)

    def make_package_filename(self):
        """Return the standard package filename based on current attributes
        parts of control which are either 'all' or empty are not included in filename

        Returns:
            str:  standard package filename
                  - packagename.wapt for host
                  - packagename_arch_maturity_locale.wapt for group
                  - packagename_version_arch_maturity_locale.wapt for others
        """
        if self.section not in ['host','group','unit'] and not (self.package and self.version and self.architecture):
            raise Exception(u'Not enough information to build the package filename for %s (%s)'%(self.package,self.version))

        if self.section == 'host':
            return self.package+'.wapt'
        elif self.section in ('group'):
            # we don't keep version for group
            att = u'_'.join([f for f in (self.architecture,self.maturity,'-'.join(ensure_list(self.locale))) if (f and f != 'all')])
            if att:
                att = '_'+att
            return self.package+'_'+self.version+att+'.wapt'
        elif self.section in ('unit','profile'):
            # we have to hash the name.
            return hashlib.md5(self.package).hexdigest()+ u'_'.join([f for f in (self.architecture,self.maturity,u'-'.join(ensure_list(self.locale))) if (f and f != 'all')]) + '.wapt'
        else:
            # includes only non empty fields
            att= u'_'.join([f for f in (self.architecture,self.maturity,'-'.join(ensure_list(self.locale))) if f])
            if att:
                att = '_'+att
            return self.package+'_'+self.version+att+'.wapt'

    def make_package_edit_directory(self):
        """Return the standard package directory to edit the package based on current attributes

        Returns:
            str:  standard package filename
                  - packagename_arch_maturity_locale-wapt for softwares and groups
                  - packagename-wapt for host.
        """
        if not (self.package):
            raise Exception(u'Not enough information to build the package directory for %s'%(self.package))
            # includes only non empty fields
        return u'_'.join([f for f in (self.package,self.architecture,self.maturity.replace(',','-'),self.locale.replace(',','-')) if (f and f != 'all')]) + '-wapt'

    def asrequirement(self):
        """Return package and version for designing this package in depends or install actions

        Returns:
            str: "packagename (=version)"
        """
        return u"%s(=%s)" % (self.package,self.version)

    @property
    def download_url(self):
        """Calculate and return the download URL for this entry

        """
        if self.repo_url:
            if self.filename:
                return self.repo_url+'/'+self.filename.strip('./')
            else:
                # fallback
                return self.repo_url+'/'+self.make_package_filename()

        else:
            return None

    def inc_build(self):
        """Increment last number part of version in memory"""
        version_parts = self.parse_version()
        for part in ('packaging','subpatch','patch','minor','major'):
            if part in version_parts and version_parts[part] != None and\
                (isinstance(version_parts[part],int) or version_parts[part].isdigit()):
                version_parts[part] = "%i" % (int(version_parts[part])+1,)
                self.version = make_version(version_parts)
                return
        raise EWaptBadControl(u'no build/packaging part in version number %s' % self.version)


    def build_management_package(self,target_directory=None):
        """Build the WAPT package from attributes only, without setup.py
        stores the result in target_directory.

        self.sourcespath must be None.
        Package will contain only a control file.

        Args:
            target_directory (str): where to create Zip wapt file.
                                    if None, temp dir will be used.

        Returns:
            str: path to zipped Wapt file. It is unsigned.

        >>> pe = PackageEntry('testgroup','0',description='Test package',maintainer='Hubert',sources='https://dev/svn/testgroup',architecture='x86')
        >>> waptfn = pe.build_management_package()
        >>> key = SSLPrivateKey('c:/private/htouvet.pem',password='monmotdepasse')
        >>> crt = SSLCertificate('c:/private/htouvet.crt')
        >>> pe.sign_package(crt,key)
        'qrUUQeNJ3RSSeXQERrP9vD7H/Hfvw8kmBXZvczq0b2PVRKPdjMCElYKzryAbQ+2nYVDWAGSGrXxs\ny2WzhOhrdMfGfcy6YLaY5muApoArBn3CjKP5G6HypOGD5agznLEKkcUg5/Y3aIR8bL55Ylmp3RaS\nWKnezUcuA2yuNuKwHsXr9CdihK5pRyYrm5KhCNy8S7+kAJvayrUj5q8ur6z0nNMQCHEnWGN+V3MI\n84PymR1eXsuauKeYNqIESWCyyD/lFZv0JEYfrfml8rirC6yd6iTJW0OqH7gKwCEl03JpRaF91vWB\nOXN65S1j2oV8Jgjq43oa7lyywKC01a/ehQF5Jw==\n'
        >>> pe.unzip_package()
        'c:\\users\\htouvet\\appdata\\local\\temp\\waptob4gcd'
        >>> ca = SSLCABundle('c:/wapt/ssl')
        >>> pe.check_control_signature(ca)
        <SSLCertificate cn=u'htouvet' issuer=u'tranquilit-ca-test' validity=2017-06-28 - 2027-06-26 Code-Signing=True CA=True>
        """

        result_filename = u''
        # some checks
        if self.sourcespath:
            raise Exception('Package must not have local sources')

        # check version syntax
        parse_major_minor_patch_build(self.version)

        # check architecture
        if not self.architecture in ArchitecturesList:
            raise EWaptBadControl(u'Architecture should one of %s' % (ArchitecturesList,))

        self.filename = self.make_package_filename()

        control_data = self.ascontrol()

        if target_directory is None:
            target_directory = tempfile.gettempdir()

        if not os.path.isdir(target_directory):
            raise Exception(u'Bad target directory %s for package build' % target_directory)

        result_filename = os.path.abspath(os.path.join(target_directory,self.filename))

        if os.path.isfile(result_filename):
            logger.warning(u'Target package already exists, removing %s' % result_filename)
            os.unlink(result_filename)

        self.localpath = result_filename
        with ZipFile(result_filename,'w',allowZip64=True,compression=zipfile.ZIP_DEFLATED) as wapt_zip:
            wapt_zip.writestr('WAPT/control',control_data.encode('utf8'))
        return result_filename


    def build_package(self,excludes=['.svn','.git','.gitignore','setup.pyc'],target_directory=None):
        """Build the WAPT package, stores the result in target_directory
        Zip the content of self.sourcespath directory into a zipfile
        named with default package filename based on control attributes.

        Update filename attribute.
        Update localpath attribute with result filepath.

        Args:
            excludes (list) : list of patterns for source files to exclude from built package.
            target_directory (str): target directory where to store built package.
                                 If None, use parent directory of package sources dircetory.

        Returns:
            str: full filepath to built wapt package
        """

        result_filename = u''

        # some checks
        if not self.sourcespath:
            raise EWaptNotSourcesDirPackage(u'Error building package : There is no WAPT directory in %s' % self.sourcespath)

        if not os.path.isdir(os.path.join(self.sourcespath,'WAPT')):
            raise EWaptNotSourcesDirPackage(u'Error building package : There is no WAPT directory in %s' % self.sourcespath)

        control_filename = os.path.join(self.sourcespath,'WAPT','control')
        if not os.path.isfile(control_filename):
            raise EWaptNotSourcesDirPackage(u'Error building package : There is no control file in WAPT directory')

        force_utf8_no_bom(control_filename)

        # check version syntax
        parse_major_minor_patch_build(self.version)

        # check architecture
        if not self.architecture in ArchitecturesList:
            raise EWaptBadControl(u'Architecture should one of %s' % (ArchitecturesList,))

        self.filename = self.make_package_filename()

        logger.debug(u'Control data : \n%s' % self.ascontrol())
        if target_directory is None:
            target_directory = os.path.abspath(os.path.join(self.sourcespath,'..'))

        if not os.path.isdir(target_directory):
            raise Exception(u'Bad target directory %s for package build' % target_directory)

        result_filename = ensure_unicode(os.path.abspath(os.path.join(target_directory,self.filename)))
        if os.path.isfile(result_filename):
            logger.warning(u'Target package already exists, removing %s' % result_filename)
            os.unlink(result_filename)

        self.localpath = result_filename

        allfiles = create_recursive_zip(
            zipfn = result_filename,
            source_root = ensure_unicode(self.sourcespath),
            target_root = u'' ,
            excludes=excludes)

        self._invalidate_package_content()
        return result_filename

    def _invalidate_package_content(self):
        """Remove the _package_content for host packages

        """
        if hasattr(self,'_package_content'):
            self._package_content = None

    def _signed_content(self):
        """Return the signed control informations"""
        # workaround for migration
        if not self.signed_attributes and self.signature_date < '20170609':
            logger.warning(u'Package %s has old control signature style, some attributes are not checked. Please re-sign package' % (self.localpath or self.sourcespath or self.asrequirement()))
            effective_signed_attributes = ['package','version','architecture','section','priority','depends','conflicts','maturity']
        else:
            effective_signed_attributes = self.signed_attributes
        return {att:getattr(self,att,None) for att in ensure_list(effective_signed_attributes)}

    def _sign_control(self,private_key,certificate,keep_signature_date=False):
        """Sign the contractual attributes of the control file using
        the provided key, add certificate Fingerprint and CN too

        Args:
            private_key (SSLPrivateKey)
            certificate (SSLCertificate)

        Returns:
            list: signed attributes
        """
        self.make_uuid()
        self.signed_attributes = ','.join(self.get_default_signed_attributes())
        if not keep_signature_date or not self.signature_date:
            self.signature_date = datetime2isodate()
        self.signer = certificate.cn
        self.signer_fingerprint = certificate.fingerprint
        self.signature = base64.b64encode(
            private_key.sign_content(self._signed_content(),self._md or self._default_md))
        return self.get_default_signed_attributes()

    def check_control_signature(self,trusted_bundle,signers_bundle=None):
        """Check in memory control signature against a list of public certificates

        Args:
            trusted_bundle (SSLCABundle): Trusted certificates. : packages certificates must be signed by a one of this bundle.
            signers_bundle : Optional. List of potential packages signers certificates chains.
                             When checking Packages index, actual
                             packages are not available, only certificates embedded in Packages index.
                             Package signature are checked againt these certificates
                             looking here for potential intermediate CA too.
                             and matching certificate is checked against trusted_bundle.

        Returns:
            SSLCertificate : matching trusted package's signers SSLCertificate

        >>> from waptpackage import *
        >>> from common import SSLPrivateKey,SSLCertificate
        >>> k = SSLPrivateKey('c:/private/test.pem')
        >>> c = SSLCertificate('c:/private/test.crt')

        >>> p = PackageEntry('test',version='1.0-0')
        >>> p.depends = 'test'
        >>> p._sign_control(k,c)
        >>> p.check_control_signature(c)

        >>> p.check_control_signature(SSLCABundle('c:/wapt/ssl'))

        """
        if not self.signature:
            raise EWaptNotSigned(u'Package control %s on repo %s is not signed' % (self.asrequirement(),self.repo))
        assert(isinstance(trusted_bundle,SSLCABundle))

        certs = self.package_certificates()
        if certs is None and signers_bundle is not None:
            certs = signers_bundle.certificate_chain(fingerprint = self.signer_fingerprint)
        if not certs and trusted_bundle:
            certs = trusted_bundle.certificate_chain(fingerprint = self.signer_fingerprint)
        if not certs:
            raise EWaptMissingCertificate(u'Control %s data has no matching certificate in Packages index or Package, please rescan your Packages index.' % self.asrequirement())

        #append trusted to ca

        issued_by = trusted_bundle.check_certificates_chain(certs)[-1]
        #logger.debug('Certificate %s is trusted by root CA %s' % (cert.subject,issued_by.subject))

        signed_content = self._signed_content()
        signature_raw = self.signature.decode('base64')
        if certs[0].verify_content(signed_content,signature_raw,md=self._default_md):
            self._md = self._default_md
            return certs[0]

        raise SSLVerifyException(u'SSL signature verification failed for control %s against embedded certificate %s' % (self.asrequirement(),certs[0].cn))

    def has_file(self,fname):
        """Return None if fname is not in package, else return file datetime

        Args:
            fname (unicode): file path like WAPT/signature

        Returns:
            datetime : last modification datetime of file in Wapt archive if zipped or local sources if unzipped
        """
        if self.localpath or self._package_content is not None:
            try:
                with self.as_zipfile() as waptzip:
                    return datetime.datetime(*waptzip.getinfo(fname).date_time)
            except KeyError as e:
                return None
        elif self.sourcespath and os.path.isdir(self.sourcespath) and os.path.isfile(os.path.join(self.sourcespath,fname)):
            # unzipped sources
            fpath = os.path.abspath(os.path.join(self.sourcespath,fname))
            return datetime.datetime.fromtimestamp(os.stat(fpath).st_mtime)
        else:
            # package is not yet built/signed.
            return None


    def package_certificates(self):
        """Return certificates from package. If package is built, take it from Zip
        else take the certificates from unzipped directory

        Returns:
            list: list of embedded certificates when package was signed or None if not provided or signed.
                    First one of the list is the signer, the others are optional intermediate CA
        """
        if self.localpath and os.path.isfile(self.localpath):
            try:
                with ZipFile(self.localpath,allowZip64=True) as zip:
                    cert_pem = zip.read('WAPT/certificate.crt')
                certs = SSLCABundle()
                certs.add_certificates_from_pem(cert_pem)
                return certs.certificates()
            except Exception as e:
                logger.warning(u'No certificate found in %s : %s'% (self.localpath,repr(e)))
                return None
        elif self.sourcespath and os.path.isdir(self.sourcespath) and os.path.isfile(os.path.join(self.sourcespath,'WAPT','certificate.crt')):
            # unzipped sources
            certs = SSLCABundle(os.path.join(self.sourcespath,'WAPT','certificate.crt'))
            return certs.certificates()
        else:
            # package is not yet built/signed.
            return None

    def build_manifest(self,exclude_filenames = None,block_size=2**20,forbidden_files=[],md='sha256',waptzip=None):
        """Calc the manifest of an already built (zipped) wapt package

        Returns:
            dict: {filepath:shasum,}
        """
        if not self.localpath:
            raise EWaptMissingLocalWaptFile(u'Wapt package "%s" is not yet built' % self.sourcespath)

        if not os.path.isfile(self.localpath):
            raise EWaptMissingLocalWaptFile(u'%s is not a Wapt package' % self.localpath)

        if exclude_filenames is None:
            exclude_filenames = self.manifest_filename_excludes

        if waptzip is None:
            waptzip = zipfile.ZipFile(self.localpath,'r',allowZip64=True)
            _close_zip = True
        else:
            _close_zip = False

        try:
            manifest = {}
            for fn in waptzip.filelist:
                if not fn.filename in exclude_filenames:
                    if fn.filename in forbidden_files:
                        raise EWaptPackageSignError('File %s is not allowed.'% fn.filename)

                    shasum = hashlib.new(md)

                    file_data = waptzip.open(fn)
                    while True:
                        data = file_data.read(block_size)
                        if not data:
                            break
                        shasum.update(data)
                    shasum.update(data)
                    manifest[fn.filename] = shasum.hexdigest()
            return manifest
        finally:
            if _close_zip:
                waptzip.close()


    def sign_package(self,certificate,private_key=None,password_callback=None,private_key_password=None,mds=['sha256'],keep_signature_date=False):
        """Sign an already built package.
        Should follow immediately the build_package step.

        Append signed control, manifest.sha256 and signature to zip wapt package
        If these files are already in the package, they are first removed.

        Use the self.localpath attribute to get location of waptfile build file.

        Args:
            certificate (SSLCertificate or list): signer certificate chain
            private_key (SSLPrivateKey): signer private key
            password_callback (func) : function to call to get key password if encrypted.
            private_key_password (str): password to use if key is encrypted. Use eithe this or password_callback
            mds (list): list of message digest manifest and signature methods to include. For backward compatibility.

        Returns:
            str: signature

        """
        if not self.localpath or (not os.path.isfile(self.localpath) and not os.path.isdir(self.localpath)):
            raise Exception(u"Path %s is not a Wapt package" % self.localpath)

        if isinstance(certificate,list):
            signer_cert = certificate[0]
            certificate_chain = certificate
        else:
            signer_cert = certificate
            certificate_chain = [certificate]

        cert_chain_str = None

        if private_key is None:
            private_key = signer_cert.matching_key_in_dirs(password_callback = password_callback,private_key_password=private_key_password)

        start_time = time.time()
        package_fn = self.localpath
        logger.debug(u'Signing %s with key %s, and certificate CN "%s"' % (package_fn,private_key,signer_cert.cn))
        # sign the control (one md only, so take default if many)
        if len(mds) == 1:
            self._default_md = mds[0]
        self._sign_control(certificate=signer_cert,private_key=private_key,keep_signature_date=keep_signature_date)

        # control file is appended to manifest file separately.
        control = self.ascontrol().encode('utf8')
        excludes = self.manifest_filename_excludes
        excludes.append('WAPT/control')

        forbidden_files = []
        # removes setup.py
        # if file is in forbidden_files, raise an exception.
        if not signer_cert.is_code_signing:
            forbidden_files.append('setup.py')

        self._invalidate_package_content()

        # clear existing signatures
        with zipfile.ZipFile(self.localpath,'a',allowZip64=True,compression=zipfile.ZIP_DEFLATED) as waptzip:
            filenames = waptzip.namelist()
            for md in hashlib.algorithms:
                if self.get_signature_filename(md) in filenames:
                    waptzip.remove(self.get_signature_filename(md))
                if self.get_manifest_filename(md) in filenames:
                    waptzip.remove(self.get_manifest_filename(md))

            if 'WAPT/control' in filenames:
                waptzip.remove('WAPT/control')
            waptzip.writestr('WAPT/control',control)

            # replace or append signer certificate
            if 'WAPT/certificate.crt' in filenames:
                waptzip.remove('WAPT/certificate.crt')
            cert_chain_str = '\n'.join([cert.as_pem() for cert in certificate_chain])
            waptzip.writestr('WAPT/certificate.crt',cert_chain_str)

            # add manifest and signature for each digest
            for md in mds:
                try:
                    # need read access to ZIP file.
                    manifest_data = self.build_manifest(exclude_filenames = excludes,forbidden_files = forbidden_files,md=md,waptzip=waptzip)
                except EWaptPackageSignError as e:
                    raise EWaptBadCertificate('Certificate %s doesn''t allow to sign packages with setup.py file.' % signer_cert.cn)

                manifest_data['WAPT/control'] = hexdigest_for_data(control,md = md)

                new_cert_hash = hexdigest_for_data(cert_chain_str,md = md)
                if manifest_data.get('WAPT/certificate.crt',None) != new_cert_hash:
                    # need to replace certificate in Wapt package
                    manifest_data['WAPT/certificate.crt'] = new_cert_hash
                else:
                    new_cert_hash = None

                # convert to list of list...
                wapt_manifest = json.dumps( manifest_data.items())

                # sign with default md
                signature = private_key.sign_content(wapt_manifest,md = md)

                waptzip.writestr(self.get_manifest_filename(md=md),wapt_manifest)
                waptzip.writestr(self.get_signature_filename(md),signature.encode('base64'))

        self._md = self._default_md
        mtime = time.mktime(isodate2datetime(self.signature_date).timetuple())
        os.utime(self.localpath,(mtime,mtime))

        return signature.encode('base64')

    def get_manifest_filename(self,md = None):
        if md is None:
            md = self._md or self._default_md
        return 'WAPT/manifest.%s' % md

    def get_signature_filename(self,md = None):
        if md is None:
            md = self._md or self._default_md
        if md == 'sha1':
            return 'WAPT/signature'
        else:
            return 'WAPT/signature.%s' % md

    def _get_package_zip_entry(self,filename):
        """Open wapt zipfile and return one package zipfile entry
        could fail if zip file is already opened elsewhere...

        Returns
            zip
        """
        with zipfile.ZipFile(self.localpath,'r',allowZip64=True) as waptzip:
            try:
                return waptzip.getinfo(filename)
            except:
                return None

    def change_prefix(self,new_prefix):
        """Change prefix of package name to new_prefix and return True if
        it was really changed.
        """
        if '-' in self.package:
            (old_prefix,name) = self.package.split('-',1)
            if old_prefix != new_prefix:
                self.package = '%s-%s' % (new_prefix,name)
                return True
            else:
                return False
        else:
            return False

    def invalidate_signature(self):
        """Remove all signature informations from control and unzipped package directory
        Package must be in unzipped state.
        """
        # remove control signature
        for att in self.signature_attributes:
            if hasattr(self,att):
                setattr(self,att,None)

        # remove package / files signature if sources entry.
        if self.sourcespath and os.path.isdir(self.sourcespath):
            for md in ['sha1','sha256']:
                manifest_filename = os.path.abspath(os.path.join(self.sourcespath,self.get_manifest_filename(md=md)))
                if os.path.isfile(manifest_filename):
                    os.remove(manifest_filename)

                signature_filename = os.path.abspath(os.path.join(self.sourcespath,self.get_signature_filename(md=md)))
                if os.path.isfile(signature_filename):
                    os.remove(signature_filename)

            certificate_filename = os.path.join(self.sourcespath,'WAPT','certificate')
            if os.path.isfile(certificate_filename):
                os.remove(certificate_filename)

        self._invalidate_package_content()

    def list_corrupted_files(self):
        """Check hexdigest sha for the files in manifest.
        Package must be already unzipped.

        Returns:
            list: non matching files (corrupted files)
        """

        if not self.sourcespath:
            raise EWaptNotSourcesDirPackage(u'Package %s (path %s) is not unzipped, checking corrupted files is not supported.' % (self,self.localpath))

        if not os.path.isdir(self.sourcespath):
            raise EWaptNotSourcesDirPackage(u'%s is not a valid package directory.'%self.sourcespath)

        manifest_filename = os.path.join(self.sourcespath,'WAPT','manifest.%s' % self._md )
        if not os.path.isfile(manifest_filename):
            raise EWaptBadSignature(u'no manifest file in %s directory.'%self.sourcespath)

        with open(manifest_filename,'r') as manifest_file:
            manifest = ujson.loads(manifest_file.read())
            if not isinstance(manifest,list):
                raise EWaptBadSignature(u'manifest file in %s is invalid.'%self.sourcespath)

        errors = []
        expected = []

        for (filename,hexdigest) in manifest:
            fullpath = os.path.abspath(os.path.join(self.sourcespath,filename))
            expected.append(fullpath)
            # file was expected but has disapeared...
            if not os.path.isfile(fullpath):
                errors.append(filename)
            elif hexdigest != hexdigest_for_file(fullpath,md = self._md):
                errors.append(filename)

        files = all_files(ensure_unicode(self.sourcespath))
        # removes files which are not in manifest by design
        for fn in self.manifest_filename_excludes:
            full_fn = os.path.abspath(os.path.join(self.sourcespath,fn))
            if full_fn in files:
                files.remove(full_fn)
        # add in errors list files found but not expected...
        errors.extend([ fn for fn in files if fn not in expected])
        return errors


    def has_setup_py(self):
        if not self.sourcespath and not self.localpath and hasattr(self,'setuppy'):
            return self.get('setuppy',None) is not None
        elif self.sourcespath or self.localpath:
            return self.has_file('setup.py')
            raise EWaptBadSetup('Unable to determine if this package has a setup.py file. No sources, no local package and no setuppy attribute')

    def check_package_signature(self,trusted_bundle):
        """Check
        - hash of files in unzipped package_dir with list in package's manifest file
        - try to decrypt manifest signature with package's certificate
        - check that the package certificate is issued by a know CA or the same as one the authorized certitificates.

        Args:
            trusted_bundle (SSLCABundle) : list of authorized certificates / ca filepaths

        Returns:
            SSLCertificate : matching certificate

        Raises:
            Exception if no certificate match is found.
        """
        if not trusted_bundle:
            raise EWaptBadCertificate(u'No supplied trusted_bundle to check package signature')

        if isinstance(trusted_bundle,SSLCertificate):
            cert = trusted_bundle
            trusted_bundle = SSLCABundle()
            trusted_bundle.add_certificates_from_pem(cert.as_pem())

        assert(isinstance(trusted_bundle,SSLCABundle))

        if not self.sourcespath:
            raise EWaptNotSourcesDirPackage(u'Package entry is not an unzipped sources package directory.')

        if not os.path.isdir(self.sourcespath):
            raise EWaptNotAPackage(u'%s is not a valid package directory.'% self.sourcespath)

        manifest_filename = os.path.join(self.sourcespath,self.get_manifest_filename(self._default_md))
        if not os.path.isfile(manifest_filename):
            raise EWaptNotSigned(u'The package %s in %s does not contain the %s file with content fingerprints' % (self.asrequirement(),self.sourcespath,self.get_manifest_filename()))

        verified_by = None
        self._md =  self._default_md

        manifest_data = open(manifest_filename,'r').read()
        manifest_filelist = ujson.loads(manifest_data)

        if self.has_setup_py():
            logger.info(u'Package has a setup.py, code signing certificate is required.')

        signature_filename = os.path.abspath(os.path.join(self.sourcespath,self.get_signature_filename(self._md)))
        if not os.path.isfile(signature_filename):
            raise EWaptNotSigned(u'The package %s in %s does not contain a signature' % (self.asrequirement(),self.sourcespath))

        # first check if signature can be decrypted by any of the public keys
        with open(signature_filename,'r') as signature_file:
            signature = signature_file.read().decode('base64')
        try:
            certs = self.package_certificates()
            if certs:
                issued_by = ', '.join('%s' % ca.cn for ca in trusted_bundle.check_certificates_chain(certs))
                logger.debug(u'Certificate %s is trusted by root CA %s' % (certs[0].subject,issued_by))

            if certs:
                signer_cert = certs[0]
                logger.debug(u'Checking signature with %s' % signer_cert)
                signer_cert.verify_content(manifest_data,signature,md = self._md)
                if not self.has_setup_py() or signer_cert.is_code_signing:
                    logger.debug(u'OK with %s' % signer_cert)
                    verified_by = signer_cert
                else:
                    raise SSLVerifyException(u'Signature OK but not a code signing certificate: %s' % signer_cert)

                logger.info(u'Package issued by %s' % (verified_by.subject,))
            else:
                raise SSLVerifyException(u'No certificate found in the package. Is the package signed with Wapt version prior to 1.5 ?' % signer_cert)

        except Exception as e:
            logger.debug(u'Check_package_signature failed for %s. Signer:%s, trusted_bundle: %s :  %s' % (
                    self.asrequirement(),
                    self.signer,u'\n'.join([u'%s' % cert for cert in trusted_bundle.certificates()]),
                    traceback.format_exc()))
            raise

        # now check the integrity of files
        errors = self.list_corrupted_files()
        if errors:
            raise EWaptCorruptedFiles(u'Error in package %s in %s, files corrupted, SHA not matching for %s' % (self.asrequirement(),self.sourcespath,errors,))
        return verified_by


    def unzip_package(self,target_dir=None,cabundle=None):
        """Unzip package and optionnally check content

        Args:
            target_dir (str): where to unzip package content. If None, a temp dir is created
            cabundle (list) : list of Certificates to check content. If None, no check is done

        Returns:
            str : path to unzipped packages files

        Raises:
            EWaptNotAPackage, EWaptBadSignature,EWaptCorruptedFiles
            if check is not successful, unzipped files are deleted.
        """
        if not self.localpath:
            raise EWaptNotAPackage('unzip_package : Package %s is not downloaded' % ensure_unicode(self))

        if not os.path.isfile(self.localpath):
            raise EWaptNotAPackage('unzip_package : Package %s does not exists' % ensure_unicode(self.localpath))

        if target_dir is not None and not isinstance(target_dir,(unicode,str)):
            raise Exception('Provide a valid directory name to unzip package to')

        if not target_dir:
            target_dir = tempfile.mkdtemp(prefix="wapt")
        else:
            target_dir = os.path.abspath(target_dir)

        logger.info(u'Unzipping package %s to directory %s' % (self.localpath,ensure_unicode(target_dir)))
        with ZipFile(self.localpath,allowZip64=True) as zip:
            try:
                zip.extractall(path=target_dir)
                self.sourcespath = target_dir
                if cabundle is not None:
                    verified_by = self.check_package_signature(cabundle)
                    logger.info(u'Unzipped files verified by certificate %s' % verified_by)
            except Exception as e:
                if os.path.isdir(target_dir):
                    try:
                        shutil.rmtree(target_dir)
                    except Exception as e:
                        logger.critical(u'Unable to remove temporary files %s' % repr(target_dir))
                raise e
        return self.sourcespath

    def delete_localsources(self):
        """Remove the unzipped local directory
        """
        if self.sourcespath and os.path.isdir(self.sourcespath):
            try:
                shutil.rmtree(self.sourcespath)
                self.sourcespath = None
            except Exception as e:
                pass

    def as_zipfile(self,mode='r'):
        """Return a Zipfile for this package for read only operations"""
        if self.localpath and os.path.isfile(self.localpath):
            return ZipFile(self.localpath,compression=zipfile.ZIP_DEFLATED,allowZip64=True,mode=mode)
        elif self._package_content is not None:
            return ZipFile(StringIO.StringIO(self._package_content),mode=mode,compression=zipfile.ZIP_DEFLATED,allowZip64=True)
        else:
            raise EWaptMissingLocalWaptFile('This PackageEntry has no local content for zip operations %s' % self.asrequirement())


    def call_setup_hook(self,hook_name='session_setup',wapt_context=None,params=None,force=None):
        """Calls a hook in setuppy given a wapt_context

        Set basedir, control, and run context within the function context.

        Args:
            hook_name (str): name of function to call in setuppy
            wapt_context (Wapt) : run context

        Returns:
            output of hook.

        Changes:

            1.6.2.1: the called hook is run with Disabled win6432 FileSystem redirection
        """
        setuppy = None

        if self.sourcespath:
            setup_filename = os.path.join(self.sourcespath,'setup.py')
            # PackageEntry from developement or temporary directory with setup.py in a file
            if not os.path.isfile(setup_filename):
                raise EWaptNotAPackage(u'There is no setup.py file in %s, aborting.' % ensure_unicode(self.sourcespath))
            else:
                setuppy = codecs.open(setup_filename,'r',encoding='utf8').read()
        else:
            # PackageEntry from database with stored setup.py as a field
            setuppy = getattr(self,'setuppy',None)
            setup_filename = None

        if setuppy is None:
            if self.localpath:
                # we have a zipped package file, but it is not unzipped in a temporary directory
                raise EWaptBadSetup('Package %s has not been unzipped yet, unable to call %s' % (self.asrequirement(),hook_name))
            else:
                # we have a PackageEntry without setuppy
                raise EWaptBadSetup('No setup.py source for package %s, unable to call %s' % (self.asrequirement(),hook_name))

        # we  record old sys.path as we will include current setup.py
        oldpath = sys.path

        try:
            previous_cwd = os.getcwdu()
            if self.sourcespath :
                os.chdir(self.sourcespath)

            # import the setup module from package file
            logger.info(u"  sourcing setuppy file %s " % ensure_unicode(setup_filename))
            if setup_filename:
                # import code as file to allow debugging.
                setup = import_setup(setup_filename)
            else:
                setup = import_code(setuppy)

            hook_func = getattr(setup,hook_name,None)
            if hook_func is None:
                raise EWaptMissingPackageHook(u'No %s function found in setup module for %s' % (hook_name,setup_filename or self.asrequirement()))

            # get definitions of required parameters from setup module
            if hasattr(setup,'required_params'):
                required_params = setup.required_params
                if not isinstance(required_params,dict):
                    required_params = {k:None for k in required_params}
                else:
                    required_params = copy.deepcopy(required_params)
            else:
                required_params = {}

            # be sure some minimal functions are available in setup module at install step
            setattr(setup,'basedir',self.sourcespath)
            setattr(setup,'control',self)
            setattr(setup,'force',force)

            if not hasattr(setup,'uninstallkey'):
                setup.uninstallkey = []

            persistent_source_dir = None
            persistent_dir = None

            if self.sourcespath and os.path.isdir(self.sourcespath):
                persistent_source_dir = os.path.join(self.sourcespath,'WAPT','persistent')

            setattr(setup,'persistent_source_dir',persistent_source_dir)

            if wapt_context:
                setattr(setup,'run',wapt_context.run)
                setattr(setup,'run_notfatal',wapt_context.run_notfatal)
                setattr(setup,'WAPT',wapt_context)
                setattr(setup,'language',wapt_context.language)
                setattr(setup,'user',wapt_context.user)
                setattr(setup,'usergroups',wapt_context.usergroups)

            else:
                setattr(setup,'WAPT',None)
                setattr(setup,'language',get_language())
                # todo
                setattr(setup,'user',None)
                setattr(setup,'usergroups',[])
                setattr(setup,'persistent_dir',None)

            if hasattr(self,'persistent_dir') and self.persistent_dir:
                persistent_dir = self.persistent_dir
            elif self.package_uuid and wapt_context:
                    persistent_dir = os.path.join(wapt_context.wapt_base_dir,'private',self.package_uuid)

            setattr(setup,'persistent_dir',persistent_dir)

            # set params dictionary
            if not hasattr(setup,'params'):
                # create a params variable for the setup.install func call
                setattr(setup,'params',required_params)
            else:
                # update the already created params with additional params from command line
                setup.params.update(required_params)

            # add specific hook call arguments
            if params is not None:
                setup.params.update(params)

            try:
                logger.info(u"  executing setup.%s(%s) " % (hook_name,repr(setup.params)))
                with _disable_file_system_redirection():
                    hookdata = hook_func()
                return hookdata
            except Exception as e:
                logger.critical(u'Fatal error in %s function: %s:\n%s' % (hook_name,ensure_unicode(e),ensure_unicode(traceback.format_exc())))
                raise e

        finally:
            os.chdir(previous_cwd)
            gc.collect()
            if 'setup' in dir() and setup is not None:
                setup_name = setup.__name__[:]
                logger.debug('Removing module: %s, refcnt: %s'%(setup_name,sys.getrefcount(setup)))
                del setup
                if setup_name in sys.modules:
                    del sys.modules[setup_name]
            sys.path = oldpath


class WaptPackageDev(PackageEntry):
    """Source package directory"""

    def build_package(self,directoryname,inc_package_release=False,excludes=['.svn','.git','.gitignore','setup.pyc'],
                target_directory=None):
        raise NotImplementedError()


class WaptPackage(PackageEntry):
    """Built Wapt package zip file"""

    def __init__(self,package_filename):
        PackageEntry.__init__(self)
        self.package_filename = package_filename



def extract_iconpng_from_wapt(fname):
    """Return the content of WAPT/icon.png if it exists, a unknown.png file content if not

    """
    iconpng = None
    if os.path.isfile(fname):
        with zipfile.ZipFile(fname,'r',allowZip64=True) as waptzip:
            try:
                iconpng = waptzip.open(u'WAPT/icon.png').read()
            except:
                pass
    elif os.path.isdir(fname):
        png_path = os.path.join(fname,'WAPT','icon.png')
        if os.path.isfile(png_path):
            iconpng = open(u'WAPT/icon.png','rb').read()

    if not iconpng:
        unknown_png_path = os.path.join(os.path.dirname(__file__),'icons','unknown.png')
        if os.path.isfile(unknown_png_path):
            iconpng = open(unknown_png_path,'rb').read()

    if not iconpng:
        raise Exception(u'no icon.png found in package name {}'.format(fname))

    return iconpng


class WaptBaseRepo(BaseObjectClass):
    """Base abstract class for a Wapt Packages repository
    """

    _default_config = {
        'public_certs_dir': '',
        'check_certificates_validity':'True',
    }

    def __init__(self,name='abstract',cabundle=None,config=None):
        """Init properties, get default values from _default_config, and override them
                with constructor paramaters

        Args:
            name (str): internal name of the repository
            cabundle (CASSLBundle) : ca signature checking.

        Returns:
            self
        """

        self.name = name
        self._packages = None
        self._index = {}
        self._packages_date = None
        self.discarded = []
        self.check_certificates_validity = None
        self.public_certs_dir = None
        self.cabundle = None

        self.packages_whitelist = None
        self.packages_blacklist = None

        self.load_config(config=config)

        if self.public_certs_dir:
            self.cabundle = SSLCABundle()
            self.cabundle.add_pems(self.public_certs_dir)

        # if not None, control's signature will be check against this certificates list
        if cabundle is not None:
            self.cabundle = cabundle

    def load_config(self,config=None,section=None):
        """Load configuration from inifile section.
                Use name of repo as section name if section is not provided.
                Use 'global' if no section named section in ini file
                Value not defined in ini file are taken from class _default_config

                load_config is called at __init__, eventually with config = None.
                In this case, all parameters are initialized from defaults

        Args:
            config (RawConfigParser): ini configuration
            section (str)           : section where to loads parameters
                                      defaults to name of repository

        Returns:
            self: return itself to chain calls.
        """
        if not section:
             section = self.name

        # creates a default parser with a default section if None provided to get defaults
        if config is None:
            config = RawConfigParser(self._default_config)
            config.add_section(section)

        if not config.has_section(section):
            section = 'global'

        if config.has_option(section,'public_certs_dir'):
            self.public_certs_dir = config.get(section,'public_certs_dir')

        if config.has_option(section,'check_certificates_validity'):
            self.check_certificates_validity = config.getboolean(section,'check_certificates_validity')

        if config.has_option(section,'packages_whitelist'):
            self.packages_whitelist = ensure_list(config.get(section,'packages_whitelist'),allow_none=True)

        if config.has_option(section,'packages_blacklist'):
            self.packages_blacklist = ensure_list(config.get(section,'packages_blacklist'),allow_none=True)

        return self

    def load_config_from_file(self,config_filename,section=None):
        """Load repository configuration from an inifile located at config_filename

        Args:
            config_filename (str) : path to wapt inifile
            section (str): ini section from which to get parameters. default to repo name

        Returns:
            WaptBaseRepo: self

        """
        if section is None:
            section = self.name

        ini = RawConfigParser()
        ini.read(config_filename)
        self.load_config(ini,section)

        return self

    def _load_packages_index(self):
        self._packages = []
        self._packages_date = None
        self.discarded = []

    def _get_packages_index_data(self):
        """Method to get packages index as bytes from repository and last update date of ths index

        Returns:
            tuple (bytes,datetime) : data and last update datetime UTC
        """
        return (None,datetime.datetime.utcnow())

    def get_certificates(self,packages_zipfile=None):
        """Download signers certificates and crl from Package index on remote repository.

            These certificates and CRL are appended to Packages index when scanning
            packages.

        Args:
            packages_zipfile (zipfile): if None, donwload it from repo

        Returns :
            SSLCABundle
        """
        signer_certificates = SSLCABundle()
        if packages_zipfile is None:
            (packages_index_data,_dummy_date) = self._get_packages_index_data()
            packages_zipfile = zipfile.ZipFile(StringIO.StringIO(packages_index_data))

        filenames = packages_zipfile.namelist()
        for fn in filenames:
            if fn.startswith('ssl/'):
                cert = SSLCertificate(crt_string=packages_zipfile.read(name=fn))
                if not self.check_certificates_validity or cert.is_valid():
                    signer_certificates.add_certificates(cert)
            if fn.startswith('crl/'):
                try:
                    data = packages_zipfile.read(name=fn)
                    crl = SSLCRL(der_data=data)
                except:
                    crl = SSLCRL(pem_data=data)
                signer_certificates.add_crl(crl)

        #logger.debug('Packages embedded certificates : %s' % signer_certificates.certificates())
        return signer_certificates

    def invalidate_packages_cache(self):
        """Reset in memory packages index

        Returns:
            dict : old cache status dict(_packages=self._packages,_packages_date=self._packages_date,discarded=self.discarded)
        """
        old_status = dict(_packages=self._packages,_packages_date=self._packages_date,discarded=self.discarded)
        self._packages = None
        self._packages_date = None
        self._index = {}
        self.discarded = []
        return old_status

    def update(self):
        """Update local index of packages from source index

        Returns:
            None
        """
        self._load_packages_index()

    def is_locally_allowed_package(self,package):
        """Return True if package is not in blacklist and is in whitelist if whitelist is not None
        packages_whitelist and packages_blacklist are list of package name wildcards (file style wildcards)
        blacklist is taken in account first if defined.
        whitelist is taken in acoount if not None, else all not blacklisted package names are allowed.
        """
        if self.packages_blacklist is not None:
            for bl in self.packages_blacklist:
                if glob.fnmatch.fnmatch(package.package,bl):
                    return False
        if self.packages_whitelist is None:
            return True
        else:
            for wl in self.packages_whitelist:
                if glob.fnmatch.fnmatch(package.package,wl):
                    return True
        return False


    def packages(self):
        """Return list of packages, load it from repository if not yet available in memory
        To force the reload, call invalidate_index_cache() first or update()

        """
        if self._packages is None:
            self._load_packages_index()
        return self._packages

    def packages_date(self):
        """Date of last known packages index

        Returns:
            str: date/time of Packages index in iso format (string)
        """
        if self._packages is None:
            self._load_packages_index()
        return self._packages_date

    def is_available(self):
        """Return isodate of last updates of the repo is available else None
        """
        return self.packages_date()

    def need_update(self,last_modified=None):
        """Check if packages index has changed on repo and local index needs an update

        Compare date on local package index DB with the Packages file on remote
          repository with a HEAD http request.

        Args:
            last_modified (str): iso datetime of last known update of packages.

        Returns
            bool:   True if either Packages was never read or remote date of Packages is
                    more recent than the provided last_modifed date.

        >>> repo = WaptRemoteRepo(name='main',url='http://wapt/wapt',timeout=4)
        >>> waptdb = WaptDB('c:/wapt/db/waptdb.sqlite')
        >>> res = repo.need_update(waptdb.read_param('last-%s'% repo.url))
        >>> isinstance(res,bool)
        True
        """
        if not last_modified and not self._packages_date:
            logger.debug(u'need_update : no last_modified date provided, update is needed')
            return True
        else:
            if not last_modified:
                last_modified = self._packages_date
            if last_modified:
                logger.debug(u'Check last-modified header for %s to avoid unecessary update' % (self.name,))
                current_update = self.is_available()
                if current_update == last_modified:
                    logger.info(u'Index from %s has not been updated (last update %s), skipping update' % (self.name,current_update))
                    return False
                else:
                    return True
            else:
                return True

    def search(self,searchwords = [],sections=[],newest_only=False,exclude_sections=[],description_locale=None,
            host_capabilities=None,package_request=None):
        """Return list of package entries
            with description or name matching all the searchwords and section in
            provided sections list

        Args:
            searchwords (list or csv) : list of word to lookup in description and package names
            sections (list or csv) : list of package sections to use when searching
            newest_only (bool) : returns only highest version of package
            exclude_sections (list or csv): list of package sections to exclude when searching
            description_locale (str): if not None, search in description using this locale
            host_capabilities (HostCapabilities or dict): restrict output to these capabilities (os version locales, arch etc..)
            package_request (PackageRequest or dict) : restrict output to these filters, and sort output based on them

        Returns:
            list of PackageEntry with additional _localized_description added if description_locale is provided

        >>> r = WaptRemoteRepo(name='test',url='http://wapt.tranquil.it/wapt')
        >>> r.search('test')
        """
        searchwords = ensure_list(searchwords)
        sections = ensure_list(sections)
        exclude_sections = ensure_list(exclude_sections)
        if host_capabilities is not None and not isinstance(host_capabilities,HostCapabilities):
            # if dict
            host_capabilities = HostCapabilities(**host_capabilities)

        if package_request is not None and not isinstance(package_request,PackageRequest):
            # if given as dict from lazarus
            package_request = PackageRequest(**package_request)

        words = [ w.lower() for w in searchwords ]

        result = []
        if package_request is not None:
            packages = self.packages_matching(package_request)
        else:
            packages = self.packages()

        for package in packages:
            if host_capabilities is not None and not host_capabilities.is_matching_package(package):
                continue
            selected = True
            if description_locale is not None:
                _description = package.get_localized_description(description_locale)
                package._localized_description = _description
            else:
                _description = package.description

            for w in words:
                if w not in (_description+' '+package.package).lower():
                    selected = False
                    break
            if sections:
                if package.section not in sections:
                    selected = False

            if selected and package.section in exclude_sections:
                selected = False

            if selected:
                result.append(package)

        def sort_no_version(package1,package2):
            return cmp((package1.package,package1.architecture,package1.locale,package1.maturity,PackageVersion(package1.version)),(package2.package,package2.architecture,package2.locale,package2.maturity,PackageVersion(package2.version)))

        if newest_only:
            filtered = []
            last_package = ('','','','')
            for package in sorted(result,reverse=True,cmp=sort_no_version):
                if (package.package,package.architecture,package.locale,package.maturity) != last_package:
                    filtered.append(package)
                last_package = (package.package,package.architecture,package.locale,package.maturity)
            return list(reversed(filtered))
        else:
            return sorted(result)


    def get_package_entries(self,packages_names):
        r"""Return most up to date packages entries for packages_names
        packages_names is either a list or a string
        Returns:
            dict: a dictionnary with {'packages':[],'missing':[]}

        >>> r = WaptRemoteRepo()
        >>> r.load_config_from_file('c:/wapt/wapt-get.ini')
        >>> res = r.get_package_entries(['tis-firefox','tis-putty'])
        >>> isinstance(res['missing'],list) and isinstance(res['packages'][0],PackageEntry)
        True
        """
        result = {'packages':[],'missing':[]}
        if isinstance(packages_names,str) or isinstance(packages_names,unicode):
            packages_names=[ p.strip() for p in packages_names.split(",")]
        for package_name in packages_names:
            matches = self.packages_matching(package_name)
            if matches:
                result['packages'].append(matches[-1])
            else:
                result['missing'].append(package_name)
        return result

    def packages_matching(self,package_cond):
        """Return an ordered list of available packages entries which match
            the condition "packagename[([=<>]version)]?"
            version ascending

        Args;
            package_cond (str or PackageRequest): package name with optional version specifier.

        Returns:
            list of PackageEntry

        >>> from waptpackage import *
        >>> r = WaptRemoteRepo('http://wapt.tranquil.it/wapt')
        >>> r.packages_matching('tis-firefox(>=20)')
        [PackageEntry('tis-firefox','20.0.1-02'),
         PackageEntry('tis-firefox','21.0.0-00'),
         ...]
        """
        if package_cond is not None and not isinstance(package_cond,PackageRequest):
            package_cond = PackageRequest(request=package_cond)

        if package_cond is None:
            return sorted(self.packages())
        else:
            # sort using filter criteria preferences
            return sorted(
                    [p for p in self.packages() if package_cond.is_matched_by(p)],
                    cmp=lambda p1,p2: package_cond.compare_packages(p1,p2)
                )

    def __iter__(self):
        """Return an iterator for package names (higer version)"""
        # ensure packages is loaded
        if self._packages is None:
            self._load_packages_index()
        return self._index.__iter__()


    def __getitem__(self,packagename):
        """Return the highest version PackageEntry for supplied packagename
        """
        # ensure packages is loaded
        if self._packages is None:
            self._load_packages_index()
        return self._index[packagename]

    def get(self,packagename,default=None):
        # ensure packages is loaded
        if self._packages is None:
            self._load_packages_index()
        return self._index.get(packagename,default)

    def as_dict(self):
        result = {
            'name':self.name,
            'packages_whitelist':self.packages_whitelist,
            'packages_blacklist':self.packages_blacklist,
            'check_certificates_validity':self.check_certificates_validity,
            'authorized_certificates':([dict(c) for c in self.cabundle.certificates()] if self.cabundle else None),
             }
        return result



class WaptLocalRepo(WaptBaseRepo):
    """Index of Wapt local repository.
        Index of packages is located in a Packages zip file, having one
            Packages file, containing the concatenated content of "control"
            files of the packages.

            A blank line means new package.
    >>> localrepo = WaptLocalRepo('c:/wapt/cache')
    >>> localrepo.update()
    """

    def __init__(self,localpath=u'/var/www/wapt',name='waptlocal',cabundle=None,config=None):
        # store defaults at startup
        self._default_config.update({
            'localpath':ensure_unicode(localpath),
        })

        WaptBaseRepo.__init__(self,name=name,cabundle=cabundle,config=None)

        # override defaults and config with supplied parameters
        if self.localpath is not None:
            self.localpath = ensure_unicode(localpath.rstrip(os.path.sep))

    @property
    def packages_path(self):
        return os.path.abspath(os.path.join(self.localpath,'Packages'))

    def _get_packages_index_data(self):
        """Download or load local Packages index raw zipped data

        Returns:
            file: File like object for Packages Zipped data (local or remote)
        """
        return (open(self.packages_path,mode='rb').read(),fileutcdate(self.packages_path))

    def _load_packages_index(self):
        """Parse Packages index from local repo Packages file

        Packages file is zipped file with one file named Packages.

        This files is the concatenation of control files of each package
          in the repository

        Returns:
            None

        >>> repo = WaptLocalRepo(localpath='c:\\wapt\\cache')
        >>> repo._load_packages_index()
        >>> isinstance(repo.packages,list)
        True
        """
        # Packages file is a zipfile with one Packages file inside
        if not os.path.isdir(os.path.dirname(self.packages_path)):
            raise EWaptException(u'Directory for wapt local repo %s does not exist' % self.packages_path)

        if os.path.isfile(self.packages_path):
            (packages_data_str,_packages_datetime) =  self._get_packages_index_data()
            self._packages_date = datetime2isodate(_packages_datetime)
            with zipfile.ZipFile(StringIO.StringIO(packages_data_str)) as packages_file:
                packages_lines = packages_file.read(name='Packages').decode('utf8').splitlines()

            if self._packages is not None:
                del(self._packages[:])
            else:
                self._packages = []
            self._index.clear()

            self.discarded = []

            startline = 0
            endline = 0

            def add(start,end):
                if start != end:
                    package = PackageEntry()
                    package._load_control(u'\n'.join(packages_lines[start:end]))
                    logger.debug(u"%s (%s)" % (package.package,package.version))
                    package.repo_url = u'file:///%s'%(self.localpath.replace('\\','/'))
                    package.repo = self.name
                    # TO CHECK
                    #package.filename = package.make_package_filename()
                    #package.localpath = os.path.join(self.localpath,package.filename)
                    if self.is_locally_allowed_package(package):
                        try:
                            if self.cabundle is not None:
                                package.check_control_signature(self.cabundle)
                            self._packages.append(package)
                            # index last version
                            if package.package not in self._index or self._index[package.package] < package:
                                self._index[package.package] = package
                        except Exception as e:
                            logger.info(u'Package %s discarded because: %s'% (package.localpath,e))
                            self.discarded.append(package)
                    else:
                        logger.info(u'Discarding %s on repo "%s" because of local whitelist of blacklist rules' % (package.asrequirement(),self.name))
                        self.discarded.append(package)

            for line in packages_lines:
                if line.strip()=='':
                    add(startline,endline)
                    endline += 1
                    startline = endline
                # add ettribute to current package
                else:
                    endline += 1
            # last one
            add(startline,endline)
        else:
            self.invalidate_packages_cache()
            self._packages = []
            logger.info(u'Index file %s does not yet exist' % self.packages_path)

    def _extract_icon(self,entry):
        # looks for an icon in wapt package
        icons_path = os.path.abspath(os.path.join(self.localpath,'icons'))
        if not os.path.isdir(icons_path):
            os.makedirs(icons_path)
        icon_fn = os.path.join(icons_path,u"%s.png" % entry.package)
        if entry.section not in ['group','host','unit','profile','wsus'] and not os.path.isfile(icon_fn):
            try:
                icon = extract_iconpng_from_wapt(entry.localpath)
                open(icon_fn,'wb').write(icon)
            except Exception as e:
                logger.debug(r"Unable to extract icon for %s:%s"%(entry.localpath,e))

    def _append_package_to_index(self,entry):
        """Append a single package to zipped index Packages without checking if it exists already

        Returns:


        """
        packages_fname = os.path.abspath(os.path.join(self.localpath,'Packages'))
        self._packages = None

        logger.info(u"Writing new %s" % packages_fname)
        tmp_packages_fname = packages_fname+'.%s'%datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        try:
            shutil.copy2(packages_fname,tmp_packages_fname)
            with zipfile.ZipFile(tmp_packages_fname, "a",compression=zipfile.ZIP_DEFLATED) as  myzipfile:
                packages_lines = myzipfile.read('Packages').decode('utf8').splitlines()
                if packages_lines and packages_lines[-1] != '':
                    packages_lines.append('')
                packages_lines.append(entry.ascontrol(with_non_control_attributes=True))
                packages_lines.append('')

                myzipfile.remove(u"Packages")
                zi = zipfile.ZipInfo(u"Packages",date_time = time.localtime())
                zi.compress_type = zipfile.ZIP_DEFLATED
                myzipfile.writestr(zi,u'\n'.join(packages_lines).encode('utf8'))

                # Add list of signers certificates
                certs = entry.package_certificates()
                if certs:
                    for crt in certs:
                        crt_filename = u"ssl/%s.crt" % crt.fingerprint
                        if not myzipfile.NameToInfo.get(crt_filename):
                            zi = zipfile.ZipInfo(crt_filename,date_time = time.localtime())
                            zi.compress_type = zipfile.ZIP_DEFLATED
                            myzipfile.writestr(zi,crt.as_pem())

            if os.path.isfile(packages_fname):
                os.unlink(packages_fname)
            os.rename(tmp_packages_fname,packages_fname)
            logger.info(u"Finished")
            return entry.localpath

        except Exception as e:
            if os.path.isfile(tmp_packages_fname):
                os.unlink(tmp_packages_fname)
            logger.critical(u'Unable to create new Packages file : %s' % e)
            raise e

    def _ensure_canonical_package_filename(self,entry):
        """Rename the local wapt package so that it complies with canonical package naming rules

        """
        theoritical_package_filename =  entry.make_package_filename()
        package_filename = entry.filename
        if package_filename != theoritical_package_filename:
            logger.warning(u'Package filename %s should be %s to comply with control metadata. Renaming...'%(package_filename,theoritical_package_filename))
            new_fn = os.path.join(os.path.dirname(entry.localpath),theoritical_package_filename)
            os.rename(entry.localpath,new_fn)
            return new_fn
        else:
            return None

    def update_packages_index(self,force_all=False,proxies=None):
        """Scan self.localpath directory for WAPT packages and build a Packages (utf8) zip file with control data and MD5 hash

        Extract icons from packages (WAPT/icon.png) and stores them in <repo path>/icons/<package name>.png
        Extract certificate and add it to Packages zip file in ssl/<fingerprint.crt>
        Append CRL for certificates.

        Returns:
            dict :  {'processed':processed,'kept':kept,'errors':errors,'packages_filename':packages_fname}

        """
        packages_fname = os.path.abspath(os.path.join(self.localpath,'Packages'))
        if force_all:
            self._packages = []

        # A bundle for package signers certificates
        signer_certificates = SSLCABundle()

        old_entries = {}

        for package in self.packages():
            # keep only entries which are older than index. Other should be recalculated.
            localwaptfile = os.path.abspath(os.path.join(self.localpath,os.path.basename(package.filename)))
            if os.path.isfile(localwaptfile):
                if fileisoutcdate(localwaptfile) <= self._packages_date:
                    old_entries[os.path.basename(package.filename)] = package
                else:
                    logger.info(u"Don't keep old entry for %s, wapt package is newer than index..." % package.asrequirement())
            else:
                logger.info(u'Stripping entry without matching file : %s'%localwaptfile)

        if not os.path.isdir(self.localpath):
            raise Exception(u'%s is not a directory' % (self.localpath))
        waptlist = glob.glob(os.path.abspath(os.path.join(self.localpath,'*.wapt')))
        packages_lines = []
        kept = []
        processed = []
        errors = []
        if self._packages is None:
            self._packages = []
        else:
            del(self._packages[:])
        self._index.clear()

        for fname in waptlist:
            try:
                package_filename = os.path.basename(fname)
                entry = PackageEntry()
                if package_filename in old_entries:
                    entry.load_control_from_wapt(fname,calc_md5=False)

                    if self.cabundle is not None:
                        try:
                            entry.check_control_signature(self.cabundle)
                        except (EWaptNotSigned,SSLVerifyException) as e:
                            logger.info(u'Package %s discarded because: %s'% (package_filename,e))
                            continue

                    if not force_all and entry == old_entries[package_filename] and \
                                entry.signature == old_entries[package_filename].signature and \
                                entry.signature_date == old_entries[package_filename].signature_date:
                        logger.debug(u"  Keeping %s" % package_filename)
                        kept.append(fname)
                        entry = old_entries[package_filename]
                    else:
                        logger.info(u"  Reprocessing %s" % fname)
                        entry.load_control_from_wapt(fname)
                        processed.append(fname)
                else:
                    logger.info(u"  Processing new %s" % fname)
                    entry.load_control_from_wapt(fname)
                    processed.append(fname)
                    self._ensure_canonical_package_filename(entry)

                packages_lines.append(entry.ascontrol(with_non_control_attributes=True))
                # add a blank line between each package control
                packages_lines.append('')

                self._packages.append(entry)
                # index last version
                if entry.package not in self._index or self._index[entry.package] < entry:
                    self._index[entry.package] = entry

                # looks for the signer certificate and add it to Packages if not already
                certs = entry.package_certificates()
                if certs:
                    signer_certificates.add_certificates(certs)

                self._extract_icon(entry)

            except Exception as e:
                logger.critical(u"package %s: %s" % (fname,ensure_unicode(e)))
                errors.append(fname)

        try:
            logger.info(u"Check / update CRL for embedded certificates")
            signer_certificates.update_crl(force = force_all, proxies=proxies)
        except Exception as e:
            logger.critical(u'Error when updating CRL for signers certificates : %s' % e)

        logger.info(u"Writing new %s" % packages_fname)
        tmp_packages_fname = packages_fname+'.%s'%datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        try:
            with zipfile.ZipFile(tmp_packages_fname, "w",compression=zipfile.ZIP_DEFLATED) as  myzipfile:
                zi = zipfile.ZipInfo(u"Packages",date_time = time.localtime())
                zi.compress_type = zipfile.ZIP_DEFLATED
                myzipfile.writestr(zi,u'\n'.join(packages_lines).encode('utf8'))

                # Add list of signers certificates
                for crt in signer_certificates.certificates():
                    zi = zipfile.ZipInfo(u"ssl/%s.crt" % crt.fingerprint,date_time = crt.not_before.timetuple())
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    myzipfile.writestr(zi,crt.as_pem())

                for crl in signer_certificates.crls:
                    aki = crl.authority_key_identifier
                    zi = zipfile.ZipInfo(u"crl/%s.crl" % aki.encode('hex'),date_time = crl.last_update.timetuple())
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    myzipfile.writestr(zi,crl.as_der())

            if os.path.isfile(packages_fname):
                os.unlink(packages_fname)
            os.rename(tmp_packages_fname,packages_fname)
            logger.info(u"Finished")
        except Exception as e:
            if os.path.isfile(tmp_packages_fname):
                os.unlink(tmp_packages_fname)
            logger.critical(u'Unable to create new Packages file : %s' % e)
            raise e
        return {'processed':processed,'kept':kept,'errors':errors,'packages_filename':packages_fname}

    def load_config(self,config=None,section=None):
        """Load waptrepo configuration from inifile section.

                Use name of repo as section name if section is not provided.
                Use 'global' if no section named section in ini file
        Args:
            config (RawConfigParser): ini configuration
            section (str)           : section where to loads parameters
                                      defaults to name of repository

        Returns:
            WaptRemoteRepo: return itself to chain calls.
        """

        if not section:
             section = self.name

        # creates a default parser with a default section if None provided to get defaults
        if config is None:
            config = RawConfigParser(self._default_config)
            config.add_section(section)

        if not config.has_section(section):
            section = 'global'

        WaptBaseRepo.load_config(self,config,section)

        if config.has_option(section,'localpath'):
            self.localpath = config.get(section,'localpath')

        return self

    def as_dict(self):
        result = super(WaptLocalRepo,self).as_dict()
        result.update(
            {'localpath':self.localpath,
            })
        return result


class WaptRemoteRepo(WaptBaseRepo):
    """Gives access to a remote http repository, with a zipped Packages packages index

    >>> repo = WaptRemoteRepo(name='main',url='http://wapt/wapt',timeout=4)
    >>> last_modified = repo.is_available()
    >>> isinstance(last_modified,str)
    True
    """

    def __init__(self,url=None,name='',verify_cert=None,http_proxy=None,timeout=None,cabundle=None,config=None):
        """Initialize a repo at url "url".

        Args:
            name (str): internal local name of this repository
            url  (str): http URL to the repository.
                 If url is None, the url is requested from DNS by a SRV query
            http_proxy (str): url of proxy like  http://proxy:port
            timeout (float): timeout in seconds for the connection to the rmeote repository
            config (RawConfigParser) : loads conf from this Parser
        """

        # additional properties
        self._default_config.update({
            'repo_url':'',
            'timeout':5.0,
            'verify_cert':'1', # default is to check repo https certificates
            'http_proxy':'',
        })

        # create additional properties
        self._repo_url = None
        self.http_proxy = None
        self.verify_cert = None

        self.client_certificate = None
        self.client_private_key = None

        self.timeout = None

        # this load and empty config
        WaptBaseRepo.__init__(self,name=name,cabundle=cabundle,config=config)

        # forced URL
        if url is not None:
            if url and url[-1]=='/':
                url = url.rstrip('/')
            self._repo_url = url

        if verify_cert is not None:
            self.verify_cert = verify_cert
        if self.verify_cert == '':
            self.verify_cert = '0'
        if timeout is not None:
            self.timeout = timeout
        if http_proxy is not None:
            self.http_proxy = http_proxy

    @property
    def repo_url(self):
        return self._repo_url

    @property
    def proxies(self):
        if self.http_proxy:
            return {'http':self.http_proxy,'https':self.http_proxy}
        else:
            return {'http':None,'https':None}

    @repo_url.setter
    def repo_url(self,value):
        if value:
            value = value.rstrip('/')

        if value != self._repo_url:
            self._repo_url = value
            self.invalidate_packages_cache()

    def load_config(self,config=None,section=None):
        """Load waptrepo configuration from inifile section.

                Use name of repo as section name if section is not provided.
                Use 'global' if no section named section in ini file
        Args:
            config (RawConfigParser): ini configuration
            section (str)           : section where to loads parameters
                                      defaults to name of repository

        Returns:
            WaptRemoteRepo: return itself to chain calls.
        """
        if not section:
             section = self.name

        # creates a default parser with a default section if None provided to get defaults
        if config is None:
            config = RawConfigParser(self._default_config)
            config.add_section(section)

        if not config.has_section(section):
            section = 'global'

        WaptBaseRepo.load_config(self,config,section)

        if config.has_option(section,'repo_url'):
            self.repo_url = config.get(section,'repo_url')

        if config.has_option(section,'verify_cert'):
            try:
                self.verify_cert = config.getboolean(section,'verify_cert')
            except:
                self.verify_cert = config.get(section,'verify_cert')
                if self.verify_cert == '':
                    self.verify_cert = '0'
        #else:
        #    self.verify_cert = self._default_config['verify_cert']

        if config.has_option(section,'http_proxy'):
            if not config.has_option(section,'use_http_proxy_for_repo') or config.getboolean(section,'use_http_proxy_for_repo'):
                self.http_proxy = config.get(section,'http_proxy')

        if config.has_option(section,'timeout'):
            self.timeout = config.getfloat(section,'timeout')

        if config.has_option(section,'client_certificate'):
            self.client_certificate = config.get(section,'client_certificate')

        if config.has_option(section,'client_private_key'):
            self.client_private_key = config.get(section,'client_private_key')

        return self


    @property
    def packages_url(self):
        """return url of Packages index file

        >>> repo = WaptRemoteRepo(name='main',url='http://wapt/wapt',timeout=4)
        >>> repo.packages_url
        'http://wapt/wapt/Packages'

        hardcoded path to the Packages index.
        """
        return self.repo_url + '/Packages'

    def client_auth(self):
        """Return SSL pair (cert,key) filenames for client side SSL auth
        """
        if self.client_certificate and os.path.isfile(self.client_certificate) and os.path.isfile(self.client_private_key):
            return (self.client_certificate,self.client_private_key)
        else:
            return None

    def is_available(self):
        """Check if repo is reachable an return createion date of Packages.

        Try to access the repo and return last modified date of repo index or None if not accessible

        Returns:
            str: Iso creation date of remote Package file as returned in http headers

        >>> repo = WaptRemoteRepo(name='main',url='https://wapt/wapt',timeout=1)
        >>> repo.is_available() <= datetime2isodate()
        True
        >>> repo = WaptRemoteRepo(name='main',url='https://badwapt/wapt',timeout=1)
        >>> repo.is_available() is None
        True
        """
        try:
            logger.debug(u'Checking availability of %s' % (self.packages_url,))
            req = requests.head(
                self.packages_url,
                timeout=self.timeout,
                proxies=self.proxies,
                verify=self.verify_cert,
                headers=default_http_headers(),
                cert = self.client_auth(),
                allow_redirects=True,
                )
            req.raise_for_status()
            packages_last_modified = req.headers.get('last-modified')
            return httpdatetime2isodate(packages_last_modified)
        except requests.exceptions.SSLError as e:
            print(u'Certificate check failed for %s and verify_cert %s'%(self.packages_url,self.verify_cert))
            raise
        except requests.RequestException as e:
            logger.info(u'Repo packages index %s is not available : %s'%(self.packages_url,e))

            return None

    def _load_packages_index(self):
        """Try to load index of packages as PackageEntry list from repository

        HTTP Get remote Packages zip file and parses the entries.

        The list of package entries is stored in the packages property.

        Returns
            dict: list of added or removed packages and create date {'added':list,'removed':list,'last-modified':isodatetime}
        """
        if not self.repo_url:
            raise EWaptException('Repository URL for %s is empty. Add a %s section in ini' % (self.name,self.name))

        if self._packages is None:
            self._packages = []
            self._packages_date = None

        self._index.clear()
        self.discarded = []

        new_packages = []
        logger.debug(u'Read remote Packages zip file %s' % self.packages_url)

        (_packages_index_str,_packages_index_date) = self._get_packages_index_data()
        with zipfile.ZipFile(StringIO.StringIO(_packages_index_str)) as waptzip:
            filenames = waptzip.namelist()
            packages_lines = codecs.decode(waptzip.read(name='Packages'),'UTF-8').splitlines()

            if self.cabundle is not None:
                # load certificates and CRLs
                signer_certificates = self.get_certificates(packages_zipfile = waptzip)
                logger.debug(u'Packages index from repo %s has %s embedded certificates' % (self.name,len(signer_certificates._certificates)))

        startline = 0
        endline = 0

        def add(start,end):
            if start != end:
                package = PackageEntry()
                package._load_control(u'\n'.join(packages_lines[start:end]))
                #logger.debug(u"%s (%s)" % (package.package,package.version))
                package.repo_url = self.repo_url
                package.repo = self.name

                if self.is_locally_allowed_package(package):
                    try:
                        if self.cabundle is not None:
                            package.check_control_signature(trusted_bundle=self.cabundle,signers_bundle = signer_certificates)
                        new_packages.append(package)
                        if package.package not in self._index or self._index[package.package] < package:
                            self._index[package.package] = package
                    except Exception as e:
                        logger.info(u'Discarding %s on repo "%s": %s' % (package.asrequirement(),self.name,e))
                        #logger.debug('Certificate bundle : %s' % self.cabundle)
                        self.discarded.append(package)
                else:
                    logger.info(u'Discarding %s on repo "%s" because of local whitelist of blacklist rules' % (package.asrequirement(),self.name))
                    self.discarded.append(package)

        for line in packages_lines:
            if line.strip()=='':
                add(startline,endline)
                endline += 1
                startline = endline
            # add ettribute to current package
            else:
                endline += 1
        # last one
        add(startline,endline)
        added = [ p for p in new_packages if p not in self._packages]
        removed = [ p for p in self._packages if p not in new_packages]
        self._packages = new_packages
        self._packages_date = datetime2isodate(_packages_index_date)
        return {'added':added,'removed':removed,'last-modified': self.packages_date(), 'discarded':self.discarded }

    def _get_packages_index_data(self):
        """Download or load local Packages index raw zipped data

        Returns:
            (str,datetime.datetime): Packages data (local or remote) and last update date
        """
        packages_answer = requests.get(
            self.packages_url,
            proxies=self.proxies,
            timeout=self.timeout,
            verify=self.verify_cert,
            headers=default_http_headers(),
            cert = self.client_auth(),
            allow_redirects=True,
            )
        packages_answer.raise_for_status()
        packages_last_modified = packages_answer.headers.get('last-modified')
        _packages_index_date = httpdatetime2datetime(packages_last_modified)
        return (str(packages_answer.content),_packages_index_date)

    def packages(self):
        if self._packages is None:
            self._load_packages_index()
        return self._packages

    def as_dict(self):
        result = super(WaptRemoteRepo,self).as_dict()
        result.update({
            'repo_url':self._repo_url,
            'proxies':self.proxies,
            'timeout':self.timeout,
             })
        return result

    def download_packages(self,package_requests,target_dir=None,usecache=True,printhook=None):
        r"""Download a list of packages (requests are of the form packagename (>version) )
           returns a dict of {"downloaded,"skipped","errors"}

        If package_requests is a list of PackageEntry, update localpath of entry to match downloaded file.

        Args:
            package_requests (list) : list of PackageEntry to download or list of package with optional version

        Returns:
            dict: 'packages', 'downloaded', 'skipped', 'errors'

        >>> repo = WaptRemoteRepo(url='http://wapt.tranquil.it/wapt')
        >>> wapt.download_packages(['tis-firefox','tis-waptdev'],printhook=nullhook)
        {'downloaded': [u'c:/wapt\\cache\\tis-firefox_37.0.2-9_all.wapt', u'c:/wapt\\cache\\tis-waptdev.wapt'], 'skipped': [], 'errors': []}
        """
        if not isinstance(package_requests,(list,tuple)):
            package_requests = [ package_requests ]
        if not target_dir:
            target_dir = tempfile.mkdtemp()

        downloaded = []
        skipped = []
        errors = []
        packages = []
        for p in package_requests:
            if isinstance(p,(str,unicode)):
                mp = self.packages_matching(p)
                if mp:
                    packages.append(mp[-1])
                else:
                    errors.append((p,u'Unavailable package %s' % (p,)))
                    logger.critical(u'Unavailable package %s' % (p,))
            elif isinstance(p,PackageEntry):
                packages.append(p)
            else:
                raise Exception('Invalid package request %s' % p)

        for entry in packages:
            download_url = entry.download_url
            fullpackagepath = os.path.join(target_dir,entry.filename)
            skip = False
            if usecache and os.path.isfile(fullpackagepath) and os.path.getsize(fullpackagepath) == entry.size :
                # check version
                try:
                    cached = PackageEntry()
                    cached.load_control_from_wapt(fullpackagepath,calc_md5=True)
                    if entry == cached:
                        if entry.md5sum == cached.md5sum:
                            entry.localpath = cached.localpath
                            skipped.append(fullpackagepath)
                            logger.info(u"  Use cached package file from " + fullpackagepath)
                            skip = True
                        else:
                            logger.critical(u"Cached file MD5 doesn't match MD5 found in packages index. Discarding cached file")
                            os.remove(fullpackagepath)
                except Exception as e:
                    # error : reload
                    logger.debug(u'Cache file %s is corrupted, reloading it. Error : %s' % (fullpackagepath,e) )

            if not skip:
                logger.info(u"  Downloading package from %s" % download_url)
                try:
                    def report(received,total,speed,url):
                        try:
                            if total>1:
                                stat = u'%s : %i / %i (%.0f%%) (%.0f KB/s)\r' % (url,received,total,100.0*received/total, speed)
                                print(stat)
                            else:
                                stat = ''
                        except:
                            pass
                    """
                    if not printhook:
                        printhook = report
                    """
                    wget(download_url,
                        target_dir,
                        proxies=self.proxies,
                        printhook = printhook,
                        connect_timeout=self.timeout,
                        verify_cert = self.verify_cert,
                        cert = self.client_auth(),
                        resume= usecache,
                        md5 = entry.md5sum,
                        )
                    entry.localpath = fullpackagepath
                    downloaded.append(fullpackagepath)
                except Exception as e:
                    if os.path.isfile(fullpackagepath):
                        os.remove(fullpackagepath)
                    logger.critical(u"Error downloading package from http repository, please update... error : %s" % e)
                    errors.append((download_url,"%s" % e))
        return {"downloaded":downloaded,"skipped":skipped,"errors":errors,"packages":packages}

def update_packages(adir,force=False,proxies=None):
    """Helper function to update a local packages index

    This function is used on repositories to rescan all packages and
      update the Packages index.

    >>> if os.path.isdir('c:\\wapt\\cache'):
    ...     repopath = 'c:\\wapt\\cache'
    ... else:
    ...     repopath = '/var/www/wapt'
    >>> p = PackageEntry()
    >>> p.package = 'test'
    >>> p.version = '10'
    >>> new_package_fn = os.path.join(repopath,p.make_package_filename())
    >>> if os.path.isfile(new_package_fn):
    ...     os.unlink(new_package_fn)
    >>> res = update_packages(repopath)
    >>> os.path.isfile(res['packages_filename'])
    True
    >>> r = WaptLocalRepo(localpath=repopath)
    >>> l1 = r.packages()
    >>> res = r.update_packages_index()
    >>> l2 = r.packages()
    >>> [p for p in l2 if p not in l1]
    ["test (=10)"]
    """
    repo = WaptLocalRepo(localpath=os.path.abspath(adir))
    return repo.update_packages_index(force_all=force,proxies=proxies)

if __name__ == '__main__':
    import doctest
    import sys
    reload(sys)
    sys.setdefaultencoding("UTF-8")
    import doctest
    doctest.ELLIPSIS_MARKER = '???'
    doctest.testmod(optionflags=doctest.ELLIPSIS)
    sys.exit(0)
