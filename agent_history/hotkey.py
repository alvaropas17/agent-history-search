"""Windows-only global shortcut for the search window; resident, no dependencies.

The Hotkey field of a .lnk takes about 3 s to react on Windows; RegisterHotKey is instant.
If the window is open it comes to the front; if not, the window is launched. A second
instance exits on its own because the shortcut is already registered.
"""
import os
from pathlib import Path
import subprocess
import sys

from .core import HistoryError

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
MODIFIERS = {'alt': MOD_ALT, 'ctrl': MOD_CONTROL, 'shift': MOD_SHIFT, 'win': MOD_WIN}
WM_HOTKEY, SW_RESTORE, NO_WINDOW = 0x0312, 9, 0x08000000
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'AgentHistorySearchHotkey'
ENTRY = Path(__file__).resolve().parents[1] / 'history_window.py'


def parse_shortcut(text):
    """'ctrl+alt+h' -> (modifier flags, virtual key). Letters and digits need one modifier."""
    *mods, key = [part.strip().lower() for part in text.split('+')]
    if (not mods or len(set(mods)) != len(mods) or any(m not in MODIFIERS for m in mods)
            or len(key) != 1 or not key.isascii() or not key.isalnum()):
        raise HistoryError('invalid_shortcut:use_a_form_like_ctrl+alt+h')
    flags = MOD_NOREPEAT
    for mod in mods:
        flags |= MODIFIERS[mod]
    return flags, ord(key.upper())


def require_windows():
    if os.name != 'nt':
        raise HistoryError('hotkey_is_windows_only:bind_history_window.py_to_a_system_shortcut')


def run(shortcut, title, launch_args):
    require_windows()
    import ctypes
    from ctypes import wintypes
    flags, key = parse_shortcut(shortcut)
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    # BOOL is signed here: -1 is an error, 0 is WM_QUIT, and positive values are messages.
    user32.GetMessageW.restype = ctypes.c_int

    def find_window():
        found = []

        def check(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                text = ctypes.create_unicode_buffer(200)
                user32.GetWindowTextW(hwnd, text, 200)
                if text.value == title:
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(callback(check), 0)
        return found[0] if found else None

    def on_hotkey():
        window = find_window()
        if window:
            if user32.IsIconic(window):
                user32.ShowWindow(window, SW_RESTORE)
            user32.SetForegroundWindow(window)
        else:
            subprocess.Popen([sys.executable, '-B', str(ENTRY), *launch_args], creationflags=NO_WINDOW,
                             close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)

    if not user32.RegisterHotKey(None, 1, flags, key):
        print('The shortcut is taken by another program or a running instance.', file=sys.stderr)
        return 1
    try:
        message = wintypes.MSG()
        while True:
            status = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
            if status == -1:
                raise HistoryError(f'hotkey_message_loop_failed:winerror_{ctypes.get_last_error()}')
            if status == 0:
                break
            if message.message == WM_HOTKEY:
                try:
                    on_hotkey()
                except OSError:
                    pass
    finally:
        user32.UnregisterHotKey(None, 1)
    return 0


def startup_command(argv):
    from .window import background_python, parser
    # Parse before serializing: argparse also accepts abbreviated option names. Never
    # persist a startup-install/remove action, a server-only action, or --no-open.
    args = parser().parse_args(argv)
    kept = ['--hotkey', '--shortcut', args.shortcut]
    if args.demo:
        kept.append('--demo')
    if args.port is not None:
        kept.extend(['--port', str(args.port)])
    for source, path in args.store:
        kept.extend(['--store', f'{source}={path}'])
    for option, value in (('--only', args.only), ('--cache-dir', args.cache_dir),
                          ('--browser', args.browser)):
        if value is not None:
            # The equals form preserves values that begin with a dash.
            kept.append(f'{option}={value}')
    return subprocess.list2cmdline([str(background_python()), '-B', str(ENTRY), *kept])


def install_startup(argv, registry=None):
    """Per-user Run value: no administrator rights, removed by --uninstall-startup."""
    require_windows()
    if registry is None:
        import winreg as registry
    with registry.CreateKey(registry.HKEY_CURRENT_USER, RUN_KEY) as key:
        registry.SetValueEx(key, RUN_VALUE, 0, registry.REG_SZ, startup_command(argv))
    print('The shortcut starts when you sign in. Undo it with --uninstall-startup.')
    return 0


def uninstall_startup(registry=None):
    require_windows()
    if registry is None:
        import winreg as registry
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
            registry.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        print('Nothing to remove.')
        return 0
    print('Removed. A running shortcut stays active until you sign out.')
    return 0
