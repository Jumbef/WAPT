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
from __future__ import print_function
import sys
import os
import platform
import logging
import re

import shutil
import subprocess
import argparse
import stat
import glob

from git import Repo

makepath = os.path.join
from shutil import copyfile


def run(*args, **kwargs):
    return subprocess.check_output(*args, shell=True, **kwargs)

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def run_verbose(*args, **kwargs):
    output =  subprocess.check_output(*args, shell=True, **kwargs)
    eprint(output)
    return output

def mkdir_p(path):
    if not os.path.isdir(path):
        os.makedirs(path)

def debian_major():
    return platform.linux_distribution()[1].split('.')[0]

def git_hash():
    r = Repo('.',search_parent_directories=True)
    return '%04d-%s' % (r.active_branch.commit.count(),r.active_branch.object.name_rev[:8])

def dev_revision():
    return 'tisdeb%s-%s' % (debian_major(), git_hash())

def setloglevel(alogger,loglevel):
    """set loglevel as string"""
    if loglevel in ('debug','warning','info','error','critical'):
        numeric_level = getattr(logging, loglevel.upper(), None)
        if not isinstance(numeric_level, int):
            raise ValueError('Invalid log level: %s' % loglevel)
        alogger.setLevel(numeric_level)

def rsync(src, dst, excludes=[]):
    excludes_list = ['*.pyc','*~','.svn','deb','.git','.gitignore']
    excludes_list.extend(excludes)

    rsync_source = src
    rsync_destination = dst
    rsync_options = ['-a','--stats']
    for x in excludes_list:
        rsync_options.extend(['--exclude',x])

    rsync_command = ['/usr/bin/rsync'] + rsync_options + [rsync_source,rsync_destination]
    eprint(rsync_command)
    return subprocess.check_output(rsync_command)


def add_symlink(link_target,link_name):
    if link_target.startswith('/'):
        link_target = link_target[1:]
    relative_link_target_path = os.path.join('builddir',link_target)
    eprint("adding symlink %s -> %s" % (link_name, relative_link_target_path ))
    mkdir_p(os.path.dirname(relative_link_target_path))

    if not os.path.exists(relative_link_target_path):
        cmd = 'ln -s %s %s ' % (relative_link_target_path,link_name)
        eprint(cmd)
        eprint(subprocess.check_output(cmd))

parser = argparse.ArgumentParser(u'Build a WaptServer Debian package.')
parser.add_argument('-l', '--loglevel', help='Change log level (error, warning, info, debug...)')
parser.add_argument('-r', '--revision',default=dev_revision(), help='revision to append to package version')
options = parser.parse_args()

logger = logging.getLogger()
logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s')
if options.loglevel is not None:
    setloglevel(logger,options.loglevel)

if platform.system() != 'Linux':
    logger.error("this script should be used on debian linux")
    sys.exit(1)

revision = options.revision

####################""
# wapt
wapt_source_dir = os.path.abspath('../..')

# waptrepo
source_dir = os.path.abspath('..')

new_umask = 022
old_umask = os.umask(new_umask)
if new_umask != old_umask:
    eprint('umask fixed (previous %03o, current %03o)' % (old_umask, new_umask))

for line in open('%s/waptserver_config.py' % source_dir):
    if line.strip().startswith('__version__'):
        wapt_version = line.split('=')[
            1].strip().replace('"', '').replace("'", '')

if not wapt_version:
    eprint(u'version not found in %s/waptserver.py' % os.path.abspath('..'))
    sys.exit(1)

if options.revision:
    full_version = wapt_version + '-' + options.revision
else:
    full_version = wapt_version


for filename in glob.glob('tis-waptserver*.deb'):
    eprint('Removing %s' % filename)
    os.remove(filename)

if os.path.exists('builddir'):
    shutil.rmtree('builddir')

eprint('creating the package tree')
mkdir_p('builddir/DEBIAN')
mkdir_p('builddir/opt/wapt/conf')
mkdir_p('builddir/opt/wapt/lib')
mkdir_p('builddir/opt/wapt/log')
mkdir_p('builddir/opt/wapt/lib/python2.7/site-packages')
mkdir_p('builddir/opt/wapt/waptserver')
mkdir_p('builddir/usr/bin/')

WAPTEDITION=os.environ.get('WAPTEDITION','community')
if WAPTEDITION=='enterprise':
    mkdir_p('builddir/opt/wapt/waptenterprise')


open(os.path.join('./builddir/opt/wapt/waptserver','VERSION'),'w').write(full_version)

# for some reason the virtualenv does not build itself right if we don't
# have pip systemwide...
eprint(run('sudo apt-get install -y python-virtualenv python-setuptools python-pip python-dev libpq-dev libffi-dev libldap2-dev libsasl2-dev'))

eprint('Create a build environment virtualenv. May need to download a few libraries, it may take some time')
run_verbose(r'virtualenv ./builddir/opt/wapt --always-copy')

eprint('Install additional libraries in build environment virtualenv')

run_verbose('./builddir/opt/wapt/bin/pip install pip setuptools --upgrade')

run('./builddir/opt/wapt/bin/pip install -r ../../requirements-server.txt -t ./builddir/opt/wapt/lib/python2.7/site-packages')

run_verbose(r'virtualenv ./builddir/opt/wapt --relocatable')

eprint('copying the waptrepo files')
copyfile(makepath(wapt_source_dir, 'waptcrypto.py'),'./builddir/opt/wapt/waptcrypto.py')
copyfile(makepath(wapt_source_dir, 'waptutils.py'),'./builddir/opt/wapt/waptutils.py')
copyfile(makepath(wapt_source_dir, 'custom_zip.py'),'./builddir/opt/wapt/custom_zip.py')
copyfile(makepath(wapt_source_dir, 'waptpackage.py'),'./builddir/opt/wapt/waptpackage.py')
copyfile(makepath(wapt_source_dir, 'wapt-scanpackages.py'),'./builddir/opt/wapt/wapt-scanpackages.py')
copyfile(makepath(wapt_source_dir, 'wapt-signpackages.py'),'./builddir/opt/wapt/wapt-signpackages.py')

copyfile(makepath(wapt_source_dir, 'wapt-scanpackages'),'./builddir/usr/bin/wapt-scanpackages')
copyfile(makepath(wapt_source_dir, 'wapt-signpackages'),'./builddir/usr/bin/wapt-signpackages')

eprint('cryptography patches')
copyfile(makepath(wapt_source_dir, 'utils', 'patch-cryptography', '__init__.py'),'./builddir/opt/wapt/lib/python2.7/site-packages/cryptography/x509/__init__.py')
copyfile(makepath(wapt_source_dir, 'utils', 'patch-cryptography', 'verification.py'),'./builddir/opt/wapt/lib/python2.7/site-packages/cryptography/x509/verification.py')

eprint('copying the waptserver files')
rsync(source_dir, './builddir/opt/wapt/',
      excludes=['postconf', 'repository', 'rpm', 'deb', 'spnego-http-auth-nginx-module', '*.bat'])

if WAPTEDITION=='enterprise':
    eprint('copying the waptserver enterprise files')
    rsync(wapt_source_dir+'/waptenterprise/', './builddir/opt/wapt/waptenterprise/',
          excludes=[' ','waptservice','postconf', 'repository', 'rpm', 'deb', 'spnego-http-auth-nginx-module', '*.bat'])


# script to run waptserver in foreground mode
copyfile(makepath(wapt_source_dir, 'runwaptserver.sh'),'./builddir/opt/wapt/runwaptserver.sh')
copyfile(makepath(wapt_source_dir, 'waptpython'),'./builddir/usr/bin/waptpython')

for lib in ('dialog.py', ):
    rsync(makepath(wapt_source_dir, 'lib', 'site-packages', lib),
          './builddir/opt/wapt/lib/python2.7/site-packages/')

eprint('copying control and postinst package metadata')

if WAPTEDITION=='enterprise':
    copyfile('./DEBIAN/control_enterprise', './builddir/DEBIAN/control')
else:
    copyfile('./DEBIAN/control', './builddir/DEBIAN/control')
copyfile('./DEBIAN/postinst', './builddir/DEBIAN/postinst')
copyfile('./DEBIAN/preinst', './builddir/DEBIAN/preinst')

eprint(run(r'find ./builddir/opt/wapt/ -type f -exec chmod 644 {} \;'))
eprint(run(r'find ./builddir/opt/wapt/ -type d -exec chmod 755 {} \;'))

eprint('copying systemd startup script')
systemd_build_dest_dir = './builddir/usr/lib/systemd/system/'
try:
    mkdir_p(systemd_build_dest_dir)
    copyfile('../scripts/waptserver.service', os.path.join(systemd_build_dest_dir, 'waptserver.service'))
except Exception as e:
    eprint('error: \n%s' % e)
    exit(1)

eprint('copying logrotate script /etc/logrotate.d/waptserver')
try:
    mkdir_p('./builddir/etc/logrotate.d/')
    shutil.copyfile('../scripts/waptserver-logrotate',
                    './builddir/etc/logrotate.d/waptserver')
    #eprint(run('chown root:root ./builddir/etc/logrotate.d/waptserver'))
except Exception as e:
    eprint('error: \n%s' % e)
    exit(1)

eprint('copying logrotate script /etc/rsyslog.d/waptserver.conf')
try:
    mkdir_p('./builddir/etc/rsyslog.d/')
    shutil.copyfile('../scripts/waptserver-rsyslog',
                    './builddir/etc/rsyslog.d/waptserver.conf')
    #eprint(run('chown root:root ./builddir/etc/rsyslog.d/waptserver.conf'))
except Exception as e:
    eprint('error: \n%s' % e)
    exit(1)

os.chmod('./builddir/opt/wapt/waptserver/scripts/postconf.sh', 0o755)
for fn in glob.glob('./builddir/opt/wapt/bin/*'):
    os.chmod(fn, 0o755)

add_symlink('opt/wapt/waptserver/scripts/postconf.sh', '/usr/bin/wapt-serverpostconf')

eprint('copying nginx-related goo')
try:
    mkdir_p('./builddir/etc/systemd/system/nginx.service.d')
    copyfile('../scripts/nginx_worker_files_limit.conf', './builddir/etc/systemd/system/nginx.service.d/nginx_worker_files_limit.conf')
except Exception as e:
    eprint('error: \n%s' % e)
    exit(1)

control_file = './builddir/DEBIAN/control'
eprint(u'inscription de la version dans le fichier de control. new version: ' + full_version)

# update Control version
control = open(control_file,'r').read()
open(control_file,'w').write(re.sub('Version: .*','Version: %s' % full_version,control))

os.chmod('./builddir/DEBIAN/postinst', stat.S_IRWXU |
         stat.S_IXGRP | stat.S_IRGRP | stat.S_IROTH | stat.S_IXOTH)
os.chmod('./builddir/DEBIAN/preinst', stat.S_IRWXU |
         stat.S_IXGRP | stat.S_IRGRP | stat.S_IROTH | stat.S_IXOTH)

# build
if WAPTEDITION=='enterprise':
    package_filename = 'tis-waptserver-enterprise-%s.deb' % full_version
else:
    package_filename = 'tis-waptserver-%s.deb' % full_version
eprint(subprocess.check_output(['dpkg-deb','--build','builddir',package_filename]))
shutil.rmtree("builddir")
print(package_filename)
