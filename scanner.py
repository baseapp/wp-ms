import os
import re
import requests
import hashlib
import json
import uuid
import sys
import fnmatch
import base64
import argparse
import string
import subprocess
import datetime
import time
import traceback
import codecs
import signal
import hashlib

IS_YARA = True
try:
    import yara
except ImportError:
    IS_YARA = False

version_pattern = re.compile("^\$wp_version.*?(?P<version>[\d.]+)", re.M)
locale_pattern = re.compile("^define\('WPLANG'.*'(?P<locale>\w+)'", re.M)
php_pattern = re.compile("\$.*?\[.*?\]", re.M)


SYSTEM_UID = ''.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff) for i in range(0, 8*6, 8)][::-1])

PLUGIN_UPDATE_URL = "http://api.wordpress.org/plugins/update-check/1.1/"
THEME_UPDATE_URL = "http://api.wordpress.org/themes/update-check/1.1/"
SUBMIT_HASH_URL = 'http://wpmaldet.wpoven.com/api/submit-hash'
VALID_HASH_URL = 'http://wpmaldet.wpoven.com/api/hash'

OUTPUT_FILE = 'scanner.log'
MATCHING_SIGNATURES = []
YARA_RULES = []
HASHTABLE = {}
SORT_HASHTABLE = []
PATTERNS = []
SIGNATURES_PATH = ''
CHECK_INSECURE = False
VERBOSE = False
show_full_path = True
days = 0 # Consider latest files only, If it's 0 then take all the files

SKIP_DIRS = []

class timeout:
    def __init__(self, seconds=1, error_message='Timeout'):
        self.seconds = seconds
        self.error_message = error_message
    def handle_timeout(self, signum, frame):
        raise TimeoutError(self.error_message)
    def __enter__(self):
        signal.signal(signal.SIGALRM, self.handle_timeout)
        signal.alarm(self.seconds)
    def __exit__(self, type, value, traceback):
        signal.alarm(0)

def file_path(_p, _r=""):
    if show_full_path:
        return os.path.join(_r, _p)
    return _p


def get_file_data(file_path, default_headers):
    """
    Extract plugin/theme information from a file.

    :param file_path: Path of file
    :param default_headers: Headers to extract
    :return:
    """
    file_data = None
    with open(file_path, 'r') as f:
        file_data = f.read(8192)
    if file_data:
        file_data = file_data.replace('\r', '\n')
        for field, regex in default_headers.items():
            m = re.search(r"^[ \t\/*#@]*"+re.escape(regex) + ":(.*)$", file_data, re.M)
            if m:
                default_headers[field] = re.sub(r"\s*(?:\*\/|\?>).*", "", m.group(1)).strip()
            else:
                default_headers[field] = None
    if default_headers['Name']:
        return default_headers
    return None


def get_plugin_data(plugin_file):
    """Get plugin details from a file"""
    default_headers = {
        'Name': 'Plugin Name',
        'PluginURI': 'Plugin URI',
        'Version': 'Version',
        'Description': 'Description',
        'Author': 'Author',
        'AuthorURI': 'Author URI',
        'TextDomain': 'Text Domain',
        'DomainPath': 'Domain Path',
        'Network': 'Network',
        '_sitewide': 'Site Wide Only',
    }
    return get_file_data(plugin_file, default_headers)


def get_theme_data(theme_file):
    default_headers = {
        'Name': 'Theme Name',
        'ThemeURI': 'Theme URI',
        'Description': 'Description',
        'Author': 'Author',
        'AuthorURI': 'Author URI',
        'Version': 'Version',
        'Template': 'Template',
        'Status': 'Status',
        'Tags': 'Tags',
        'TextDomain': 'Text Domain',
        'DomainPath': 'Domain Path',
    }
    return get_file_data(theme_file, default_headers)


def get_plugins(wp_root):
    """
    Get all the plugins in a WordPress install.

    :param wp_root: Root path of WordPress Install
    :return:
    """
    plugins_dir = "{}/wp-content/plugins".format(wp_root)
    all_plugins = {}
    if not os.path.exists(plugins_dir):
        pmsg("Plugins path doesn't exists", 'error')
        return all_plugins
    for plugin_file in os.listdir(plugins_dir):
        plugin_data = None
        if os.path.isdir(os.path.join(plugins_dir, plugin_file)):
            plugin_sub_dir = os.path.join(plugins_dir, plugin_file)
            for _file in os.listdir(plugin_sub_dir):
                if os.path.isfile(os.path.join(plugin_sub_dir, _file)) and _file.endswith('.php'):
                    plugin_data = get_plugin_data(os.path.join(plugin_sub_dir, _file))
                    if plugin_data:
                        all_plugins[os.path.relpath(os.path.join(plugin_sub_dir, _file), plugins_dir)] = plugin_data
                        break
                        # print "Directory", plugin_file
        elif plugin_file.endswith('.php'):
            # print "File ", plugin_file
            plugin_data = get_plugin_data(os.path.join(plugins_dir, plugin_file))
            if plugin_data:
                all_plugins[plugin_file] = plugin_data
    return all_plugins


def get_themes(wp_root):
    """
    Get all the themes in a WordPress install.

    :param wp_root: Root path of WordPress Install
    :return:
    """
    themes_dir = "{}/wp-content/themes".format(wp_root)
    all_themes = {}
    if not os.path.exists(themes_dir):
        pmsg("Themes path doesn't exists", 'error')
        return all_themes
    for theme_dir in os.listdir(themes_dir):
        if os.path.isfile(os.path.join(themes_dir, theme_dir)) or theme_dir == 'CVS':
            continue
        if os.path.isfile(os.path.join(themes_dir, theme_dir, 'style.css')):
            theme_root = themes_dir
            theme_file = os.path.join(themes_dir, theme_dir, 'style.css')
            theme_data = get_theme_data(theme_file)
            if theme_data:
                all_themes[os.path.relpath(os.path.dirname(theme_file), theme_root)] = theme_data
        else:
            for theme_sub_dir in os.listdir(os.path.join(themes_dir, theme_dir)):
                if os.path.isfile(os.path.join(themes_dir, theme_dir, theme_sub_dir)) or theme_sub_dir == 'CVS':
                    continue
                if os.path.isfile(os.path.join(themes_dir, theme_dir, theme_sub_dir, 'style.css')):
                    theme_root = themes_dir + '/' + theme_sub_dir
                    theme_file = os.path.join(themes_dir, theme_dir, theme_sub_dir, 'style.css')
                    theme_data = get_theme_data(theme_file)
                    if theme_data:
                        all_themes[os.path.relpath(os.path.dirname(theme_file), theme_root)] = theme_data
    return all_themes


class Bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    DEBUG = '\033[90m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def pmsg(msg, code='info', write_output=False):
    color_code = Bcolors.OKGREEN
    if code == 'warning':
        color_code = Bcolors.WARNING
    if code == 'error':
        color_code = Bcolors.FAIL
    if code == 'debug':
        color_code = Bcolors.DEBUG
        if not VERBOSE:
            return
    print("\r" + Bcolors.OKBLUE + Bcolors.UNDERLINE + ">>" + Bcolors.ENDC + " " + color_code + msg + Bcolors.ENDC)
    if write_output:
        date_string = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(OUTPUT_FILE, "a") as my_file:
            my_file.write("["+date_string+"] "+msg+"\n")


def progress_bar(current, total, msg):
    # print current, total
    # _i = (current / total) * 100
    # print _i
    # if _i > 100:
    #     _i = 100
    # if _i < 0:
    #     _i = 0

    sys.stdout.write("\r"+Bcolors.OKBLUE + Bcolors.UNDERLINE + ">>" + Bcolors.ENDC + " " + Bcolors.OKGREEN + msg + " (%d/%d)" % (current, total))
    sys.stdout.flush()

    if current == total:
        sys.stdout.write("\n")
        sys.stdout.flush()


def checksum(fname):
    """
    Get MD5 hash for a file.

    :param fname: File path
    :return:
    """
    _hash = hashlib.md5()
    with open(fname, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            _hash.update(chunk)
    return _hash.hexdigest()


def checksum_sha1(fname):
    """
    Get SHA1 hash for a file.

    :param fname: File path
    :return:
    """
    _hash = hashlib.sha1()
    with open(fname, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            _hash.update(chunk)
    return _hash.hexdigest()


def get_application_path(_file=None):
    import re, os, platform
    if not hasattr(get_application_path, "dir"):
        if hasattr(sys, "frozen"):
            _dir = os.path.dirname(sys.executable)
        elif "__file__" in globals():
            _dir = os.path.dirname(os.path.realpath(__file__))
        else:
            _dir = os.getcwd()
        get_application_path.dir = _dir
    if _file is None:
        _file = ""
    if not _file.startswith("/") and not _file.startswith("\\") and (
            not re.search(r"^[\w-]+:", _file)):
        path = get_application_path.dir + os.sep + _file
        if platform.system() == "Windows":
            path = re.sub(r"[/\\]+", re.escape(os.sep), path)
        path = re.sub(r"[/\\]+$", "", path)
        return path
    return str(_file)


def load_signatures(deep_scan=False):
    # Load signatures for PHP files
    total_databases = 0
    loaded_databases = 0

    for root, dirnames, filenames in os.walk(SIGNATURES_PATH):
        for filename in filenames:
            total_databases += 1

    for root, dirnames, filenames in os.walk(os.path.join(SIGNATURES_PATH, "checksum")):
        for filename in fnmatch.filter(filenames, '*.json'):
            try:
                loaded_databases += 1
                dbdata = open(os.path.join(root, filename)).read()
                signatures = json.loads(dbdata)

                for signatureHash in signatures["Database_Hash"]:
                    if len(signatureHash["Malware_Hash"]) > 8:
                        HASHTABLE[signatureHash["Malware_Hash"]] = signatureHash["Malware_Name"]
                    else:
                        SORT_HASHTABLE.append(signatureHash["Malware_Hash"])
                progress_bar(loaded_databases, total_databases, "Loading signature database...")
            except:
                pass

    yara_databases = 0
    if IS_YARA and deep_scan:
        for root, dirnames, filenames in os.walk(os.path.join(SIGNATURES_PATH, "rules")):
            for filename in fnmatch.filter(filenames, '*.yar'):
                try:
                    loaded_databases += 1
                    filepath = os.path.join(root, filename)
                    rules = yara.compile(filepath=filepath)
                    YARA_RULES.append(rules)
                    yara_databases += 1
                    progress_bar(loaded_databases, total_databases, "Loading signature database...")
                except Exception as e:
                    print(e)
                    # sys.exit()

    progress_bar(total_databases, loaded_databases, "Loading signature database...")

    pmsg("Loaded "+str(len(HASHTABLE))+" malware hash signatures.")
    pmsg("Loaded "+str(len(SORT_HASHTABLE))+" possible malware hash signatures.")
    pmsg("Loaded "+str(yara_databases)+" YARA ruleset databases.")
    load_patterns(os.path.join(SIGNATURES_PATH, 'patterns.db'))


def load_patterns(filename):
    """
    Load regex patterns.

    :param filename:
    :return:
    """
    file_data = None
    if not os.path.isfile(filename):
        return
    with open(filename, 'r') as db_file:
        file_data = db_file.read()

    if not file_data:
        pmsg("No db data found", 'error')
        sys.exit()

    step_1 = base64.b64decode(file_data).decode('utf-8')
    #step_2 = step_1.encode('rot13')
    step_2 = codecs.encode(step_1, 'rot_13')

    json_data = json.loads(step_2)
    # with open('qtt.json', 'w') as _f:
    #     json.dump(json_data, _f, indent=True)
    # sys.exit()
    # print json_data
    for entry in json_data:
        severty = entry[0]
        expression = entry[1]
        details = entry[2]
        try:
            regexp = re.compile(expression, re.MULTILINE)
            PATTERNS.append({
                "pattern": regexp,
                "detail": details
            })
        except Exception as e:
            print(e)
    pmsg("Loaded "+str(len(PATTERNS)) + " patterns")


def is_text(filename):
    try:
        s = open(filename,"r").read(512)
    except  UnicodeDecodeError:
        return False

    text_characters = "".join(list(map(chr, range(32, 127))) + list("\n\r\t\b"))
    _null_trans = str.maketrans("", "", text_characters)
    if not s:
        # Empty files are considered text
        return True
    if "\0" in s:
        # Files with null bytes are likely binary
        return False
    # Get the non-text characters (maps a character to itself then
    # use the 'remove' option to get rid of the text characters.)
    t = s.translate(_null_trans)
    # If more than 30% non-text characters, then
    # this is considered a binary file
    if float(len(t))/float(len(s)) > 0.30:
        return False
    return True


def find_wordpress_install(path):
    result = []
    name = 'wp-config.php'
    for root, dirs, files in os.walk(path):
        if name in files:
            result.append({
                "root": root,
                "name": name
            })
    return result


def php_scan(_file):
    p = subprocess.Popen("php -d max_execution_time=5 php-scanner.php -f signatures/patterns.db -i {}".format(_file),
                     shell=True, stdout=subprocess.PIPE)

    result = p.communicate()[0]

    if p.returncode == 255:
        return "Timeout while scanning"

    if len(result) < 1:
        return None
    else:
        return repr(result)


def has_php(_file_path):
    """
    Check if file has php in it

    :param _file_path:
    :return:
    """
    try:
        with open(_file_path) as f:
            contents = f.read()
            if '<?php' in contents:
                return True
            all_results = php_pattern.findall(contents)
            if len(all_results) > 1:
                return True
        return False
    except UnicodeDecodeError:
        return False


class WordPressScanner:
    """WordPress Scanner"""

    def __init__(self, path, send_hash=True):
        self.version = None
        self.locale = 'en_US'
        self.send_hash = send_hash
        self.wp_root = path
        self.get_version()
        self.get_locale()
        self.white_list_files = set()
        self.plugins = get_plugins(self.wp_root)
        self.themes = get_themes(self.wp_root)
        self.results = []
        self.changed_files = set()
        self.deleted_files = set()
        self.extra_files = set()
        self.outdated_plugins = []
        self.outdated_themes = []
        pmsg("WordPress Version: {}, Locale: {}".format(self.version, self.locale), write_output=True)

    def get_version(self):
        version_file_name = 'wp-includes/version.php'
        version_file = os.path.join(self.wp_root, version_file_name)
        if os.path.isfile(version_file):
            with open(version_file, 'r') as vf:
                file_text = vf.read()
                matches = version_pattern.search(file_text)
                if matches:
                    match_dict = matches.groupdict()
                    self.version = match_dict['version']
        else:
            print("Version file not found")

    def get_locale(self):
        config_file_name = 'wp-config.php'
        config_file = os.path.join(self.wp_root, config_file_name)
        if os.path.isfile(config_file):
            with open(config_file, 'r') as vf:
                file_text = vf.read()
                matches = locale_pattern.search(file_text)
                if matches:
                    match_dict = matches.groupdict()
                    self.locale = match_dict['locale']

    def get_wp_checksum(self):
        """Get the checksums for the given version of WordPress."""
        pmsg("Loading checksums from WordPress.")
        url = "https://api.wordpress.org/core/checksums/1.0"
        response = requests.get(url, params={"version": self.version, "locale": self.locale})
        if response.status_code == 200:
            return response.json()['checksums']
        else:
            print("Failed to get checksum" + response.status_code)
        return None

    def validate_checksums(self):
        checksums = self.get_wp_checksum()
        if not checksums:
            print("Not valid checksums")
            return
        for filename in checksums.keys():
            # orig_core_files.add(str(filename))
            file_hash = checksums[filename]
            current_file = os.path.join(self.wp_root, filename)
            if filename.startswith("wp-content/plugins") or filename.startswith("wp-content/themes"):
                if filename not in ["wp-content/plugins/index.php", "wp-content/themes/index.php"]:
                    continue
            if not os.path.isfile(current_file):
                # self.deleted_files.add(current_file)
                # pmsg("File not found : %s" % filename)
                continue
            file_handle = open(current_file, 'rb')
            file_data = file_handle.read()
            file_handle.close()
            _hash = hashlib.md5()
            _hash.update(file_data)
            current_checksum = _hash.hexdigest()
            if current_checksum != file_hash:
                self.changed_files.add(filename)
                pmsg("File changed : %s" % file_path(filename, self.wp_root), "warning")
            else:
                self.white_list_files.add(current_file)
        current_core_files = self.get_core_files()
        orig_core_files = set(checksums.keys())
        extra_files = current_core_files - orig_core_files
        for _file in extra_files:
            self.extra_files.add(_file)
            pmsg("Extra file : %s" % file_path(_file, self.wp_root), 'warning')

    def get_core_files(self):
        core_files = set()
        skip_dirs = ['wp-content', 'wp-content/plugins', 'wp-content/uploads', 'wp-config.php']
        skip_dirs.extend(SKIP_DIRS)
        for _root, dirs, files in os.walk(self.wp_root):
            for file_name in files:
                skip = False
                for skip_dir in skip_dirs:
                    if os.path.relpath(_root, self.wp_root).startswith(skip_dir):
                        skip = True
                        break
                if skip:
                    continue
                file_complete_path = os.path.join(_root, file_name)
                file_rel_path = os.path.relpath(file_complete_path, self.wp_root)
                core_files.add(file_rel_path)
        return core_files

    def check_updates_plugins(self):
        pmsg("Checking plugins Updates")
        data = {
            'plugins': json.dumps({"plugins": self.plugins, "active": []})
        }
        r = requests.post(PLUGIN_UPDATE_URL, data)
        # print "Got response"
        if r.status_code == 200:
            try:
                data = r.json()
                # print data
                outdated_plugins = data["plugins"]
                if len(outdated_plugins) == 0:
                    return
                for plugin_name, data in outdated_plugins.items():
                    self.outdated_plugins.append({'name': plugin_name, 'new_version': data['new_version']})
                    pmsg("Outdated Plugin:: %s" % plugin_name, 'warning')
            except Exception as e:
                pmsg("Failed to check plugins updates: %s" % e.message, 'error')
        else:
            pmsg(r.text, 'error')

    def check_updates_themes(self):
        """
        Check if the updates are available for themes.

        It will request WordPress API to check the updates.
        """
        pmsg("Checking themes Updates")
        themes = {}
        for theme, data in self.themes.items():
            if data:
                themes[theme] = {
                    'Name': data['Name'],
                    'Version': data['Version']
                }
        # print themes
        data = {
            'themes': json.dumps({"themes": themes, "active":""})
        }
        r = requests.post(THEME_UPDATE_URL, data)
        # print "Got response"
        # print r.text
        if r.status_code == 200:
            try:
                data = r.json()
                # print data
                outdated_themes = data["themes"]
                if len(outdated_themes) == 0:
                    return
                for theme_name, data in outdated_themes.items():
                    self.outdated_themes.append({'name': theme_name, 'new_version': data['new_version']})
                    pmsg("Outdated theme:: %s" % theme_name, 'warning')
            except Exception as e:
                pmsg("Failed to check theme updates: %s" % e.message, 'error')
        else:
            pmsg(r.text, 'error')

    def post_data(self, data):
        """
        Send data to server.

        :param data:
        """
        data['uid'] = SYSTEM_UID

        try:
            r = requests.post(SUBMIT_HASH_URL, json.dumps(data), headers={'content-type': 'application/json'}, timeout=60)
            pmsg(r.text)
        except requests.exceptions.Timeout:
            pmsg("Timeout in submit hash", 'error')
            pmsg("Unable to connect server. Request timeout.", 'error')
        except Exception as e:
            print(e)

    def send_plugin_hash(self, plugin, data):
        """
        Send plugin files' hash to server.

        :param plugin: Name of plugin
        :param data: Plugin details
        """
        pmsg("Sending hash for plugin at : %s" % plugin)
        plugins_dir = "{}/wp-content/plugins".format(self.wp_root)
        plugin_file = os.path.join(plugins_dir, plugin)
        plugin_dir = os.path.dirname(plugin_file)
        files_hash = []
        if os.path.samefile(plugins_dir, plugin_dir):
            files_hash.append({'file_name': plugin, 'sha1': checksum_sha1(plugin_file)})
        else:
            for root, dirs, files in os.walk(plugin_dir):
                for name in files:
                    file_path = os.path.join(root, name)
                    files_hash.append({
                        'file_name': os.path.relpath(file_path, plugins_dir),
                        'sha1': checksum_sha1(file_path)
                    })

        version = data['Version']
        self.post_data({
            'version': version,
            'name': plugin,
            'type': 'plugin',
            'hash': files_hash
        })

    def send_theme_hash(self, theme, data):
        themes_dir = "{}/wp-content/themes".format(self.wp_root)
        theme_dir = os.path.join(themes_dir, theme)
        files_hash = []

        for root, dirs, files in os.walk(theme_dir):
            for name in files:
                file_path = os.path.join(root, name)
                files_hash.append({
                    'file_name': os.path.relpath(file_path, themes_dir),
                    'sha1': checksum_sha1(file_path)
                })

        version = data['Version']
        # print len(files_hash)
        self.post_data({
            'version': version,
            'name': theme,
            'type': 'theme',
            'hash': files_hash
        })

    def get_valid_hash(self, name, version, _type):
        pmsg("Getting hash for %s - %s, Version: %s" % (_type, name, version))
        cache_path = "cache/" + hashlib.md5( ("%s%s%s" % (name, version, _type)).encode("UTF-8") ).hexdigest() + ".json"
        #Cache exists ?
        if os.path.isfile(cache_path):
            with open(cache_path, "r") as openfile:
                json_object = json.load(openfile)
                return json_object

        try:            
            r = requests.get(VALID_HASH_URL, params={'name': name, 'version': version, 'type': _type}, timeout=10)
            json_object = r.json()
            if r.status_code == 200:
                with open(cache_path, "w") as outfile:
                    json.dump(json_object,outfile)
                return json_object
        except requests.exceptions.Timeout:
            pmsg("Timeout in get_valid_hash", 'error')
            pmsg("Unable to connect server. Request timeout.", 'error')
        except Exception as e:
            print(e)
        return None

    def validate_plugins_hash(self):
        for plugin, data in self.plugins.items():
            valid_hash = self.get_valid_hash(plugin, data['Version'], 'plugin')
            if valid_hash:
                pmsg("Got valid hash")
                plugins_dir = "{}/wp-content/plugins".format(self.wp_root)
                for _file in valid_hash:
                    _file_path = os.path.join(plugins_dir, _file['file_name'])
                    if os.path.exists(_file_path) and os.path.isfile(_file_path):
                        if _file['sha1'] == checksum_sha1(_file_path):
                            self.white_list_files.add(_file_path)
                            # pmsg("White listing %s" %_file_path)
                        else:
                            self.changed_files.add(_file_path)
                            pmsg("File Changed: %s" % _file_path, 'error')
                    else:
                        self.deleted_files.add(_file_path)
                        pmsg("File not found : %s" % _file_path, 'error')
            elif self.send_hash:
                self.send_plugin_hash(plugin, data)

    def validate_themes_hash(self):
        for theme, data in self.themes.items():
            valid_hash = self.get_valid_hash(theme, data['Version'], 'theme')
            if valid_hash:
                pmsg("Got valid hash")
                themes_dir = "{}/wp-content/themes".format(self.wp_root)
                for _file in valid_hash:
                    _file_path = os.path.join(themes_dir, _file['file_name'])
                    if os.path.exists(_file_path) and os.path.isfile(_file_path):
                        if _file['sha1'] == checksum_sha1(_file_path):
                            self.white_list_files.add(_file_path)
                            # pmsg("White listing %s" %_file_path)
                        else:
                            self.changed_files.add(_file_path)
                            pmsg("File Changed: %s" % _file_path, 'error')
                    else:
                        self.deleted_files.add(_file_path)
                        pmsg("File not found : %s" % _file_path, 'error')
            elif self.send_hash:
                pmsg("Hash not found for theme %s, Uploading current hash" % theme)
                self.send_theme_hash(theme, data)

    def start_scanning(self):
        pmsg("Start Scanning. . .")
        self.validate_checksums()
        self.check_updates_plugins()
        self.check_updates_themes()
        self.validate_plugins_hash()
        self.validate_themes_hash()

    def deep_scan(self, _deep_scan=False):
        if _deep_scan:
            pmsg("Running deep scanning with YARA rules")
        total_files = 0
        total_scanned = 0

        def infected_found(_filename, details):
            if show_full_path:
                _filename = os.path.join(self.wp_root, _filename)
            _result = {
                'filename': _filename,
                'details': details
            }
            return _result

        for root, dirnames, filenames in os.walk(self.wp_root):
            for filename in filenames:
                total_files += 1

        ct = time.time()

        for root, dirnames, filenames in os.walk(self.wp_root):
            for filename in filenames:
                total_scanned += 1
                current_file = os.path.join(root, filename)
                progress_bar(total_scanned, total_files, "Scanning "+str(self.wp_root)+" for malwares...")

                malware = False

                if current_file in self.white_list_files:
                    continue
                skip = False
                for skip_dir in SKIP_DIRS:
                    if os.path.relpath(current_file, self.wp_root).startswith(skip_dir):
                        skip = True
                        break
                if skip:
                    continue

                # Check the last modified time for the file
                try:
                    lt = os.path.getctime(current_file)
                    diff = ct - lt
                    if 0 < days < diff/(60*60*24):
                        pmsg("Old file skipping...", write_output=False)
                        continue
                except Exception as e:
                    print("Exception Checking Last Modified time ",e)


                pmsg("Scanning : "+ current_file, "debug")
                # print current_file
                try:
                    file_handle = open(current_file, 'rb')
                    file_data = file_handle.read()
                except:
                    pmsg("Can't read file %s" % current_file)
                    continue

                _hash = hashlib.md5()
                _hash.update(file_data)
                current_checksum = _hash.hexdigest()
                # print current_checksum
                if current_checksum in HASHTABLE:
                    malware = str(HASHTABLE[current_checksum])
                    self.results.append(infected_found(current_file, "!!! Malware : {}".format(malware)))
                elif current_checksum[:8] in SORT_HASHTABLE:
                    self.results.append(infected_found(current_file, "!!! Malware : May be"))

                #if current_file.split(".")[-1] in ["po", "pem", "crt"] and not has_php(current_file):
                #    pmsg("Skipping %s file." % current_file)
                #    continue

                if is_text(current_file) and _deep_scan:
                    if IS_YARA:
                        for rules in YARA_RULES:
                            try:
                                result = rules.match(data=file_data)
                                if result:
                                    for rule in result:
                                        self.results.append(infected_found(current_file, str(rule).replace("_", " ")))
                            except:
                                pass
                    try:            
                        file_data_string = file_data.decode("utf-8")            
                    except  UnicodeDecodeError:
                        continue

                    for pattern in PATTERNS:
                        match = False

                        try:
                            with timeout(seconds=1):                
                                match = pattern['pattern'].match(file_data_string)
                        except TimeoutError as e: 
                            php_result = php_scan(current_file)

                            if php_result:
                                pmsg("Possible Malware[PH]: "+ current_file +" : "+ php_result, 'error')
                                self.results.append(infected_found(current_file, php_result))
                            break

                        if match:      
                            pmsg("Possible Malware[PY]: "+ current_file +" : "+ pattern['detail'], 'error')
                            self.results.append(infected_found(current_file, pattern['detail']))
                            
                    # php_result = php_scan(current_file)
                    # if php_result:
                    #     pmsg("Possible Malware: "+ current_file +" : "+ php_result, 'error')
                    #     self.results.append(infected_found(current_file, php_result))

        # self.get_report()

    def get_report(self):
        pmsg("="*46 + " REPORT "+"="*46, write_output=True)
        for _file in self.extra_files:
            if show_full_path:
                _file = os.path.join(self.wp_root, _file)
            pmsg("[Report] Extra File : "+ _file, 'warning', write_output=True)

        for _file in self.changed_files:
            if show_full_path:
                _file = os.path.join(self.wp_root, _file)
            pmsg("[Report] Changed File : "+ _file, 'error', write_output=True)

        for _file in self.deleted_files:
            if show_full_path:
                _file = os.path.join(self.wp_root, _file)
            pmsg("[Report] Deleted File : "+ _file, 'warning', write_output=True)

        for _plugin in self.outdated_plugins:
            pmsg("[Report] Outdated Plugin: %s, New Version: %s" %(_plugin['name'], _plugin['new_version']), 'warning', write_output=True)

        for _theme in self.outdated_themes:
            pmsg("[Report] Outdated Theme: %s, New Version: %s" %(_theme['name'], _theme['new_version']), 'warning', write_output=True)

        for result in self.results:
            pmsg("[Report] Scan result for file "+str(result["filename"])+" : "+str(result["details"]), 'error', write_output=True)

        pmsg("="*100, write_output=True)
        pmsg("Scan completed.")


def main(web_path, deep_scan=False, send_hash=True):
    global OUTPUT_FILE, SIGNATURES_PATH

    pmsg("Scan Path : %s" % web_path)
    if deep_scan:
        pmsg("Deep Scan with YARA rules is enabled.")
        if not IS_YARA:
            pmsg("YARA is not installed. Skipping deep scan.", "error")

    SIGNATURES_PATH = os.path.join(get_application_path(), 'signatures')
    if os.path.isdir(web_path):
        pass
    else:
        pmsg("Unable to find target folder, please check input.", 'error', False)
        sys.exit()

    pmsg("Starting WordPress Malware Scanner")

    if os.path.isdir(SIGNATURES_PATH):
        load_signatures(deep_scan)
    else:
        pmsg("Unable to find signatures folder, please check installation.", 'error', False)
        sys.exit()

    config_files = find_wordpress_install(web_path)
    # print config_files
    # sys.exit()
    for wp_install in config_files:
        pmsg("Scanning : {}".format(wp_install['root']), write_output=True)
        try:
            wp = WordPressScanner(path=wp_install['root'], send_hash=send_hash)
            wp.start_scanning()
            if len(HASHTABLE) > 0 or deep_scan:
                wp.deep_scan(deep_scan)
            wp.get_report()
        except Exception as e:
            print(e)
            traceback.print_exc()
            pmsg("Site Scan Error : {}".format(wp_install['root']))


if __name__ == '__main__':
    class Action(object):
        pass
    actions = Action()

    def scan_dir(_path):
        if os.path.exists(_path):
            return _path
        raise argparse.ArgumentTypeError("%s is not a valid path" % _path)

    if not os.path.exists("cache"):
        os.makedirs("cache")

    parser = argparse.ArgumentParser(description="WordPress Malware Scanner")
    parser.add_argument('path', type=scan_dir, help="Path to scan for WordPress installation. Default : %(default)s")
    parser.add_argument('-d', '--deep-scan', default=False, action='store_true', dest='deep_scan', help="Deep scan with YARA rules")
    parser.add_argument('-f', '--full-path', default=False, action='store_true', dest='full_path', help="Show full path of file.")
    parser.add_argument('--days', default=0, help="Ignore the file if it is older.")
    parser.add_argument('--skip', nargs='*', help="Skip files")
    parser.add_argument('-v', '--verbose', default=False, action='store_true', dest='verbose', help="Show debug")
    parser.parse_args(namespace=actions)
    # global VERBOSE, SKIP_DIRS, show_full_path
    VERBOSE = actions.verbose
    if actions.skip:
        SKIP_DIRS = actions.skip
    if actions.full_path:
        show_full_path = actions.full_path
    days = actions.days
    main(actions.path, actions.deep_scan)
