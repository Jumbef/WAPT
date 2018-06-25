#!/opt/wapt/python
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

# old function to install waptserver on windows. need to be rewritten (switch to nginx, websocket, etc.)
from __future__ import absolute_import

import os
import sys
from win32api import GetUserName

try:
    wapt_root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__),'..'))
except:
    wapt_root_dir = 'c:/tranquilit/wapt'

from waptserver.config import __version__

from optparse import OptionParser
import logging
import subprocess
import setuphelpers
import datetime

import jinja2
import time
import random
import string
import iniparse

from setuphelpers import run
from waptutils import setloglevel
from waptcrypto import SSLPrivateKey,SSLCertificate

import waptserver.config
from waptserver.utils import logger,mkdir_p


def fqdn():
    result = None
    try:
        import socket
        result = socket.getfqdn()
    except:
        pass
    if not result:
        result = 'wapt'
    if '.' not in result:
        result += '.local'

    return result

def create_dhparam(key_size=2048):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import dh
    parameters = dh.generate_parameters(generator=2, key_size=key_size,backend=default_backend())
    return parameters.parameter_bytes(serialization.Encoding.PEM,format=serialization.ParameterFormat.PKCS3)

def install_windows_nssm_service(
        service_name, service_binary, service_parameters, service_logfile, service_dependencies=None):
    """Setup a program as a windows Service managed by nssm
    >>> install_windows_nssm_service("WAPTServer",
        os.path.abspath(os.path.join(wapt_root_dir,'waptpython.exe')),
        os.path.abspath(__file__),
        os.path.join(log_directory,'nssm_waptserver.log'),
        service_logfile,
        'WAPTApache')
    """
    import setuphelpers
    from setuphelpers import registry_set, REG_DWORD, REG_EXPAND_SZ, REG_MULTI_SZ, REG_SZ
    datatypes = {
        'dword': REG_DWORD,
        'sz': REG_SZ,
        'expand_sz': REG_EXPAND_SZ,
        'multi_sz': REG_MULTI_SZ,
    }

    if setuphelpers.service_installed(service_name):
        if not setuphelpers.service_is_stopped(service_name):
            logger.info('Stop running "%s"' % service_name)
            setuphelpers.run('net stop "%s" /yes' % service_name)
            while not setuphelpers.service_is_stopped(service_name):
                logger.debug('Waiting for "%s" to terminate' % service_name)
                time.sleep(2)

        logger.info('Unregister existing "%s"' % service_name)
        setuphelpers.run('sc delete "%s"' % service_name)

    if not setuphelpers.iswin64():
        raise Exception('Windows 32bit install not supported')

    nssm = os.path.join(wapt_root_dir, 'waptservice', 'win64', 'nssm.exe')


    logger.info('Register service "%s" with nssm' % service_name)
    cmd = '"{nssm}" install "{service_name}" "{service_binary}" {service_parameters}'.format(
        nssm=nssm,
        service_name=service_name,
        service_binary=service_binary,
        service_parameters=service_parameters
    )
    logger.info('running command : %s' % cmd)
    setuphelpers.run(cmd)

    # fix some parameters (quotes for path with spaces...
    params = {
        'Description': 'sz:%s' % service_name,
        'DelayedAutostart': 1,
        'DisplayName': 'sz:%s' % service_name,
        'AppStdout': r'expand_sz:{}'.format(service_logfile),
        'ObjectName': r'NT AUTHORITY\NetworkService',
        'Parameters\\AppStderr': r'expand_sz:{}'.format(service_logfile),
        'Parameters\\AppParameters': r'expand_sz:{}'.format(service_parameters),
        'Parameters\\AppNoConsole': 1,
    }

    root = setuphelpers.HKEY_LOCAL_MACHINE
    base = r'SYSTEM\CurrentControlSet\services\%s' % service_name
    for key in params:
        if isinstance(params[key], int):
            (valuetype, value) = ('dword', params[key])
        elif ':' in params[key]:
            (valuetype, value) = params[key].split(':', 1)
            if valuetype == 'dword':
                value = int(value)
        else:
            (valuetype, value) = ('sz', params[key])
        fullpath = base + '\\' + key
        (path, keyname) = fullpath.rsplit('\\', 1)
        if keyname == '@' or keyname == '':
            keyname = None
        registry_set(root, path, keyname, value, type=datatypes[valuetype])

    if service_dependencies:
        logger.info(
            'Register dependencies for service "%s" with nssm : %s ' %
            (service_name, service_dependencies))
        cmd = '"{nssm}" set "{service_name}" DependOnService {service_dependencies}'.format(
            nssm=nssm,
            service_name=service_name,
            service_dependencies=service_dependencies
        )
        logger.info('running command : %s' % cmd)
        setuphelpers.run(cmd)

        # fullpath = base+'\\' + 'DependOnService'
        #(path,keyname) = fullpath.rsplit('\\',1)
        # registry_set(root,path,keyname,service_dependencies,REG_MULTI_SZ)


def make_nginx_config(wapt_root_dir, wapt_folder, force = False):
    """Create a nginx default config file to server wapt_folder and reverse proxy waptserver
    Create a key and self signed certificate.

    Args:
        wapt_root_dir (str)
        wapt_folder (str) : local path to wapt rdirectory for packages
                             wapt-host and waptwua are derived from this.

    Returns:
        str: path to nginx conf file
    """

    ap_conf_dir = os.path.join(
        wapt_root_dir,
        'waptserver',
        'nginx',
        'conf')
    ap_file_name = 'nginx.conf'
    ap_conf_file = os.path.join(ap_conf_dir, ap_file_name)
    ap_ssl_dir = os.path.join(wapt_root_dir,'waptserver','nginx','ssl')

    if os.path.isfile(ap_conf_file) and not force:
        if 'waptserver' in open(ap_conf_file,'r').read():
            return ap_conf_file

    setuphelpers.mkdirs(ap_ssl_dir)

    key_fn = os.path.join(ap_ssl_dir,'key.pem')
    key = SSLPrivateKey(key_fn)
    if not os.path.isfile(key_fn):
        print('Create SSL RSA Key %s' % key_fn)
        key.create()
        key.save_as_pem()

    cert_fn = os.path.join(ap_ssl_dir,'cert.pem')
    if os.path.isfile(cert_fn):
        crt = SSLCertificate(cert_fn)
        if crt.cn != fqdn():
            os.rename(cert_fn,"%s-%s.old" % (cert_fn,'{:%Y%m%d-%Hh%Mm%Ss}'.format(datetime.datetime.now())))
            crt = key.build_sign_certificate(cn=fqdn(),is_code_signing=False)
            print('Create X509 cert %s' % cert_fn)
            crt.save_as_pem(cert_fn)
    else:
        crt = key.build_sign_certificate(cn=fqdn(),is_code_signing=False)
        print('Create X509 cert %s' % cert_fn)
        crt.save_as_pem(cert_fn)

    # write config file
    jinja_env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(wapt_root_dir,'waptserver','scripts')))
    template = jinja_env.get_template('waptwindows.nginxconfig.j2')
    template_variables = {
        'wapt_repository_path': os.path.dirname(conf['wapt_folder']).replace('\\','/'),
        'waptserver_port': conf['waptserver_port'],
        'windows': True,
        'ssl': True,
        'force_https': False,
        'use_kerberos': False,
        'wapt_ssl_key_file': key_fn.replace('\\','/'),
        'wapt_ssl_cert_file': cert_fn.replace('\\','/'),
        'log_dir': os.path.join(wapt_root_dir,'waptserver','nginx','logs').replace('\\','/'),
        'wapt_root_dir' : wapt_root_dir.replace('\\','/'),
    }

    config_string = template.render(template_variables)
    print('Create nginx conf file %s' % ap_conf_file)
    with open(ap_conf_file, 'wt') as dst_file:
        dst_file.write(config_string)
    return ap_conf_file


def install_nginx_service(options,conf=None):
    if conf is None:
        conf = waptserver.config.load_config(options.configfile)

    print("register nginx frontend")
    repository_path = os.path.join(wapt_root_dir,'waptserver','repository')
    for repo_path in ('wapt','wapt-host','waptwua'):
        mkdir_p(os.path.join(repository_path,repo_path))
        run(r'icacls "%s" /grant  "*S-1-5-20":(OI)(CI)(M)' % os.path.join(repository_path,repo_path))
    mkdir_p(os.path.join(wapt_root_dir,'waptserver','nginx','temp'))
    run(r'icacls "%s" /grant  "*S-1-5-20":(OI)(CI)(M)' % (os.path.join(wapt_root_dir,'waptserver','nginx','temp')))

    run(r'icacls "%s" /grant  "*S-1-5-20":(OI)(CI)(M)' % os.path.join(
                wapt_root_dir,'waptserver','nginx','logs'))

    make_nginx_config(wapt_root_dir, conf['wapt_folder'],force=options.force)
    service_binary = os.path.abspath(os.path.join(wapt_root_dir,'waptserver','nginx','nginx.exe'))
    service_parameters = ''
    service_logfile = os.path.join(log_directory, 'nssm_nginx.log')

    service_name = 'WAPTNginx'
    if setuphelpers.service_installed(service_name) and setuphelpers.service_is_running(service_name):
        setuphelpers.service_stop(service_name)
    #print('Register "%s" in registry' % service_name)
    install_windows_nssm_service(service_name,service_binary,service_parameters,service_logfile)
    time.sleep(5)

def install_postgresql_service(options,conf=None):
    if conf is None:
        conf = waptserver.config.load_config(options.configfile)
    print ("install postgres database")

    pgsql_root_dir = r'%s\waptserver\pgsql' % wapt_root_dir
    pgsql_data_dir = r'%s\waptserver\pgsql_data' % wapt_root_dir
    pgsql_data_dir = pgsql_data_dir.replace('\\','/')


    print ("build database directory")
    if not os.path.exists(os.path.join(pgsql_data_dir,'postgresql.conf')):
        print ("init pgsql data directory")
        pg_data_dir = os.path.join(wapt_root_dir,'waptserver','pgsql_data')

        setuphelpers.mkdirs(pg_data_dir)

        # need to have specific write acls for current user otherwise initdb fails...
        setuphelpers.run(r'icacls "%s" /t /grant  "%s":(OI)(CI)(M)' % (pg_data_dir,GetUserName()))
        setuphelpers.run(r'"%s\waptserver\pgsql\bin\initdb" -U postgres -E=UTF8 -D "%s\waptserver\pgsql_data"' % (wapt_root_dir,wapt_root_dir))

        setuphelpers.run(r'icacls "%s" /t /grant  "*S-1-5-20":(OI)(CI)(M)' % pg_data_dir)

        print("start postgresql database")

        if setuphelpers.service_installed('WaptPostgresql'):
            if setuphelpers.service_is_running('WaptPostgresql'):
                setuphelpers.service_stop('waptPostgresql')
            setuphelpers.service_delete('waptPostgresql')

        cmd = r'"%s\bin\pg_ctl" register -N WAPTPostgresql -U "nt authority\networkservice" -S auto -D "%s"  ' % (pgsql_root_dir ,os.path.join(wapt_root_dir,'waptserver','pgsql_data'))
        print cmd
        run(cmd)
        setuphelpers.run(r'icacls "%s" /grant  "*S-1-5-20":(OI)(CI)(M)' % log_directory)
        setuphelpers.run(r'icacls "%s" /grant  "*S-1-5-20":(OI)(CI)(M)' % pgsql_data_dir)
    else:
        print("database already instanciated, doing nothing")

    print('starting postgresql')
    if not setuphelpers.service_is_running('waptpostgresql'):
        setuphelpers.service_start('waptpostgresql')
        # waiting for postgres to be ready
        time.sleep(2)

    print("creating wapt database")
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    conn = None
    cur = None
    try:
        conn = psycopg2.connect('dbname=template1 user=postgres')
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        cur.execute("select 1 from pg_roles where rolname='%(db_user)s'" % conf)
        val = cur.fetchone()
        if val is None:
            print("%(db_user)s pgsql user does not exists, creating %(db_user)s user" % conf)
            cur.execute("create user %(db_user)s" % conf)

        cur.execute("select 1 from pg_database where datname='%(db_name)s'" % conf)
        val = cur.fetchone()
        if val is None:
            print ("database %(db_name)s does not exists, creating %(db_name)s db" % conf)
            cur.execute("create database %(db_name)s owner %(db_user)s" % conf)

    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

    print("Creating/upgrading wapt tables")
    run(r'"%s\waptpython.exe" "%s\waptserver\model.py" init_db -c "%s"' % (wapt_root_dir, wapt_root_dir, options.configfile ))
    print("Done")

def install_waptserver_service(options,conf=None):
    if conf is None:
        conf = waptserver.config.load_config(options.configfile)
    print("install waptserver")
    service_binary = os.path.abspath(os.path.join(wapt_root_dir,'waptpython.exe'))
    service_parameters = '"%s"' % os.path.join(wapt_root_dir,'waptserver','server.py')
    service_logfile = os.path.join(log_directory, 'nssm_waptserver.log')
    service_dependencies = 'WAPTPostgresql'
    install_windows_nssm_service('WAPTServer',service_binary,service_parameters,service_logfile,service_dependencies)

    if not conf.get('secret_key'):
        conf['secret_key'] = ''.join(random.SystemRandom().choice(string.letters + string.digits) for _ in range(64))
        waptserver.config.write_config_file(options.configfile,conf)

if __name__ == '__main__':
    usage = """\
    %prog [-c configfile] [install_nginx install_postgresql install_waptserver]

    WAPT Server services setup.

    actions is either :
      <nothing> : run service in foreground
      install   : install as a Windows service managed by nssm
      uninstall : uninstall Windows service managed by nssm

    """

    config_filename  = os.path.join(wapt_root_dir, 'conf', 'waptserver.ini')

    parser = OptionParser(usage=usage, version='winsetup.py ' + __version__)
    parser.add_option('-c','--config',dest='configfile',default=config_filename,
           help='Config file full path (default: %default)')

    parser.add_option('-l','--loglevel',dest='loglevel',default=None,type='choice',
            choices=['debug',   'warning','info','error','critical'],
            metavar='LOGLEVEL',help='Loglevel (default: warning)')
    parser.add_option('-d','--devel',dest='devel',default=False,action='store_true',
            help='Enable debug mode (for development only)')
    parser.add_option('-f','--force',dest='force',default=False,action='store_true',
            help='Force rewrite nginx config')

    (options, args) = parser.parse_args()
    logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s')

    if options.loglevel is not None:
        setloglevel(logger, options.loglevel)

    conf = waptserver.config.load_config(options.configfile)

    if conf['wapt_folder'].endswith('\\') or conf['wapt_folder'].endswith('/'):
        conf['wapt_folder'] = conf['wapt_folder'][:-1]

    log_directory = os.path.join(wapt_root_dir, 'log')
    if not os.path.exists(log_directory):
        os.mkdir(log_directory)

    if args == ['all']:
        args = ['install_nginx','install_postgresql','install_waptserver']

    for action in args:
        if action == 'install_nginx':
            print('Installing postgresql as a service managed by nssm')
            install_nginx_service(options,conf)
        elif action == 'install_postgresql':
            print('Installing NGINX as a service managed by nssm')
            install_postgresql_service(options,conf)
        elif action == 'install_waptserver':
            print('Installing WAPT Server as a service managed by nssm')
            install_waptserver_service(options,conf)
    setuphelpers.run(r'icacls "%s" /t /grant  "*S-1-5-20":(OI)(CI)(M)' % os.path.join(wapt_root_dir,'conf'))
    setuphelpers.run(r'icacls "%s" /t /grant  "*S-1-5-20":(OI)(CI)(M)' % os.path.join(wapt_root_dir,'log'))

