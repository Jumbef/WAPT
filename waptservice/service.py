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
from __future__ import print_function

from builtins import str
from future import standard_library
standard_library.install_aliases()

import os
import sys
import time

python_version = (sys.version_info.major, sys.version_info.minor)
if python_version == (2, 7) or python_version == (3,5) or python_version == (3,6):
    pass
else:
    raise Exception('waptservice supports only Python 2.7 and 3.3 and above, not %d.%d' % python_version)

try:
    wapt_root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__),'..'))
except NameError:
    wapt_root_dir = 'c:/tranquilit/wapt'

if sys.platform.startswith('linux') or sys.platform.startswith('darwin'):
    del os.environ['PYTHONPATH']
    del os.environ['PYTHONHOME']

from waptutils import __version__

from optparse import OptionParser

from waitress import serve

import hashlib

# flask
from flask import request, Flask,Response, send_from_directory, session, g, redirect, url_for, render_template

import jinja2
from werkzeug.utils import secure_filename
from werkzeug.utils import html

from functools import wraps

import logging
import sqlite3

import json
import threading
import queue
import traceback

import datetime

if sys.platform == 'win32':
    import pythoncom
    import win32security

import ctypes

# wapt specific stuff
from waptutils import setloglevel, ensure_list, ensure_unicode, jsondump, LogOutput, get_time_delta

import common
from common import Wapt
import setuphelpers
from setuphelpers import Version
from waptpackage import PackageEntry,WaptLocalRepo

from waptservice.waptservice_common import waptconfig
from waptservice.waptservice_common import forbidden,authenticate,allow_local
from waptservice.waptservice_common import WaptClientUpgrade,WaptServiceRestart,WaptNetworkReconfig,WaptPackageInstall
from waptservice.waptservice_common import WaptUpgrade,WaptUpdate,WaptUpdateServerStatus,WaptCleanup,WaptDownloadPackage,WaptLongTask,WaptAuditPackage
from waptservice.waptservice_common import WaptRegisterComputer,WaptPackageRemove,WaptPackageForget
from waptservice.waptservice_common import WaptEvents

from waptservice.waptservice_socketio import WaptSocketIOClient

if sys.platform == 'win32':
    if os.path.isdir(os.path.join(wapt_root_dir,'waptenterprise')):
        from waptenterprise.waptservice.enterprise import get_active_sessions,start_interactive_process  # pylint: disable=import-error
        from waptenterprise.waptservice.enterprise import WaptGPUpdate,WaptWUAScanTask,WaptWUADowloadTask,WaptWUAInstallTask  # pylint: disable=import-error
        from waptenterprise.waptservice.enterprise import run_cleanmgr,WaptRunCleanMgr # pylint: disable=import-error
        from waptenterprise.waptservice.enterprise import run_scheduled_wua_scan,run_scheduled_wua_downloads,run_scheduled_wua_installs # pylint: disable=import-error
        from waptenterprise.waptservice.enterprise import waptwua_api
        from waptenterprise import enterprise_common
    else:
        waptwua_api = None
        enterprise_common = None

if os.path.isdir(os.path.join(wapt_root_dir,'waptenterprise')):
    from waptenterprise.waptservice.repositories import WaptSyncRepo,waptrepositories_api
else:
    waptrepositories_api = None

from waptservice.plugins import *

from flask_babel import Babel
try:
    from flask_babel import gettext
except ImportError:
    gettext = (lambda s:s)

# i18n
_ = gettext

logger = logging.getLogger()
logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s')

def format_isodate(isodate):
    """Pretty format iso date like : 2014-01-21T17:36:15.652000
        >>> format_isodate('2014-01-21T17:36:15.652000')
        '21/01/2014 17:36:15'
    """
    return isodate.replace('T',' ')[0:20]
    #dateutil.parser.parse(isodate).strftime("%d/%m/%Y %H:%M:%S")

def beautify(c):
    """return pretty html"""
    join = u"".join
    if c is None:
        return ""
    elif isinstance(c,(datetime.datetime,datetime.date)):
        return c.isoformat()
    elif isinstance(c,int):
        return '{}'.format(c)
    elif isinstance(c,float):
        return '{:.3}'.format(c)
    elif isinstance(c,str):
        return jinja2.Markup(c.replace('\r\n','<br>').replace('\n','<br>'))
    elif isinstance(c,str):
        return jinja2.Markup(ensure_unicode(c).replace('\r\n','<br>').replace('\n','<br>'))
    elif isinstance(c,PackageEntry):
        return jinja2.Markup('<a href="%s">%s</a>'%(url_for('package_details',package=c.asrequirement()), c.asrequirement()))
    elif isinstance(c,dict) or (hasattr(c,'keys') and callable(c.keys)):
        rows = []
        try:
            for key in list(c.keys()):
                rows.append(u'<li><b>{}</b>: {}</li>'.format(beautify(key),beautify(c[key])))
            return jinja2.Markup(u'<ul>{}</ul>'.format(join(rows)))
        except:
            pass
    elif isinstance(c, (list, tuple)):
        if c:
            rows = [u'<li>{}</li>'.format(beautify(item)) for item in c]
            return jinja2.Markup(u'<ul>{}</ul>'.format(join(rows)))
        else:
            return ''
    else:
        return jinja2.Markup(u"<pre>{}</pre>".format(ensure_unicode(c)))

app = Flask(__name__)
app.config['PROPAGATE_EXCEPTIONS'] = True
app.config['SECRET_KEY'] = waptconfig.secret_key

try:
    from waptenterprise.waptwua.client import WaptWUA,WaptWUAParams,WaptWUARules # pylint: disable=no-name-in-module
    #app.register_blueprint(WaptWUA.waptwua)
except Exception as e:
    WaptWUA = None
    WaptWUARules = None
    WaptWUAParams = None
    pass

if sys.platform == 'win32':
    if waptwua_api is not None:
        app.register_blueprint(waptwua_api)

if waptrepositories_api is not None:
        app.register_blueprint(waptrepositories_api)

app.jinja_env.filters['beautify'] = beautify # pylint: disable=no-member
app.waptconfig = waptconfig

app_babel = Babel(app)

def apply_host_settings(waptconfig):
    """Apply waptservice / waptexit specific settings
    """
    wapt = Wapt(config_filename = waptconfig.config_filename)
    try:
        if waptconfig.max_gpo_script_wait is not None and wapt.max_gpo_script_wait != waptconfig.max_gpo_script_wait:
            logger.info('Setting max_gpo_script_wait to %s'%waptconfig.max_gpo_script_wait)
            wapt.max_gpo_script_wait = waptconfig.max_gpo_script_wait
        if waptconfig.pre_shutdown_timeout is not None and wapt.pre_shutdown_timeout != waptconfig.pre_shutdown_timeout:
            logger.info('Setting pre_shutdown_timeout to %s'%waptconfig.pre_shutdown_timeout)
            wapt.pre_shutdown_timeout = waptconfig.pre_shutdown_timeout
        if waptconfig.hiberboot_enabled is not None and wapt.hiberboot_enabled != waptconfig.hiberboot_enabled:
            logger.info('Setting hiberboot_enabled to %s'%waptconfig.hiberboot_enabled)
            wapt.hiberboot_enabled = waptconfig.hiberboot_enabled
    except Exception as e:
        logger.critical('Unable to set shutdown policies : %s' % e)

def apply_waptwua_settings(waptconfig):
    """Apply waptwua service specific settings
    """
    wapt = Wapt(config_filename = waptconfig.config_filename)
    try:
        # check waptwua
        if WaptWUA is not None:
            c = WaptWUA(wapt)
            c.apply_waptwua_settings_to_host()

    except Exception as e:
        logger.critical('Unable to set waptwua policies : %s' % e)

def wapt():
    """Flask request contextual cached Wapt instance access"""
    if not hasattr(g,'wapt'):
        g.wapt = Wapt(config_filename = waptconfig.config_filename)
        if sys.platform == 'win32':
            apply_host_settings(waptconfig)
    # apply settings if changed at each wapt access...
    elif g.wapt.reload_config_if_updated():
        #apply waptservice / waptexit specific settings
        if sys.platform == 'win32':
            apply_host_settings(waptconfig)
    return g.wapt

@app.before_first_request
def before_first_request():
    pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)

@app.teardown_appcontext
def close_connection(exception):
    try:
        local_wapt = getattr(g, 'wapt', None)
        if local_wapt is not None and local_wapt._waptdb and local_wapt._waptdb.transaction_depth > 0:
            try:
                local_wapt._waptdb.commit()
                local_wapt._waptdb = None
            except:
                try:
                    local_wapt._waptdb.rollback()
                    local_wapt._waptdb = None
                except:
                    local_wapt._waptdb = None

    except Exception as e:
        logger.debug('Error in teardown, please consider upgrading Flask if <0.10. %s' % e)

#@app.after_request
#def add_header(response):
#    if is_static():
#       response.cache_control.max_age = 300
#    return response

def check_auth(logon_name, password,check_token_in_password=True,for_group='waptselfservice'):
    """This function is called to check if a username /
    password combination is valid against local waptservice admin configuration
    or Local Admins.

    If NOPASSWORD is set for wapt admin in wapt-get.ini, any user/password match
    (for waptstarter standalone usage)

    Returns:
        Handle : handle of user or logon_name
    """
    if app.waptconfig.waptservice_password != 'NOPASSWORD':
        if len(logon_name) ==0 or len(password)==0:
            return False
        domain = ''
        if logon_name.count('\\') > 1 or logon_name.count('@') > 1  or (logon_name.count('\\') == 1 and logon_name.count('@')==1)  :
            logger.debug(u"malformed logon credential : %s "% logon_name)
            return False

        if '\\' in logon_name:
            domain = logon_name.split('\\')[0]
            username = logon_name.split('\\')[1]
        elif '@' in logon_name:
            username = logon_name.split('@')[0]
            domain = logon_name.split('@')[1]
        else:
            username = logon_name
        logger.debug(u"Checking authentification for domain: %s user: %s" % (domain,username))

        if check_token_in_password:
            token_gen = wapt().get_secured_token_generator()
            try:
                token_content = token_gen.loads(password)
                if token_content['username'] != logon_name:
                    return False
                if not for_group in token_content.get('groups',[]):
                    return False
                logging.info("authenticated with token : %s. groups: %s" % (logon_name,token_content.get('groups')))
                return logon_name
            except:
                # password is not a token or token is invalid
                pass

        try:
            try:
                huser = win32security.LogonUser (
                    username.decode("utf-8"),
                    domain.decode('utf-8'),
                    password.decode("utf-8"),
                    win32security.LOGON32_LOGON_NETWORK_CLEARTEXT,
                    win32security.LOGON32_PROVIDER_DEFAULT
                )
            except Exception:
                raise Exception('WRONG_PASSWORD_USERNAME')
            #check if user is domain admins or member of waptselfservice admin
            try:
                domain_admins_group_name = common.get_domain_admins_group_name()
                if common.check_is_member_of(huser,domain_admins_group_name):
                    return huser
                if common.check_is_member_of(huser,for_group):
                    return huser
            except:
                pass

            if app.waptconfig.waptservice_admin_auth_allow:
                local_admins_group_name = common.get_local_admins_group_name()
                if common.check_is_member_of(huser,local_admins_group_name):
                    return huser

            if app.waptconfig.waptservice_password:
                logger.debug('auth using wapt local account')
                if app.waptconfig.waptservice_user == username and app.waptconfig.waptservice_password == hashlib.sha256(password).hexdigest():
                    return username

            return None

        except win32security.error:
            if app.waptconfig.waptservice_password:
                logger.debug('auth using wapt local account')
                if app.waptconfig.waptservice_user == username and app.waptconfig.waptservice_password == hashlib.sha256(password).hexdigest():
                    return username
                else:
                    raise Exception('BAD_AUTHENTICATION')
        else:
            raise Exception('BAD_AUTHENTICATION')
    else:
        return logon_name

def get_user_self_service_groups(self_service_groups,logon_name,password):
    """Authenticate a user and returns the self-service groups membership

    Args:
        self_service_groups (list): self service groups
        logon_name(str): Username of user
        password(str): Password of user

    Returns:
        list: of user's self service groups memberships ex: ['compta','tech']
    """

    domain = ''
    if logon_name.count('\\') > 1 or logon_name.count('@') > 1  or (logon_name.count('\\') == 1 and logon_name.count('@')==1)  :
        logger.debug(u"malformed logon credential : %s "% logon_name)
        return False

    try:
        w=wapt()
        serial=w.get_secured_token_generator()
        groups=serial.loads(password)
        groups=groups['groups']
        return groups
    except:
        if '\\' in logon_name:
            domain = logon_name.split('\\')[0]
            username = logon_name.split('\\')[1]
        elif '@' in logon_name:
            username = logon_name.split('@')[0]
            domain = logon_name.split('@')[1]
        else:
            username = logon_name

        huser = win32security.LogonUser(username.decode('utf-8'),domain.decode('utf-8'),password.decode('utf-8'),win32security.LOGON32_LOGON_NETWORK_CLEARTEXT,win32security.LOGON32_PROVIDER_DEFAULT)

        listgroupuser =  [username]
        for group in self_service_groups :
            if group in listgroupuser:
                continue
            if common.check_is_member_of(huser,group) :
                listgroupuser.append(group)
        return listgroupuser

def allow_local_auth(f):
    """Restrict access to localhost authenticated"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.remote_addr in ['127.0.0.1']:
            auth = request.authorization
            if not auth:
                logging.info('no credential given')
                return authenticate()
            logging.info("authenticating : %s" % auth.username)
            try:
                huser = check_auth(auth.username, auth.password)
                if huser is None:
                    logging.info("user %s authenticated" % auth.username)
                    return authenticate()
            except:
                return authenticate()
        else:
            return forbidden()
        return f(*args, **kwargs)
    return decorated


@app_babel.localeselector
def get_locale():
    browser_lang = request.accept_languages.best_match(['en', 'fr'])
    user_lang = session.get('lang',browser_lang)
    return user_lang

@app.route('/lang/<language>')
def lang(language=None):
    session['lang'] = language
    return redirect('/')

@app_babel.timezoneselector
def get_timezone():
    user = getattr(g, 'user', None)
    if user is not None:
        return user.timezone


@app.route('/ping')
@allow_local
def ping():
    if 'uuid' in request.args:
        w = wapt()
        data = dict(
            hostname = setuphelpers.get_hostname(),
            version=__version__,
            uuid = w.host_uuid,
            waptserver = w.waptserver,
            )
    else:
        data = dict(
            version=__version__,
            )
    return Response(common.jsondump(data), mimetype='application/json')

@app.route('/login')
@allow_local
def login():
    username = None
    groups = []
    w = wapt()

    rules = None
    if enterprise_common:
        rules = enterprise_common.self_service_rules(w)

    if request.authorization:
        auth = request.authorization
        token_gen = w.get_secured_token_generator()
        wapt_admin_group = 'waptselfservice'
        try:
            if check_auth(auth.username,auth.password,for_group=wapt_admin_group):
                username = auth.username
                groups = [wapt_admin_group]
                logger.debug(u'User %s authenticated against local wapt admins (%s)' % (auth.username,wapt_admin_group))
            else:
                if rules:
                    try:
                        groups = get_user_self_service_groups(list(rules.keys()),auth.username,auth.password)
                        username = auth.username
                        logger.debug(u'User %s authenticated against self-service groups %s' % (auth.username,groups))
                    except Exception as e:
                        return authenticate(msg = str(e))
                else:
                    return authenticate(msg = 'NO_RULES')
        except Exception as e:
            return authenticate(msg = str(e))
    else:
        return authenticate()

    token_gen = w.get_secured_token_generator()
    token = token_gen.dumps({'username':request.authorization.username,'groups':groups})
    return Response(common.jsondump({'token':token,'username':username,'groups':groups}),mimetype='application/json')


@app.route('/status')
@app.route('/status.json')
@allow_local
def status():
    rows = []
    with sqlite3.connect(app.waptconfig.dbpath) as con:
        try:
            con.row_factory=sqlite3.Row
            query = '''select s.package,s.version,s.install_date,
                                 s.install_status,s.install_output,r.description,
                                 (select GROUP_CONCAT(p.version,"|") from wapt_package p where p.package=s.package) as repo_versions,
                                 explicit_by as install_par
                                 from wapt_localstatus s
                                 left join wapt_package r on r.package=s.package and r.version=s.version and r.architecture=s.architecture and r.maturity=s.maturity
                                 order by s.package'''
            cur = con.cursor()
            cur.execute(query)
            rows = []
            search = request.args.get('q','')

            for row in cur.fetchall():
                pe = PackageEntry()
                rec_dict = dict((cur.description[idx][0], value) for idx, value in enumerate(row))
                for k in rec_dict:
                    setattr(pe,k,rec_dict[k])

                # hack to enable proper version comparison in templates
                pe.version = Version(pe.version)
                # calc most up to date repo version
                if pe.get('repo_versions',None) is not None:
                    pe.repo_version = max(Version(v) for v in pe.get('repo_versions','').split('|'))
                else:
                    pe.repo_version = None

                if not search or pe.match_search(search):
                    rows.append(pe)

        except sqlite3.Error as e:
            logger.critical(u"*********** Error %s:" % e.args[0])
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(rows), mimetype='application/json')
    else:
        return render_template('status.html',packages=rows,format_isodate=format_isodate,Version=setuphelpers.Version)


def latest_only(packages):
    index = {}
    for p in sorted(packages, reverse=True):
        if not p.package in index:
            p.previous = []
            index[p.package] = p
        else:
            index[p.package].previous.append(p)

    return list(index.values())

@app.route('/keywords.json')
@allow_local
def keywords():
    with sqlite3.connect(app.waptconfig.dbpath) as con:
        try:
            con.row_factory=sqlite3.Row
            cur = con.cursor()
            rows = []
            query = "select distinct trim(keywords) from wapt_package where keywords is not null and trim(keywords)<>''"
            rows = cur.execute(query)
            result = {}
            for k in rows:
                kws = k[0].lower().split(',')
                for kw in kws:
                    kwt = kw.strip().capitalize()
                    if not kwt in result:
                        result[kwt] = 1
                    else:
                        result[kwt] += 1
            return Response(common.jsondump(sorted(result.keys())), mimetype='application/json')
        except Exception as e:
            logger.critical(u'Error: %s' % e)
            return Response(common.jsondump([]), mimetype='application/json')

@app.route('/check_install')
@app.route('/check_install.json')
@allow_local
def check_install():
    try:
        package = request.args.get('package')
        data = wapt().check_install(package)
        return Response(common.jsondump(data), mimetype='application/json')
    except Exception as e:
        logger.critical(u"*********** Error %s:" % e.args[0])
        return Response(common.jsondump(e), mimetype='application/json')

@app.route('/list/pg<int:page>')
@app.route('/packages.json')
@app.route('/packages')
@app.route('/list')
@allow_local
def all_packages(page=1):
    if not (request.args.get('format','html')=='json' or request.path.endswith('.json')):
        if not request.authorization:
            return authenticate()

    grpuser = []

    rules = None
    if enterprise_common:
        rules = enterprise_common.self_service_rules(wapt())

    if request.authorization:
        auth = request.authorization
        try:
            if check_auth(auth.username,auth.password):
                grpuser.append('waptselfservice')
                logger.debug(u'User %s authenticated against local admins (waptselfservice)' % auth.username)
            else:
                grpuser = get_user_self_service_groups(list(rules.keys()),auth.username,auth.password)
                logger.debug(u'User %s authenticated against self-service groups %s' % (auth.username,grpuser))
        except:
            return authenticate()
    else:
        return authenticate()

    with sqlite3.connect(app.waptconfig.dbpath) as con:
        try:
            con.row_factory=sqlite3.Row
            query = '''\
                select
                    r.*,
                    s.version as install_version,s.install_status,s.install_date,s.explicit_by
                from wapt_package r
                left join wapt_localstatus s on s.package=r.package
                where not r.section in ("host","unit","profile","restricted","selfservice")
                order by r.package,r.version'''
            cur = con.cursor()
            cur.execute(query)
            rows = []

            search = request.args.get('q','').encode('utf8').replace('\\', '')
            keywords = ensure_list(request.args.get('keywords','').lower().encode('utf8'))

            for row in cur.fetchall():
                pe = PackageEntry().load_control_from_dict(
                    dict((cur.description[idx][0], value) for idx, value in enumerate(row)))

                if len(keywords)>0:
                    match_kw = False
                    package_keywords = ensure_list(pe.keywords.lower())
                    for kw in package_keywords:
                        if kw in keywords:
                            match_kw = True
                            break
                    if not match_kw:
                        continue

                if not search or pe.match_search(search):
                    if wapt().is_authorized_package_action('list',pe.package,grpuser,rules):
                        rows.append(pe)

            if request.args.get('latest','0') == '1':
                filtered = []
                last_package_name = None
                for package in sorted(rows,reverse=True):
                    if package.package != last_package_name:
                        filtered.append(package)
                    last_package_name = package.package
                rows = list(reversed(filtered))

            if not request.args.get('all_versions',''):
                rows = sorted(latest_only(rows))

        except sqlite3.Error as e:
            logger.critical(u"*********** Error %s:" % e.args[0])
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        for pe in rows:
            # some basic search scoring
            score = 0
            if search in pe.package:
                score += 3
            if search in pe.description:
                score += 2
            pe.score = score
        rows = sorted(rows,key=lambda r:(r.score,r.signature_date,r.filename),reverse=True)
        return Response(common.jsondump(rows), mimetype='application/json')
    else:
        for pe in rows:
            # hack to enable proper version comparison in templates
            pe.install_version = Version(pe.install_version)
            pe.version = Version(pe.version)

        try:
            search = search
        except NameError:
            search = False

        pagination = None
        return render_template(
            'list.html',
            packages=rows, #[_min:_max],
            format_isodate=format_isodate,
            Version=setuphelpers.Version,
            pagination=pagination,
        )

@app.route('/local_package_details.json')
@app.route('/local_package_details')
@allow_local
def local_package_details():
    if not (request.args.get('format','html')=='json' or request.path.endswith('.json')):
        if not request.authorization:
            return authenticate()

    grpuser = []
    rules = None
    if enterprise_common:
        rules = enterprise_common.self_service_rules(wapt())


    if request.authorization:
        auth = request.authorization
        try:
            if check_auth(auth.username,auth.password):
                grpuser.append('waptselfservice')
                logger.debug(u'User %s authenticated against local admins (waptselfservice)' % auth.username)
            else:
                try:
                    grpuser = get_user_self_service_groups(list(rules.keys()),auth.username,auth.password)
                    logger.debug(u'User %s authenticated against self-service groups %s' % (auth.username,grpuser))
                except:
                    return authenticate()
        except:
            return authenticate()
    else:
        return authenticate()

    with sqlite3.connect(app.waptconfig.dbpath) as con:
        try:
            con.row_factory=sqlite3.Row
            query = '''\
                select r.*,s.version as install_version,s.install_status,s.install_date,s.explicit_by
                from wapt_package r
                left join wapt_localstatus s on s.package=r.package
                where r.package like "'''+request.args.get('package','')+'''" and not r.section in ("host","unit","profile")
                order by r.package,r.version'''
            cur = con.cursor()
            cur.execute(query)
            rows = []

            for row in cur.fetchall():
                pe = PackageEntry().load_control_from_dict(
                    dict((cur.description[idx][0], value) for idx, value in enumerate(row)))
                if wapt().is_authorized_package_action('list',pe.package,grpuser,rules):
                    rows.append(pe)

            rows = sorted(latest_only(rows))

            return Response(common.jsondump(rows), mimetype='application/json')
        except sqlite3.Error as e:
            logger.critical(u"*********** Error %s:" % e.args[0])
            return Response(common.jsondump([]), mimetype='application/json')

@app.route('/package_icon')
@allow_local
def package_icon():
    """Return png icon for the required 'package' parameter
    get it from local cache
    """
    package = request.args.get('package')
    icon_local_cache = os.path.join(wapt_root_dir,'cache','icons')
    if not os.path.isfile(os.path.join(icon_local_cache,package)):
        package = 'unknown'
    return send_from_directory(icon_local_cache,package+'.png',mimetype='image/png',as_attachment=True,attachment_filename=u'{}.png'.format(package),cache_timeout=43200)

@app.route('/package_details')
@app.route('/package_details.json')
@allow_local
def package_details():
    #wapt=Wapt(config_filename=app.waptconfig.config_filename)
    package = request.args.get('package')
    try:
        w = wapt()
        data = w.waptdb.installed_matching(package)
        if not data:
            data = w.is_available(package)
            # take the newest...
            data = data and data[-1].as_dict()
    except Exception as e:
        data = {'errors':[ ensure_unicode(e) ]}

    if request.args.get('format','html')=='json':
        return Response(common.jsondump(dict(result=data,errors=[])), mimetype='application/json')
    else:
        return render_template('package_details.html',data=data)


@app.route('/runstatus')
@allow_local
def get_runstatus():
    data = []
    with sqlite3.connect(app.waptconfig.dbpath) as con:
        con.row_factory=sqlite3.Row
        try:
            query ="""select value,create_date from wapt_params where name='runstatus' limit 1"""
            cur = con.cursor()
            cur.execute(query)
            rows = cur.fetchall()
            data = [dict(ix) for ix in rows]
        except Exception as e:
            logger.critical(u"*********** error " + ensure_unicode(e))
    return Response(common.jsondump(data), mimetype='application/json')


@app.route('/checkupgrades')
@app.route('/checkupgrades.json')
@allow_local
def get_checkupgrades():
    with sqlite3.connect(app.waptconfig.dbpath) as con:
        con.row_factory=sqlite3.Row
        data = ""
        try:
            query = u"""select * from wapt_params where name="last_update_status" limit 1"""
            cur = con.cursor()
            cur.execute(query)
            row = cur.fetchone()
            if row:
                data = json.loads(row['value'])
                # update runing_tasks.
                if app.task_manager:
                    with app.task_manager.status_lock:
                        if app.task_manager.running_task:
                            data['running_tasks'] = [app.task_manager.running_task.as_dict()]
                        else:
                            data['running_tasks'] = []
                        data['pending_tasks'] = [task.as_dict() for task in sorted(app.task_manager.tasks_queue.queue)]

                # if enterprise, add waptwua status
                query = u"""select * from wapt_params where name="waptwua.status" limit 1"""
                cur = con.cursor()
                cur.execute(query)
                row = cur.fetchone()
                if row:
                    data['wua_status'] = row['value']
                    # check count of updates
                    try:
                        query = u"""select * from wapt_params where name="waptwua.updates_localstatus" limit 1"""
                        cur = con.cursor()
                        cur.execute(query)
                        row = cur.fetchone()
                        if row:
                            wua_localstatus = json.loads(row['value'])
                            wua_pending_count = len([u['update_id'] for u in wua_localstatus if u['status'] == 'PENDING'])
                            data['wua_pending_count']= wua_pending_count
                    except Exception as e:
                        logger.critical('Unable to read waptwua updates_localstatus from DB: %s' % e)
            else:
                data = None

        except Exception as e :
            logger.critical(u"*********** error %s"  % (ensure_unicode(e)))
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data,title=_(u'Update status'))


@app.route('/waptupgrade')
@app.route('/waptupgrade.json')
@allow_local
def waptclientupgrade():
    """Launch an external 'wapt-get waptupgrade' process to upgrade local copy of wapt client"""
    data = app.task_manager.add_task(WaptClientUpgrade()).as_dict()
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data,title='Upgrade')


@app.route('/waptservicerestart')
@app.route('/waptservicerestart.json')
@allow_local
def waptservicerestart():
    """Restart local waptservice using a spawned batch file"""
    data = app.task_manager.add_task(WaptServiceRestart()).as_dict()
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data,title='Upgrade')


@app.route('/reload_config')
@app.route('/reload_config.json')
@allow_local
def reload_config():
    """trigger reload of wapt-get.ini file for the service"""
    notify_user = int(request.args.get('notify_user','0')) == 1
    data = app.task_manager.add_task(WaptNetworkReconfig(notify_user=notify_user)).as_dict()
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data,title=_('Reload configuration'))



@app.route('/upgrade')
@app.route('/upgrade.json')
@allow_local
def upgrade():
    force = int(request.args.get('force','0')) != 0
    notify_user = int(request.args.get('notify_user','1')) != 0
    update_packages = int(request.args.get('update','1')) != 0

    only_priorities = None
    if 'only_priorities' in request.args:
        only_priorities = ensure_list(request.args.get('only_priorities',None),allow_none=True)
    only_if_not_process_running = int(request.args.get('only_if_not_process_running','0')) != 0

    if waptwua_api:
        install_wua_updates= int(request.args.get('install_wua_updates','0')) != 0
    else:
        install_wua_updates = False

    all_tasks = []
    if update_packages:
        all_tasks.append(app.task_manager.add_task(WaptUpdate(force=force,notify_user=notify_user)).as_dict())

    all_tasks.append(app.task_manager.add_task(WaptUpgrade(notify_user=notify_user,only_priorities=only_priorities,
            only_if_not_process_running=only_if_not_process_running,force=force)).as_dict())
    all_tasks.append(app.task_manager.add_task(WaptCleanup(notify_user=False)))

    # append install wua tasks only if last scan reported to something to install
    if waptwua_api and install_wua_updates and wapt().waptwua_enabled and wapt().read_param('waptwua.status','UNKNONW') != 'OK':
        all_tasks.append(app.task_manager.add_task(WaptWUAInstallTask(notify_user=False)))

    data = {'result':'OK','content':all_tasks}
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data,title='Upgrade')


@app.route('/download_upgrades')
@app.route('/download_upgrades.json')
@allow_local
def download_upgrades():
    force = int(request.args.get('force','0')) != 0
    notify_user = int(request.args.get('notify_user','0')) != 0
    all_tasks = []
    wapt().update()
    reqs = wapt().check_downloads()
    for req in reqs:
        all_tasks.append(app.task_manager.add_task(WaptDownloadPackage(req.asrequirement(),usecache=not force,notify_user=notify_user)).as_dict())
    data = {'result':'OK','content':all_tasks}
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data,title=_(u'Download upgrades'))


@app.route('/update')
@app.route('/update.json')
@allow_local
def update():
    task = WaptUpdate()
    task.force = int(request.args.get('force','0')) != 0
    task.notify_user = int(request.args.get('notify_user','0' if not waptconfig.notify_user else '1')) != 0
    task.notify_server_on_finish = int(request.args.get('notify_server','0')) != 0
    data = app.task_manager.add_task(task).as_dict()
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data,title=_(u'Installed software update'))


@app.route('/audit')
@app.route('/audit.json')
@allow_local
def audit():
    tasks = []
    notify_user = int(request.args.get('notify_user','0' if not waptconfig.notify_user else '1')) != 0
    notify_server_on_finish = int(request.args.get('notify_server','1')) != 0
    force = int(request.args.get('force','0')) != 0

    packagenames = []

    now = setuphelpers.currentdatetime()
    for package_status in wapt().installed():
        if force or not package_status.next_audit_on or (now >= package_status.next_audit_on):
            packagenames.append(package_status.package)

    if packagenames:
        task = WaptAuditPackage(packagenames,force=force)
        task.notify_user=notify_user
        task.notify_server_on_finish=notify_server_on_finish
        tasks.append(app.task_manager.add_task(task).as_dict())
        tasks.append(app.task_manager.add_task(WaptUpdateServerStatus(priority=100)).as_dict())

    data = {'result':'OK','content':tasks,'message':'%s tasks queued' % len(tasks)}
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data,title=_(u'Triggered packages audits'))


@app.route('/update_status')
@app.route('/update_status.json')
@allow_local
def update_status():
    task = WaptUpdateServerStatus()
    data = app.task_manager.add_task(task).as_dict()
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data,title=task)


@app.route('/longtask')
@app.route('/longtask.json')
@allow_local_auth
def longtask():
    notify_user = request.args.get('notify_user',None)
    if notify_user is not None:
        notify_user=int(notify_user)
    data = app.task_manager.add_task(
        WaptLongTask(
            duration=int(request.args.get('duration','60')),
            raise_error=int(request.args.get('raise_error',0)),
            notify_user=notify_user)).as_dict()
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data,title=_('LongTask'))


@app.route('/cleanup')
@app.route('/cleanup.json')
@app.route('/clean')
@allow_local
def cleanup():
    task = WaptCleanup()
    task.force = int(request.args.get('force','0')) == 1
    notify_user = int(request.args.get('notify_user','0')) == 1
    data = app.task_manager.add_task(task,notify_user=notify_user)
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data.as_dict(),title=_('Cleanup'))


@app.route('/install_log')
@app.route('/install_log.json')
@allow_local_auth
def install_log():
    logger.info(u"show install log")
    try:
        packagename = request.args.get('package')
        data = wapt().last_install_log(packagename)
    except Exception as e:
        data = {'result':'ERROR','message': u'{}'.format(ensure_unicode(e))}
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data,title=_('Trace of the installation of {}').format(packagename))


@app.route('/enable')
@allow_local_auth
def enable():
    logger.info(u"enable tasks scheduling")
    data = wapt().enable_tasks()
    return Response(common.jsondump(data), mimetype='application/json')


@app.route('/disable')
@allow_local_auth
def disable():
    logger.info(u"disable tasks scheduling")
    data = wapt().disable_tasks()
    return Response(common.jsondump(data), mimetype='application/json')


@app.route('/register')
@app.route('/register.json')
@allow_local_auth
def register():
    logger.info(u"register computer")
    notify_user = int(request.args.get('notify_user','0')) == 1
    data = app.task_manager.add_task(WaptRegisterComputer(),notify_user=notify_user).as_dict()

    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data,title=_('Saving host to the WAPT server'))


@app.route('/inventory')
@app.route('/inventory.json')
@allow_local_auth
def inventory():
    logger.info(u"Inventory")
    #wapt=Wapt(config_filename=app.waptconfig.config_filename)
    data = wapt().inventory()
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data,title=_('Inventory of the host'))

@app.route('/install', methods=['GET'])
@app.route('/install.json', methods=['GET'])
@app.route('/install.html', methods=['GET'])
@allow_local
def install():
    print('trying install')
    package_requests = request.args.get('package')
    if not isinstance(package_requests,list):
        package_requests = [package_requests]

    force = int(request.args.get('force','0')) == 1
    notify_user = int(request.args.get('notify_user','0')) == 1
    only_priorities = None
    if 'only_priorities' in request.args:
        only_priorities = ensure_list(request.args.get('only_priorities',None),allow_none=True)
    only_if_not_process_running = int(request.args.get('only_if_not_process_running','0')) != 0

    username = None
    grpuser = []
    rules = None
    if enterprise_common:
        rules = enterprise_common.self_service_rules(wapt())


    if request.authorization:
        auth = request.authorization
        try:
            if check_auth(auth.username,auth.password):
                grpuser.append('waptselfservice')
                username = auth.username
                logger.debug(u'User %s authenticated against local admins or waptselfservice)' % auth.username)
            else:
                try:
                    grpuser = get_user_self_service_groups(list(rules.keys()),auth.username,auth.password)
                    username = auth.username
                    logger.debug(u'User %s authenticated against self-service groups %s' % (auth.username,grpuser))
                except:
                    logger.debug(u'User %s not allowed' % (auth.username))
                    return authenticate()
        except:
            return authenticate()

    authorized_packages = []
    for apackage in package_requests:
        if wapt().is_authorized_package_action('install',apackage,grpuser,rules):
            authorized_packages.append(apackage)
        else:
            return authenticate()

    logging.info("user %s authenticated" % username)

    if authorized_packages:
        data = app.task_manager.add_task(WaptPackageInstall(authorized_packages,force=force,installed_by=username,
            only_priorities = only_priorities,only_if_not_process_running=only_if_not_process_running,notify_user=notify_user)).as_dict()
        app.task_manager.add_task(WaptAuditPackage(packagenames=authorized_packages,force=force,notify_user=notify_user,priority=100)).as_dict()
    else:
        data = []

    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('install.html',data=data)


@app.route('/package_download')
@app.route('/package_download.json')
@allow_local_auth
def package_download():
    package = request.args.get('package')
    logger.info(u"download package %s" % package)
    notify_user = int(request.args.get('notify_user','0')) == 1
    usecache = int(request.args.get('usecache','1')) == 1
    data = app.task_manager.add_task(WaptDownloadPackage(package,usecache=usecache,notify_user=notify_user)).as_dict()

    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data)


@app.route('/remove', methods=['GET'])
@app.route('/remove.json', methods=['GET'])
@app.route('/remove.html', methods=['GET'])
@allow_local
def remove():
    print('trying remove')
    package_requests = request.args.get('package')
    if not isinstance(package_requests,list):
        package_requests = [package_requests]

    force = int(request.args.get('force','0')) == 1
    notify_user = int(request.args.get('notify_user','0')) == 1
    only_priorities = None
    if 'only_priorities' in request.args:
        only_priorities = ensure_list(request.args.get('only_priorities',None),allow_none=True)
    only_if_not_process_running = int(request.args.get('only_if_not_process_running','0')) != 0

    username = None
    grpuser = []
    rules = None
    if enterprise_common:
        rules = enterprise_common.self_service_rules(wapt())


    if request.authorization:
        auth = request.authorization
        try:
            if check_auth(auth.username,auth.password):
                grpuser.append('waptselfservice')
                username = auth.username
                logger.debug(u'User %s authenticated against local admins (waptselfservice)' % auth.username)
            else:
                try:
                    grpuser = get_user_self_service_groups(list(rules.keys()),auth.username,auth.password)
                    username = auth.username
                    logger.debug(u'User %s authenticated against self-service groups %s' % (auth.username,grpuser))
                except:
                    logger.debug(u'User %s not allowed' % (auth.username))
                    return authenticate()
        except:
            logger.debug(u'User %s wrong authentication' % (auth.username))
            return authenticate

    authorized_packages = []
    for apackage in package_requests:
        if wapt().is_authorized_package_action('remove',apackage,grpuser,rules):
            authorized_packages.append(apackage)
        else:
            return authenticate()

    logging.info("user %s authenticated" % username)

    data = []
    if authorized_packages:
        for package in authorized_packages:
            data.append(app.task_manager.add_task(WaptPackageRemove(package,force=force,created_by=username),notify_user=notify_user).as_dict())

    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('remove.html',data=data)


@app.route('/forget', methods=['GET'])
@app.route('/forget.json', methods=['GET'])
@allow_local_auth
def forget():
    packages = request.args.get('package')
    if not isinstance(packages,list):
        packages = [packages]
    logger.info(u"Forget package(s) %s" % packages)
    notify_user = int(request.args.get('notify_user','0')) == 1
    data = app.task_manager.add_task(WaptPackageForget(packages),notify_user=notify_user).as_dict()
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('install.html',data=data)

@app.route('/', methods=['GET'])
@allow_local
def index():
    host_info = setuphelpers.host_info()
    data = dict(html=html,
        host_info=host_info,
        wapt=wapt(),
        wapt_info=wapt().wapt_status(),
        update_status=wapt().get_last_update_status(),)
    if request.args.get('format','html')=='json'  or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('index.html',**data)


@app.route('/tasks')
@app.route('/tasks.json')
@allow_local
def tasks():
    last_received_event_id = int(request.args.get('last_event_id','-1'))
    timeout = int(request.args.get('timeout','-1'))

    data = None
    start_time = time.time()

    while True:
        # wait for events manager initialisation
        if app.task_manager.events:
            actual_last_event_id = app.task_manager.events.last_event_id()
            if actual_last_event_id is not None and actual_last_event_id <= last_received_event_id:
                if (time.time() - start_time) * 1000 > timeout:
                    break
            elif actual_last_event_id is None or actual_last_event_id > last_received_event_id:
                data = app.task_manager.tasks_status()
                break
        if time.time() - start_time > timeout:
            break

        # avoid eating cpu
        time.sleep(0.1)

    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('tasks.html',data=data)


@app.route('/tasks_status')
@app.route('/tasks_status.json')
@allow_local
def tasks_status():
    last_received_event_id = int(request.args.get('last_event_id','-1'))
    timeout = int(request.args.get('timeout','-1'))

    result = {}
    start_time = time.time()
    data = None

    while True:
        if app.task_manager.events:
            actual_last_event_id = app.task_manager.events.last_event_id()
            result['last_event_id'] = actual_last_event_id
            if actual_last_event_id is not None and actual_last_event_id <= last_received_event_id:
                if (time.time() - start_time) * 1000 > timeout:
                    break
            elif actual_last_event_id is None or actual_last_event_id > last_received_event_id:
                data = app.task_manager.tasks_status()
                break

        if time.time() - start_time > timeout:
            break

        # avoid eating cpu
        time.sleep(0.1)

    if data:
        tasks = []
        tasks.extend(data['pending'])
        if data['running']:
            tasks.append(data['running'])
        tasks.extend(data['done'])
        tasks.extend(data['errors'])
        tasks.extend(data['cancelled'])
        result['tasks'] = tasks
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(result), mimetype='application/json')
    else:
        return render_template('tasks.html',data=result)


@app.route('/task')
@app.route('/task.json')
@app.route('/task.html')
@allow_local
def task():
    id = int(request.args['id'])
    tasks = app.task_manager.tasks_status()
    all_tasks = tasks['done']+tasks['pending']+tasks['errors']
    if tasks['running']:
        all_tasks.append(tasks['running'])
    all_tasks = [task for task in all_tasks if task and task['id'] == id]
    if all_tasks:
        task = all_tasks[0]
    else:
        task = {}
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(task), mimetype='application/json')
    else:
        return render_template('task.html',task=task)


@app.route('/cancel_all_tasks')
@app.route('/cancel_all_tasks.html')
@app.route('/cancel_all_tasks.json')
@allow_local
def cancel_all_tasks():
    data = app.task_manager.cancel_all_tasks()
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data)


@app.route('/cancel_running_task')
@app.route('/cancel_running_task.json')
@allow_local
def cancel_running_task():
    data = app.task_manager.cancel_running_task()
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data)

@app.route('/cancel_task')
@app.route('/cancel_task.json')
@allow_local
def cancel_task():
    id = int(request.args['id'])
    data = app.task_manager.cancel_task(id)
    if request.args.get('format','html')=='json' or request.path.endswith('.json'):
        return Response(common.jsondump(data), mimetype='application/json')
    else:
        return render_template('default.html',data=data)


@app.route('/wapt/<string:input_package_name>')
@allow_local
def get_wapt_package(input_package_name):
    package_name = secure_filename(input_package_name)
    cache_dir = wapt().package_cache_dir
    local_fn = os.path.join(cache_dir,package_name)
    force = int(request.args.get('force','0')) == 1

    if package_name == 'Packages' and (not os.path.isfile(local_fn) or force):
        local_repo = WaptLocalRepo(cache_dir)
        local_repo.update_packages_index(force_all=force)

    if os.path.isfile(local_fn):
        r = send_from_directory(cache_dir, package_name)
        if 'content-length' not in r.headers:
            r.headers.add_header(
                'content-length', int(os.path.getsize(local_fn)))
        return r
    else:
        return Response(status=404)

@app.route('/events')
@app.route('/events.json')
@allow_local
def events():
    """Get the last waptservice events.
    Blocking call for timeout seconds.

    Args:
        last_read (int): id of last read event.
        timeout (float): time to wait until new events come in
    """
    last_read = int(request.args.get('last_read',session.get('last_read_event_id','0')))
    timeout = int(request.args.get('timeout','10000'))
    max_count = int(request.args.get('max_count','0')) or None
    if app.task_manager.events:
        data = app.task_manager.events.get_missed(last_read=last_read,max_count=max_count)
        if not data and timeout > 0.0:
            start_time = time.time()
            while not data and (time.time() - start_time) * 1000 <= timeout:
                time.sleep(1.0)
                data = app.task_manager.events.get_missed(last_read=last_read,max_count=max_count)
            if app.task_manager.events.events:
                session['last_read_event_id'] = app.task_manager.events.events[-1].id
    else:
        data = None
    return Response(jsondump(data), mimetype='application/json')

class WaptTaskManager(threading.Thread):
    def __init__(self,config_filename = 'c:/wapt/wapt-get.ini'):
        threading.Thread.__init__(self)
        self.name = 'WaptTaskManager'
        self.status_lock = threading.RLock()
        self.wapt = None
        self.tasks = []

        self.tasks_queue = queue.PriorityQueue()
        self.tasks_counter = 0

        self.tasks_done = []
        self.tasks_error = []
        self.tasks_cancelled = []
        self.events = None

        self.running_task = None
        self.config_filename = config_filename

        self.last_update_server_date = None

        self.last_upgrade = None
        self.last_update = None
        self.last_audit = None
        self.last_sync = None

    def setup_event_queue(self):
        self.events = WaptEvents()
        return self.events

    def update_runstatus(self,status):
        # update database with new runstatus
        self.wapt.runstatus = status
        if self.events:
            # dispatch event to listening parties
            self.events.post_event("STATUS",self.wapt.get_last_update_status())

    def update_server_status(self):
        if self.wapt.waptserver_available():
            try:
                result = self.wapt.update_server_status()
                if result and result['success'] and result['result']['uuid']:
                    self.last_update_server_date = datetime.datetime.now()
                elif result and not result['success']:
                    logger.critical('Unable to update server status: %s' % result['msg'])
                else:
                    raise Exception('No answer')
            except Exception as e:
                logger.debug('Unable to update server status: %s' % repr(e))

    def broadcast_tasks_status(self,event_type,task):
        """event_type : TASK_ADD TASK_START TASK_STATUS TASK_FINISH TASK_CANCEL TASK_ERROR
        """
        # ignore broadcast for this..
        if isinstance(task,WaptUpdateServerStatus):
            return
        if self.events and task:
            self.events.post_event(event_type,task.as_dict())

    def add_task(self,task,notify_user=None):
        """Adds a new WaptTask for processing"""
        with self.status_lock:
            if not self.wapt:
                start_wait = time.time()
                while not self.wapt:
                    time.sleep(1)
                    if time.time() - start_wait>15:
                        raise Exception('WapttaskManager.add_task : No wapt instance available in Task manager')

            same = [pending for pending in self.tasks_queue.queue if pending.same_action(task)]
            if self.running_task and self.running_task.same_action(task):
                same.append(self.running_task)

            # keep track of last update/upgrade add date to avoid relaunching
            if isinstance(task,WaptUpdate):
                self.last_update = datetime.datetime.now()
            if isinstance(task,WaptUpgrade):
                self.last_upgrade = datetime.datetime.now()
            if waptrepositories_api and isinstance(task,WaptSyncRepo):
                self.last_sync = datetime.datetime.now()

            # not already in pending  actions...
            if not same:
                task.wapt = self.wapt
                task.task_manager = self

                self.tasks_counter += 1
                task.id = self.tasks_counter
                # default order is task id
                task.order = self.tasks_counter
                if notify_user is not None:
                    task.notify_user = notify_user
                self.tasks_queue.put(task)
                self.tasks.append(task)
                self.broadcast_tasks_status('TASK_ADD',task)
                return task
            else:
                return same[0]

    def check_configuration(self):
        """Check wapt configuration, reload ini file if changed"""
        try:
            logger.debug(u"Checking if config file has changed")
            if waptconfig.reload_if_updated():
                logger.info(u"Wapt config file has changed, reloading")
                self.wapt.reload_config_if_updated()

        except:
            pass

    def run_scheduled_audits(self):
        """Add packages audit tasks to the queue"""
        now = setuphelpers.datetime2isodate()
        self.last_audit = datetime.datetime.now()
        packages = []
        for installed_package in self.wapt.installed():
            if not installed_package.next_audit_on or now >= installed_package.next_audit_on:
                packages.append(installed_package.package)
        if packages:
            task = WaptAuditPackage(packages,created_by='SCHEDULER')
            self.add_task(task)
            self.add_task(WaptUpdateServerStatus(priority=100,created_by='SCHEDULER'))


    @property
    def last_waptwua_download(self):
        return self.wapt.read_param('last_waptwua_download',ptype='datetime')

    @last_waptwua_download.setter
    def last_waptwua_download(self,value):
        if value is None:
            self.wapt.delete_param('last_waptwua_download')
        else:
            self.wapt.write_param('last_waptwua_download',value)

    @property
    def last_waptwua_install(self):
        return self.wapt.read_param('last_waptwua_install',ptype='datetime')

    @last_waptwua_install.setter
    def last_waptwua_install(self,value):
        if value is None:
            self.wapt.delete_param('last_waptwua_install')
        else:
            self.wapt.write_param('last_waptwua_install',value)

    def check_scheduled_tasks(self):
        """Add update/upgrade tasks if elapsed time since last update/upgrade is over"""
        logger.debug(u'Check scheduled tasks')

        if datetime.datetime.now() - self.start_time >= datetime.timedelta(days=1):
            self.start_time = datetime.datetime.now()
            self.add_task(WaptServiceRestart(created_by='DAILY RESTART'))

        if waptconfig.waptupdate_task_period is not None:
            if self.last_update is None or \
                    (datetime.datetime.now() - self.last_update) > get_time_delta(waptconfig.waptupdate_task_period,'m') or \
                    (setuphelpers.datetime2isodate() > self.wapt.read_param('next_update_on','9999-12-31')):
                try:
                    self.wapt.update()
                    reqs = self.wapt.check_downloads()
                    for req in reqs:
                        self.add_task(WaptDownloadPackage(req.asrequirement(),notify_user=True,created_by='SCHEDULER'))
                    self.add_task(WaptUpdate(notify_user=False,notify_server_on_finish=True,created_by='SCHEDULER'))
                except Exception as e:
                    logger.debug(u'Error for update in check_scheduled_tasks: %s'%e)

        if waptconfig.waptupgrade_task_period is not None and setuphelpers.running_on_ac():
            if self.last_upgrade is None or (datetime.datetime.now() - self.last_upgrade) > get_time_delta(waptconfig.waptupgrade_task_period,'m'):
                try:
                    self.add_task(WaptUpgrade(notifyuser=False,created_by='SCHEDULER',only_if_no_process_running=True))
                except Exception as e:
                    logger.debug(u'Error for upgrade in check_scheduled_tasks: %s'%e)
                self.add_task(WaptCleanup(notifyuser=False,created_by='SCHEDULER'))

        if waptconfig.waptaudit_task_period:
            if self.last_audit is None or (datetime.datetime.now() - self.last_audit > get_time_delta(waptconfig.waptaudit_task_period,'m')):
                try:
                    self.run_scheduled_audits()
                except Exception as e:
                    logger.debug(u'Error checking audit: %s' % e)

        if waptrepositories_api and waptconfig.enable_remote_repo:
            if waptconfig.local_repo_sync_task_period:
                if self.last_sync is None or (datetime.datetime.now() - self.last_sync > get_time_delta(waptconfig.local_repo_sync_task_period,'m')):
                    try:
                        logger.debug(u'Add_task for sync with local_repo_sync_task_period')
                        self.add_task(WaptSyncRepo(notifyuser=False,created_by='SCHEDULER'))
                    except Exception as e:
                        logger.debug(u'Error syncing local repo with server repo : %s' % e)
            elif waptconfig.local_repo_time_for_sync_start:
                time_now = datetime.datetime.now()
                if common.is_between_two_times(waptconfig.local_repo_time_for_sync_start,waptconfig.local_repo_time_for_sync_end) and (self.last_sync is None or (datetime.datetime.now() - self.last_sync > get_time_delta('10m','m'))):
                    try:
                        logger.debug(u'Add_task for sync with local_repo_time_for_sync')
                        self.add_task(WaptSyncRepo(notifyuser=False,created_by='SCHEDULER'))
                    except Exception as e:
                        logger.debug(u'Error syncing local repo with server repo : %s' % e)


        if WaptWUAParams is not None and self.wapt.waptwua_enabled:
            params = WaptWUAParams()
            if self.wapt.config and self.wapt.config.has_section('waptwua'):
                params.load_from_ini(config=self.wapt.config,section='waptwua')
            if params.install_scheduling:
                if self.last_waptwua_install is None or (datetime.datetime.now() - self.last_waptwua_install > get_time_delta(params.install_scheduling,'d')):
                    if self.wapt.read_param('waptwua.status','') == 'PENDING_UPDATES':
                        self.add_task(WaptWUAInstallTask(notify_user=False,notify_server_on_finish=True,created_by='SCHEDULER'))
                    self.last_waptwua_install = datetime.datetime.now()
                    self.last_waptwua_download = datetime.datetime.now()

            if params.download_scheduling:
                if self.last_waptwua_download is None or (datetime.datetime.now() - self.last_waptwua_download > get_time_delta(params.download_scheduling,'d')):
                    self.add_task(WaptWUADowloadTask(notify_user=False,notify_server_on_finish=True,created_by='SCHEDULER'))
                    self.last_waptwua_download = datetime.datetime.now()


    def run(self):
        """Queue management, event processing"""
        if sys.platform == 'win32':
            try:
                pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            except pythoncom.com_error:
                # already initialized.
                pass

        self.start_time = datetime.datetime.now()
        self.wapt = Wapt(config_filename=self.config_filename)
        self.setup_event_queue()

        logger.info(u'Wapt tasks management initialized with {} configuration, thread ID {}'.format(self.config_filename,threading.current_thread().ident))

        if self.wapt.config.has_option('global','reconfig_on_network_change') and self.wapt.config.getboolean('global','reconfig_on_network_change'):
            self.start_network_monitoring()
            self.start_ipaddr_monitoring()

        logger.debug(u"Wapt tasks queue started")
        while True:
            try:
                # check wapt configuration, reload ini file if changed
                # reload wapt config
                self.check_configuration()
                # force update if host capabilities have changed
                new_capa = self.wapt.host_capabilities_fingerprint()
                old_capa = self.wapt.read_param('host_capabilities_fingerprint')
                if old_capa != new_capa:
                    logger.info('Host capabilities have changed since last update, forcing update')
                    task = WaptUpdate()
                    task.created_by = 'TASK MANAGER'
                    task.force = True
                    task.notify_server_on_finish = True
                    self.add_task(task).as_dict()

                # check tasks queue
                self.running_task = self.tasks_queue.get(timeout=waptconfig.waptservice_poll_timeout)
                try:
                    # don't send update_run status for updatestatus itself...
                    self.broadcast_tasks_status('TASK_START',self.running_task)
                    if self.running_task.notify_server_on_start:
                        self.update_runstatus(_(u'Running: {description}').format(description=self.running_task) )
                        self.update_server_status()
                    try:
                        def update_running_status(append_output=None,set_status=None):
                            if append_output:
                                if self.running_task:
                                    self.running_task.logs.append(append_output)
                                if self.events:
                                    self.events.post_event('PRINT',ensure_unicode(append_output))
                            if self.events and self.running_task:
                                self.broadcast_tasks_status('TASK_STATUS',self.running_task)

                        with LogOutput(console=sys.stderr,update_status_hook=update_running_status):
                            self.running_task.run()

                        if self.running_task:
                            self.tasks_done.append(self.running_task)
                            self.broadcast_tasks_status('TASK_FINISH',self.running_task)
                            if self.running_task.notify_server_on_finish:
                                self.update_runstatus(_(u'Done: {description}\n{summary}').format(description=self.running_task,summary=self.running_task.summary) )
                                self.update_server_status()

                    except common.EWaptCancelled as e:
                        if self.running_task:
                            self.running_task.logs.append(u"{}".format(ensure_unicode(e)))
                            self.running_task.summary = _(u"Canceled")
                            self.tasks_cancelled.append(self.running_task)
                            self.broadcast_tasks_status('TASK_CANCEL',self.running_task)
                    except Exception as e:
                        if self.running_task:
                            self.running_task.logs.append(u"{}".format(ensure_unicode(e)))
                            self.running_task.logs.append(ensure_unicode(traceback.format_exc()))
                            self.running_task.summary = u"{}".format(ensure_unicode(e))
                            self.tasks_error.append(self.running_task)
                            self.broadcast_tasks_status('TASK_ERROR',self.running_task)
                        logger.critical(ensure_unicode(e))
                        try:
                            logger.debug(ensure_unicode(traceback.format_exc()))
                        except:
                            print("Traceback error")
                finally:
                    self.tasks_queue.task_done()
                    try:
                        self.update_runstatus('')
               	    except Exception as e:
                        logger.warning(u'Error reset runstatus : %s' % ensure_unicode(traceback.format_exc()))

                    self.running_task = None
                    # trim history lists
                    if len(self.tasks_cancelled)>waptconfig.MAX_HISTORY:
                        del self.tasks_cancelled[:len(self.tasks_cancelled)-waptconfig.MAX_HISTORY]
                    if len(self.tasks_done)>waptconfig.MAX_HISTORY:
                        del self.tasks_done[:len(self.tasks_done)-waptconfig.MAX_HISTORY]
                    if len(self.tasks_error)>waptconfig.MAX_HISTORY:
                        del self.tasks_error[:len(self.tasks_error)-waptconfig.MAX_HISTORY]

            except queue.Empty:
                try:
                    self.update_runstatus('')
                except Exception as e:
                    logger.warning(u'Error reset runstatus : %s' % ensure_unicode(traceback.format_exc()))

                try:
                    self.check_scheduled_tasks()
                except Exception as e:
                    logger.warning(u'Error checking scheduled tasks : %s' % ensure_unicode(traceback.format_exc()))
                logger.debug(u"{} i'm still alive... but nothing to do".format(datetime.datetime.now()))

            except Exception as e:
                logger.critical(u'Unhandled error in task manager loop: %s' % ensure_unicode(e))


    def current_task_counter(self):
        with self.status_lock:
            return self.tasks_counter

    def tasks_status(self):
        """Returns list of pending, error, done tasks, and current running one"""
        with self.status_lock:
            return dict(
                running=self.running_task and self.running_task.as_dict(),
                pending=[task.as_dict() for task in sorted(self.tasks_queue.queue)],
                done = [task.as_dict() for task in self.tasks_done],
                cancelled = [ task.as_dict() for task in self.tasks_cancelled],
                errors = [ task.as_dict() for task in self.tasks_error],
                last_task_id = self.tasks_counter,
                last_event_id = self.events.last_event_id() if self.events else None,
                )

    def cancel_running_task(self):
        """Cancel running task. Returns cancelled task"""
        with self.status_lock:
            if self.running_task:
                try:
                    cancelled = self.running_task
                    self.tasks_error.append(self.running_task)
                    try:
                        self.running_task.kill()
                    except:
                        pass
                finally:
                    self.running_task = None
                if cancelled:
                    self.tasks_cancelled.append(cancelled)
                    self.broadcast_tasks_status('TASK_CANCEL',cancelled)
                return cancelled
            else:
                return None

    def cancel_task(self,id):
        """Cancel running or pending task with supplied id.
            return cancelled task"""
        with self.status_lock:
            cancelled = None
            if self.running_task and self.running_task.id == id:
                cancelled = self.running_task
                try:
                    self.running_task.kill()
                except:
                    pass
                finally:
                    self.running_task = None
            else:
                for task in self.tasks_queue.queue:
                    if task.id == id:
                        cancelled = task
                        self.tasks_queue.queue.remove(task)
                        break
                if cancelled:
                    try:
                        cancelled.kill()
                    except:
                        pass
            if cancelled:
                self.broadcast_tasks_status('TASK_CANCEL',cancelled)
            return cancelled

    def cancel_all_tasks(self):
        """Cancel running and pending tasks. Returns list of cancelled tasks"""
        with self.status_lock:
            cancelled = []
            while not self.tasks_queue.empty():
                 cancelled.append(self.tasks_queue.get())
            if self.running_task:
                try:
                    cancelled.append(self.running_task)
                    self.tasks_error.append(self.running_task)
                    try:
                        self.running_task.kill()
                    except:
                        pass
                finally:
                    self.running_task = None
            for task in cancelled:
                self.tasks_cancelled.append(task)
                self.broadcast_tasks_status('TASK_CANCEL',task)
            return cancelled

    def start_ipaddr_monitoring(self):
        nac = ctypes.windll.iphlpapi.NotifyAddrChange
        def addr_change(taskman):
            while True:
                nac(0, 0)
                taskman.add_task(WaptNetworkReconfig())

        nm = threading.Thread(target=addr_change,args=(self,),name='ip_monitoring')
        nm.daemon = True
        nm.start()
        logger.debug(u"Wapt network address monitoring started")

    def start_network_monitoring(self):
        nrc = ctypes.windll.iphlpapi.NotifyRouteChange
        def connected_change(taskman):
            while True:
                nrc(0, 0)
                taskman.add_task(WaptNetworkReconfig())

        nm = threading.Thread(target=connected_change,args=(self,),name='network_monitoring')
        nm.daemon = True
        nm.start()
        logger.debug(u"Wapt connection monitor started")

    def __unicode__(self):
        return "\n".join(self.tasks_status())

def install_service():
    """Setup waptservice as a windows Service managed by nssm
    >>> install_service()
    """
    from setuphelpers import registry_set,REG_DWORD,REG_EXPAND_SZ,REG_MULTI_SZ,REG_SZ
    datatypes = {
        'dword':REG_DWORD,
        'sz':REG_SZ,
        'expand_sz':REG_EXPAND_SZ,
        'multi_sz':REG_MULTI_SZ,
    }

    if setuphelpers.service_installed('waptservice'):
        if not setuphelpers.service_is_stopped('waptservice'):
            logger.info(u'Stop running waptservice')
            setuphelpers.service_stop('waptservice')
            while not setuphelpers.service_is_stopped('waptservice'):
                logger.debug(u'Waiting for waptservice to terminate')
                time.sleep(2)
        logger.info(u'Unregister existing waptservice')
        setuphelpers.service_delete('waptservice')

    if setuphelpers.iswin64():
        nssm = os.path.join(wapt_root_dir,'waptservice','win64','nssm.exe')
    else:
        nssm = os.path.join(wapt_root_dir,'waptservice','win32','nssm.exe')

    logger.info(u'Register new waptservice with nssm')
    setuphelpers.run('"{nssm}" install WAPTService "{waptpython}" -E ""{waptservicepy}""'.format(
        waptpython = os.path.abspath(os.path.join(wapt_root_dir,'waptpython.exe')),
        nssm = nssm,
        waptservicepy = os.path.abspath(__file__),
     ))

    #logger.info('Delayed startup')
    #setuphelpers.run('"{nssm}" set WAPTService Start SERVICE_DELAYED_START'.format(
    #    nssm = nssm))

    # fix some parameters (quotes for path with spaces...
    params = {
        "Description": "sz:Local helper managing WAPT install/remove/update/upgrade",
        "DisplayName" : "sz:WAPTService",
        "AppStdout" : r"expand_sz:{}".format(os.path.join(waptconfig.log_directory,'waptservice.log')),
        "Parameters\\AppStderr" : r"expand_sz:{}".format(os.path.join(waptconfig.log_directory,'waptservice.log')),
        "Parameters\\AppStdout" : r"expand_sz:{}".format(os.path.join(waptconfig.log_directory,'waptservice.log')),
        "Parameters\\AppParameters" : r'expand_sz:"{}"'.format(os.path.abspath(__file__)),
        "Parameters\\AppRotateFiles": 1,
        "Parameters\\AppRotateBytes": 10*1024*1024,
        "Parameters\\AppNoConsole":1,
        }

    root = setuphelpers.HKEY_LOCAL_MACHINE
    base = r'SYSTEM\CurrentControlSet\services\WAPTService'
    for key in params:
        if isinstance(params[key],int):
            (valuetype,value) = ('dword',params[key])
        elif ':' in params[key]:
            (valuetype,value) = params[key].split(':',1)
            if valuetype == 'dword':
                value = int(value)
        else:
            (valuetype,value) = ('sz',params[key])
        fullpath = base+'\\'+key
        (path,keyname) = fullpath.rsplit('\\',1)
        if keyname == '@' or keyname =='':
            keyname = None
        registry_set(root,path,keyname,value,type = datatypes[valuetype])

    logger.info(u'Allow authenticated users to start/stop waptservice')
    if waptconfig.allow_user_service_restart:
        setuphelpers.run('sc sdset waptservice D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)(A;;CCLCSWRPWPDTLOCRRC;;;S-1-5-11)S:(AU;FA;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;WD)')
    else:
        setuphelpers.run('sc sdset waptservice D:(A;;CCLCSWRPLORC;;;AU)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;SY)S:(AU;FA;CCDCLCSWRPWPDTLOSDRCWDWO;;;WD)')



if __name__ == "__main__":
    usage="""\
    %prog -c configfile [action]

    WAPT Service.

    action is either :
      <nothing> : run service in foreground
      install   : install as a Windows service managed by nssm

    """

    parser=OptionParser(usage=usage,version='service ' + __version__+' common.py '+common.__version__+' setuphelpers.py '+setuphelpers.__version__)
    parser.add_option("-c","--config", dest="config", default=os.path.join(wapt_root_dir,'wapt-get.ini') , help="Config file full path (default: %default)")
    parser.add_option("-l","--loglevel", dest="loglevel", default=None, type='choice',  choices=['debug','warning','info','error','critical'], metavar='LOGLEVEL',help="Loglevel (default: warning)")
    parser.add_option("-d","--devel", dest="devel", default=False,action='store_true', help="Enable debug mode (for development only)")

    (options,args)=parser.parse_args()

    if args  and args[0] == 'doctest':
        import doctest
        sys.exit(doctest.testmod())

    if args and args[0] == 'install':
        install_service()
        sys.exit(0)

    waptconfig.config_filename = options.config
    waptconfig.load()

    # force loglevel
    if options.loglevel:
        setloglevel(logger,options.loglevel)
        setloglevel(app.logger,options.loglevel)

    elif waptconfig.loglevel is not None:
        setloglevel(logger,waptconfig.loglevel)
        setloglevel(app.logger,waptconfig.loglevel)

    if waptconfig.log_to_windows_events:
        try:
            from logging.handlers import NTEventLogHandler
            hdlr = NTEventLogHandler('waptservice')
            logger.addHandler(hdlr)
        except Exception as e:
            logger.warning('Unable to initialize windows log Event handler: %s' % e)

    # setup basic settings
    if sys.platform == 'win32':
        apply_host_settings(waptconfig)

    # waptwua
    apply_waptwua_settings(waptconfig)

    # starts one WaptTasksManager
    logger.info('Starting task queue')
    task_manager = WaptTaskManager(config_filename = waptconfig.config_filename)
    task_manager.daemon = True
    task_manager.start()
    app.task_manager = task_manager
    if sys.platform == 'win32':
        if waptwua_api is not None:
            waptwua_api.task_manager = task_manager
    if waptrepositories_api is not None:
        waptrepositories_api.task_manager = task_manager

    logger.info('Task queue running')

    if waptconfig.waptserver:
        sio = WaptSocketIOClient(waptconfig.config_filename,task_manager=task_manager)
        sio_logger = logging.getLogger('socketIO-client-2')
        sio_logger.addHandler(logging.StreamHandler())

        sio.start()
        if options.loglevel:
            setloglevel(sio_logger,options.loglevel)
        else:
            setloglevel(sio_logger,waptconfig.loglevel)
        if waptrepositories_api is not None:
            waptrepositories_api.sio = sio

    if options.devel:
        #socketio_server.run(app,host='127.0.0.1', port=8088)

        logger.info('Starting local dev waptservice...')
        app.run(host='127.0.0.1',port=8088,debug=False)
    else:
        #wsgi.server(eventlet.listen(('', 8088)), app)

        port_config = []
        if waptconfig.waptservice_port:
            server = serve(app ,host='127.0.0.1' , port=waptconfig.waptservice_port)
            waitress_logger = logging.getLogger('waitress')
            if options.loglevel:
                setloglevel(waitress_logger ,options.loglevel)
            else:
                setloglevel(waitress_logger ,waptconfig.loglevel)
