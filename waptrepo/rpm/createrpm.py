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
import os
import glob
import sys
import stat
import shutil
import fileinput
import subprocess
import platform
import errno

def run(*args, **kwargs):
    return subprocess.check_output(*args, shell=True, **kwargs)

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def run_verbose(*args, **kwargs):
    output =  subprocess.check_output(*args, shell=True, **kwargs)
    eprint(output)
    return output

def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:  # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise

def replaceAll(file, searchExp, replaceExp):
    for line in fileinput.input(file, inplace=1):
        if searchExp in line:
            line = line.replace(searchExp, replaceExp)
        sys.stdout.write(line)

def rsync(src, dst, excludes=[]):
    rsync_option = " --exclude 'postconf' --exclude 'mongodb' --exclude 'rpm' --exclude '*.pyc' --exclude '*.pyo' --exclude '.svn' --exclude 'apache-win32' --exclude 'deb' --exclude '.git' --exclude '.gitignore' -a --stats"
    if excludes:
        rsync_option = rsync_option + \
            ' '.join(" --exclude '%s'" % x for x in excludes)
    rsync_source = src
    rsync_destination = dst
    rsync_command = '/usr/bin/rsync %s "%s" "%s" 1>&2' % (
        rsync_option, rsync_source, rsync_destination)
    eprint(rsync_command)
    os.system(rsync_command)


makepath = os.path.join
from shutil import copyfile

# wapt
wapt_source_dir = os.path.abspath('../..')

# waptrepo
source_dir = os.path.abspath('..')

if platform.system() != 'Linux':
    eprint('this script should be used on debian linux')
    sys.exit(1)

if len(sys.argv) > 2:
    eprint('wrong number of parameters (0 or 1)')
    sys.exit(1)

new_umask = 022
old_umask = os.umask(new_umask)
if new_umask != old_umask:
    eprint('umask fixed (previous %03o, current %03o)' %
          (old_umask, new_umask))

def check_if_package_is_installed(package_name):
    # issue with yum module in buildbot, using dirty subprocess way...
    try:
        data = run('rpm -q %s' % package_name)
    except:
        return False
    if data.strip().startswith('%s-' % package_name):
        return True
    else:
        return False


if (not check_if_package_is_installed('python-virtualenv')
    or not check_if_package_is_installed('gcc')
    or not check_if_package_is_installed('openssl-devel')
    or not check_if_package_is_installed('libffi-devel')
    or not check_if_package_is_installed('openldap-devel')
    ):
    eprint("""
#########################################################################################################################
     Please install build time packages first:
        yum install -y python-virtualenv gcc libffi-devel openssl-devel openldap-devel python-pip postgresql-devel.x86_64
#########################################################################################################################
""")
    sys.exit(1)

eprint('creating the package tree')

if os.path.exists('builddir'):
    eprint('cleaning up builddir directory')
    shutil.rmtree('builddir')

mkdir_p('builddir/opt/wapt/lib')
mkdir_p('builddir/opt/wapt/conf')
mkdir_p('builddir/opt/wapt/log')
mkdir_p('builddir/opt/wapt/lib/python2.7/site-packages')
mkdir_p('builddir/usr/bin')

# we use pip and virtualenv to get the wapt dependencies. virtualenv usage here is a bit awkward, it can probably be improved. For instance, it install a outdated version of pip that cannot install Rocket dependencies...
# for some reason the virtualenv does not build itself right if we don't
# have pip systemwide...
eprint(
    'Create a build environment virtualenv. May need to download a few libraries, it may take some time')

run_verbose(r'virtualenv ./builddir/opt/wapt/')
run_verbose('pip install --upgrade pip')
eprint('Install additional libraries in build environment virtualenv')
run_verbose(r'source ./builddir/opt/wapt/bin/activate ;curl https://bootstrap.pypa.io/ez_setup.py | python')
run_verbose(r'source ./builddir/opt/wapt/bin/activate ;pip install pip setuptools --upgrade')

# fix for psycopg install because of ImportError: libpq-9c51d239.so.5.9: ELF load command address/offset not properly aligned
run_verbose(r'source ./builddir/opt/wapt/bin/activate ;pip install psycopg2==2.7.3.2 --no-binary :all: ')
run_verbose(r'source ./builddir/opt/wapt/bin/activate; pip install -r ../../requirements-server.txt')

eprint('copying the waptrepo files')

rsync(source_dir, './builddir/opt/wapt/',excludes=['postconf', 'mongod.exe', 'include','spnego-http-auth-nginx-module'])

eprint('cryptography patches')
mkdir_p('./builddir/opt/wapt/lib/python2.7/site-packages/cryptography/x509/')
copyfile(makepath(wapt_source_dir, 'utils', 'patch-cryptography', '__init__.py'),
         'builddir/opt/wapt/lib/python2.7/site-packages/cryptography/x509/__init__.py')
copyfile(makepath(wapt_source_dir, 'utils', 'patch-cryptography', 'verification.py'),
         'builddir/opt/wapt/lib/python2.7/site-packages/cryptography/x509/verification.py')

eprint('copying files formerly from waptrepo')
copyfile(makepath(wapt_source_dir, 'waptcrypto.py'),
         'builddir/opt/wapt/waptcrypto.py')
copyfile(makepath(wapt_source_dir, 'waptutils.py'),
         'builddir/opt/wapt/waptutils.py')
copyfile(makepath(wapt_source_dir, 'waptpackage.py'),
         'builddir/opt/wapt/waptpackage.py')
copyfile(makepath(wapt_source_dir, 'wapt-scanpackages.py'),
         'builddir/opt/wapt/wapt-scanpackages.py')
copyfile(makepath(wapt_source_dir, 'wapt-signpackages.py'),
         'builddir/opt/wapt/wapt-signpackages.py')
copyfile(makepath(wapt_source_dir, 'custom_zip.py'),
         'builddir/opt/wapt/custom_zip.py')

copyfile(makepath(wapt_source_dir, 'wapt-scanpackages'),'./builddir/usr/bin/wapt-scanpackages')
copyfile(makepath(wapt_source_dir, 'wapt-signpackages'),'./builddir/usr/bin/wapt-signpackages')
copyfile(makepath(wapt_source_dir, 'waptpython'),'./builddir/usr/bin/waptpython')
os.chmod('./builddir/usr/bin/wapt-scanpackages', 0o755)
os.chmod('./builddir/usr/bin/wapt-signpackages', 0o755)
os.chmod('./builddir/usr/bin/waptpython', 0o755)
