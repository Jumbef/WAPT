# -*- coding: utf-8 -*-
from setuphelpers import *
import os
import _winreg
import tempfile
import hashlib
import time
import platform
import codecs
import types
import re

# registry key(s) where WAPT will find how to remove the application(s)
uninstallkey = []


def update_registry_version(version):
    # updatethe registry
    with _winreg.CreateKeyEx(HKEY_LOCAL_MACHINE,r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\WAPT_is1',\
            0, _winreg.KEY_READ| _winreg.KEY_WRITE ) as waptis:
        reg_setvalue(waptis,"DisplayName","WAPT %s" % version)
        reg_setvalue(waptis,"DisplayVersion","%s" % version)
        reg_setvalue(waptis,"InstallDate",currentdate())


def sha256_for_file(fname, block_size=2**20):
    f = open(fname,'rb')
    sha256 = hashlib.sha256()
    while True:
        data = f.read(block_size)
        if not data:
            break
        sha256.update(data)
    return sha256.hexdigest()

class Version(object):
    """Version object of form 0.0.0
    can compare with respect to natural numbering and not alphabetical

    >>> Version('0.10.2') > Version('0.2.5')
    True
    >>> Version('0.1.2') < Version('0.2.5')
    True
    >>> Version('0.1.2') == Version('0.1.2')
    True
    >>> Version('7') < Version('7.1')
    True
    """

    def __init__(self,version,members_count=None):
        if version is None:
            version = ''
        assert isinstance(version,types.ModuleType) or isinstance(version,str) or isinstance(version,unicode) or isinstance(version,Version)
        if isinstance(version,types.ModuleType):
            self.versionstring =  getattr(version,'__version__',None)
        elif isinstance(version,Version):
            self.versionstring = getattr(version,'versionstring',None)
        else:
            self.versionstring = version
        self.members = [ v.strip() for v in self.versionstring.split('.')]
        self.members_count = members_count
        if members_count is not None:
            if len(self.members)<members_count:
                self.members.extend(['0'] * (members_count-len(self.members)))
            else:
                self.members = self.members[0:members_count]

    def __cmp__(self,aversion):
        def nat_cmp(a, b):
            a = a or ''
            b = b or ''

            def convert(text):
                if text.isdigit():
                    return int(text)
                else:
                    return text.lower()

            def alphanum_key(key):
                return [convert(c) for c in re.split('([0-9]+)', key)]

            return cmp(alphanum_key(a), alphanum_key(b))

        if not isinstance(aversion,Version):
            aversion = Version(aversion,self.members_count)
        for i in range(0,max([len(self.members),len(aversion.members)])):
            if i<len(self.members):
                i1 = self.members[i]
            else:
                i1 = ''
            if i<len(aversion.members):
                i2 = aversion.members[i]
            else:
                i2=''
            v = nat_cmp(i1,i2)
            if v:
                return v
        return 0

    def __str__(self):
        return '.'.join(self.members)

    def __repr__(self):
        return "Version('{}')".format('.'.join(self.members))



def download_waptagent(waptagent_path,expected_sha256):
    if WAPT.repositories:
        for r in WAPT.repositories:
            try:
                waptagent_url = "%s/waptagent.exe" % r.repo_url
                print('Trying %s'%waptagent_url)
                print wget(waptagent_url,waptagent_path)
                wapt_agent_sha256 =  sha256_for_file(waptagent_path)
                # eefac39c40fdb2feb4aa920727a43d48817eb4df   waptagent.exe
                if expected_sha256 != wapt_agent_sha256:
                    print('Error : bad  SHA256 for the downloaded waptagent.exe\n Expected : %s \n Found : %s '%(expected_sha256,wapt_agent_sha256))
                    continue
                return waptagent_url
            except Exception as e:
                print('Error when trying %s: %s'%(r.name,e))
        error('No proper waptagent downlaoded')
    error('No repository found for the download of waptagent.exe')


def windows_version(members_count=None):
    """see https://msdn.microsoft.com/en-us/library/windows/desktop/ms724832(v=vs.85).aspx"""
    try:
        return Version(platform.win32_ver()[1],members_count)
    except:
        return Version(platform.win32_ver()[1])

# recopied here as the code in wapt < 1.3.12 is bugged in case of decode errors.
def run_notfatal(*cmd,**args):
    """Runs the command and wait for it termination, returns output
    Ignore exit status code of command, return '' instead
    """
    try:
        return run(*cmd,**args)
    except Exception as e:
        print('Warning : %s' % repr(e))
        return ''


TASK_TEMPLATE="""\
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>%(created_on)s</Date>
    <Author>WAPT</Author>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>%(run_on)s</StartBoundary>
      <EndBoundary>%(expired_on)s</EndBoundary>
      <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
      <Enabled>true</Enabled>
    </TimeTrigger>
    <BootTrigger>
      <StartBoundary>%(run_on)s</StartBoundary>
      <EndBoundary>%(expired_on)s</EndBoundary>
      <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
      <Enabled>true</Enabled>
    </BootTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <DeleteExpiredTaskAfter>PT0S</DeleteExpiredTaskAfter>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>%(cmd)s</Command>
      <Arguments>%(parameters)s</Arguments>
    </Exec>
  </Actions>
</Task>
"""

def create_onetime_task(name,cmd,parameters=None, delay_minutes=2,max_runtime=10, retry_count=3,retry_delay_minutes=1):
    """creates a one time Windows scheduled task and activate it.
    """
    run_time = time.localtime(time.time() + delay_minutes*60)
    # task

    if windows_version(2) <= Version('5.2',2):
        # for win XP
        system_account = r'"NT AUTHORITY\SYSTEM"'
        # windows xp doesn't support one time startup task /Z nor /F
        hour_min = time.strftime('%H:%M:%S', run_time)
        run_notfatal('schtasks /Delete /TN "%s" /F'%name)
        return run('schtasks /Create /SC ONCE /TN "%s" /TR  "%s %s" /ST %s /RU %s' % (name,cmd,parameters,hour_min,system_account))
    else:
        system_account = 'SYSTEM'
        xmlfile = tempfile.mktemp('.xml')
        created_on = time.strftime('%Y-%m-%dT%H:%M:%S',time.localtime(time.time()))
        run_on = time.strftime('%Y-%m-%dT%H:%M:%S',run_time)
        expired_on = time.strftime('%Y-%m-%dT%H:%M:%S',time.localtime(time.time() + 90*24*3600))
        codecs.open(xmlfile,'wb',encoding='utf8').write(TASK_TEMPLATE % locals())
        result = run('schtasks /Create /F /TN "%s" /XML "%s"' % (name,xmlfile))
        if isfile(xmlfile):
            remove_file(xmlfile)
        return result


def full_waptagent_install(min_version,at_startup=False):
    # get it from
    waptagent_path = makepath(tempfile.gettempdir(),'waptagent.exe')
    waptdeploy_path = makepath(tempfile.gettempdir(),'waptdeploy.exe')
    if isfile(waptdeploy_path):
        killalltasks('waptdeploy.exe')
        killalltasks('waptagent.exe')
        remove_file(waptdeploy_path)

    filecopyto(makepath('patchs','waptdeploy.exe'),waptdeploy_path)

    expected_sha256 = open('waptagent.sha256','r').read().splitlines()[0].split()[0]
    if isfile('waptagent.exe'):
        filecopyto('waptagent.exe',waptagent_path)
    if not isfile(waptagent_path) or sha256_for_file(waptagent_path) != expected_sha256:
        download_waptagent(waptagent_path,expected_sha256)
    #create_onetime_task('fullwaptupgrade',waptagent_path,'/VERYSILENT',delay_minutes=15)

    if at_startup or isrunning('waptexit.exe'):
        cmd = '%s --hash=%s --waptsetupurl=%s --wait=15 --temporary --force --minversion=%s' %(waptdeploy_path,expected_sha256,waptagent_path,min_version)
        if not at_startup:
            print('waptexit is running, scheduling a one time task at system startup with command %s'%cmd)
        # task at system startup
        try:
            print run('schtasks /Create /RU SYSTEM /SC ONSTART /TN fullwaptupgrade /TR "%s" /F /V1 /Z' % cmd)
        except:
            # windows xp doesn't support one time startup task /Z nor /F
            run_notfatal('schtasks /Delete /TN fullwaptupgrade /F')
            print run('schtasks /Create /RU SYSTEM /SC ONSTART /TN fullwaptupgrade /TR "%s"' % cmd)
    else:
        # use embedded waptagent.exe, wait 15 minutes for other tasks to complete.
        print create_onetime_task('fullwaptupgrade',waptdeploy_path,'--hash=%s --waptsetupurl=%s --wait=15 --temporary --force --minversion=%s'%(expected_sha256,waptagent_path,min_version),delay_minutes=1)

force = False

def install():
    # if you want to modify the keys depending on environment (win32/win64... params..)
    if installed_softwares('WAPT Server_is1'):
        error('Wapt server installed on this host. Aborting')

    status = WAPT.wapt_status()
    installed_wapt_version = status['wapt-exe-version']

    # get upgrade package informations
    (package_wapt_version,package_packaging) = control.version.split('-')
    package_packaging = int(package_packaging)

    if not force and Version(installed_wapt_version,4) >= Version(package_wapt_version,4):
        print('Your current wapt (%s) is same or more recent than the upgrade package (%s). Skipping...'%(installed_wapt_version,control.version))
    else:
        print('Setting up upgrade from wapt version %s to %s. waptagent install planned for %s'%(installed_wapt_version,package_wapt_version,time.ctime(time.time() + 1*60)))
        full_waptagent_install(str(Version(package_wapt_version,4)))
        update_registry_version(package_wapt_version)

if __name__ == '__main__':
    pass
