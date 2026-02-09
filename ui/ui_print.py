from base import *

from ui import ui_settings

sameline = False
sameline_log = False
config_dir = "."

# ── Route all legacy ui_print messages through Python logging ───────
import logging as _logging
_legacy_logger = _logging.getLogger("legacy")


def ui_cls(path='',update=""):
    os.system('cls' if os.name == 'nt' else 'clear')
    logo(path=path,update=update)

def logo(path='',update=""):
    print('                                                         ')
    print('           __                  __     __         _     __')
    print('    ____  / /__  _  __    ____/ /__  / /_  _____(_)___/ /')
    print('   / __ \/ / _ \| |/_/   / __  / _ \/ __ \/ ___/ / __  / ')
    print('  / /_/ / /  __/>  <    / /_/ /  __/ /_/ / /  / / /_/ /  ')
    print(' / .___/_/\___/_/|_|____\__,_/\___/_.___/_/  /_/\__,_/   ')
    print('/_/               /_____/                         [v' + ui_settings.version[0] + ']' + update)
    print()
    print(path)
    print()
    sys.stdout.flush()

def set_log_dir(config):
    global config_dir
    config_dir = config

def ui_print(string: str, debug="true"):
    """Legacy print function — routes through Python logging so all output is unified."""
    global sameline
    global sameline_log
    try:
        # Emit via Python logging (will go to console + file + ring buffer)
        if string and string != 'done':
            _legacy_logger.debug(string)
        # Keep original sameline tracking for any legacy CLI callers
        if debug == "true":
            if string == 'done' and sameline:
                sameline = False
            elif sameline and string.startswith('done'):
                sameline = False
            elif sameline and string.endswith('...'):
                sameline = True
            elif string.endswith('...'):
                sameline = True
            elif not string.startswith('done') and sameline:
                sameline = False
            elif not string.startswith('done'):
                sameline = False
    except:
        pass
