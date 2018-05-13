# -*- coding: utf-8 -*-
from setuphelpers import *

uninstallkey = []

def install():
    print('installing %%s' %% control.asrequirement())
    # put here what to do when package is installed on host
    # implicit context variables are WAPT, basedir, control, user, params, run

def uninstall():
    print('uninstalling %%s' %% control.asrequirement())
    # put here what to do when package is removed from host
    # implicit context variables are WAPT, control, user, params, run

def session_setup():
    print('Session setup for %%s' %% control.asrequirement())
    # put here what to do when package is configured inside a user session
    # implicit context variables are WAPT, control, user, params

def update_package():
    print('Update package content from upstream binary sources')
    # put here what to do to update package content with newer installers.
    # launched with command wapt-get update-package-sources <path-to-wapt-directory>
    # implicit context variables are WAPT, basedir, control, user, params, run
    # if attributes in control are changed, they should be explicitly saved to package file with control.save_control_to_wapt()

def audit():
    print('Auditing %%s' %% control.asrequirement())
    # put here code to check periodically that state is matching expectations
    # return "OK", "WARNING" or "ERROR" to report status in console.
    # all print statement are reported too

if __name__ == '__main__':
    update_package()

