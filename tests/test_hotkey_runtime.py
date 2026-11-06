"""Mocked hotkey runtime checks. No Windows API, process, registry, or window is used."""
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import unittest
from unittest import mock

from agent_history import hotkey
from agent_history.core import HistoryError


class HotkeyRuntime(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.api = mock.Mock()
        self.api.RegisterHotKey.return_value = True
        self.api.GetMessageW.return_value = 0
        self.api.EnumWindows.return_value = True
        self.api.IsWindowVisible.return_value = True
        self.api.IsIconic.return_value = False
        self.dll = self.stack.enter_context(mock.patch.object(
            ctypes, 'WinDLL', return_value=self.api, create=True))
        # Use a real ctypes callback with a mock enumerator, also on non-Windows hosts.
        self.stack.enter_context(mock.patch.object(
            ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE, create=True))
        self.stack.enter_context(mock.patch.object(
            ctypes, 'get_last_error', return_value=1234, create=True))
        self.stack.enter_context(mock.patch.object(hotkey, 'require_windows'))
        self.spawn = self.stack.enter_context(mock.patch.object(hotkey.subprocess, 'Popen'))

    def messages(self, *messages):
        pending = iter(messages)

        def receive(pointer, hwnd, low, high):
            message = ctypes.cast(pointer, ctypes.POINTER(wintypes.MSG)).contents
            message.message = next(pending)
            message.wParam = 1
            return 1

        self.api.GetMessageW.side_effect = receive

    def test_quit_unregisters_and_declares_api_types(self):
        self.assertEqual(hotkey.run('ctrl+alt+h', 'Fixture', []), 0)
        self.dll.assert_called_once_with('user32', use_last_error=True)
        self.api.RegisterHotKey.assert_called_once_with(
            None, 1, hotkey.MOD_CONTROL | hotkey.MOD_ALT | hotkey.MOD_NOREPEAT, ord('H'))
        self.api.UnregisterHotKey.assert_called_once_with(None, 1)
        self.assertEqual(self.api.GetMessageW.restype, ctypes.c_int)
        self.assertEqual(self.api.GetMessageW.argtypes,
                         [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT])
        self.assertEqual(self.api.GetWindowTextW.restype, ctypes.c_int)
        for name in ('EnumWindows', 'IsWindowVisible', 'IsIconic', 'ShowWindow',
                     'SetForegroundWindow', 'RegisterHotKey', 'UnregisterHotKey'):
            with self.subTest(api=name):
                self.assertEqual(getattr(self.api, name).restype, wintypes.BOOL)
        self.assertEqual(self.api.UnregisterHotKey.argtypes, [wintypes.HWND, ctypes.c_int])
        self.assertEqual(self.api.SetForegroundWindow.argtypes, [wintypes.HWND])
        self.spawn.assert_not_called()

    def test_message_error_is_not_reported_as_success(self):
        self.api.GetMessageW.return_value = -1
        with self.assertRaisesRegex(HistoryError, 'hotkey_message_loop_failed:winerror_1234'):
            hotkey.run('ctrl+alt+h', 'Fixture', [])
        self.api.UnregisterHotKey.assert_called_once_with(None, 1)
        self.spawn.assert_not_called()

    def test_conflict_does_not_unregister_or_dispatch(self):
        self.api.RegisterHotKey.return_value = False
        with mock.patch('builtins.print'):
            self.assertEqual(hotkey.run('ctrl+alt+h', 'Fixture', []), 1)
        self.api.UnregisterHotKey.assert_not_called()
        self.api.GetMessageW.assert_not_called()
        self.api.EnumWindows.assert_not_called()
        self.spawn.assert_not_called()

    def test_invalid_shortcut_does_not_load_windows_api(self):
        with self.assertRaises(HistoryError):
            hotkey.run('ctrl+ctrl+h', 'Fixture', [])
        self.dll.assert_not_called()
        self.api.RegisterHotKey.assert_not_called()

    def test_keyboard_interrupt_unregisters(self):
        self.api.GetMessageW.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            hotkey.run('ctrl+alt+h', 'Fixture', [])
        self.api.UnregisterHotKey.assert_called_once_with(None, 1)

    def test_dispatch_exception_unregisters(self):
        self.messages(hotkey.WM_HOTKEY)
        self.api.EnumWindows.side_effect = RuntimeError('fixture failure')
        with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
            hotkey.run('ctrl+alt+h', 'Fixture', [])
        self.api.UnregisterHotKey.assert_called_once_with(None, 1)

    def test_existing_minimized_window_keeps_pointer_width(self):
        handle = 0x1234567887654321 if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x12345678

        def enumerate_windows(callback, extra):
            self.assertFalse(callback(handle, extra))
            return True

        def window_title(hwnd, buffer, length):
            self.assertEqual(hwnd, handle)
            buffer.value = 'Fixture'
            return len(buffer.value)

        self.api.EnumWindows.side_effect = enumerate_windows
        self.api.GetWindowTextW.side_effect = window_title
        self.api.IsIconic.return_value = True
        calls = iter((1, 0))

        def receive(pointer, *args):
            ctypes.cast(pointer, ctypes.POINTER(wintypes.MSG)).contents.message = hotkey.WM_HOTKEY
            return next(calls)

        self.api.GetMessageW.side_effect = receive
        self.assertEqual(hotkey.run('ctrl+alt+h', 'Fixture', []), 0)
        self.api.IsWindowVisible.assert_called_once_with(handle)
        self.api.ShowWindow.assert_called_once_with(handle, hotkey.SW_RESTORE)
        self.api.SetForegroundWindow.assert_called_once_with(handle)
        self.api.UnregisterHotKey.assert_called_once_with(None, 1)
        self.spawn.assert_not_called()

    def test_missing_window_launches_exact_argument_list(self):
        calls = iter((1, 0))

        def receive(pointer, *args):
            ctypes.cast(pointer, ctypes.POINTER(wintypes.MSG)).contents.message = hotkey.WM_HOTKEY
            return next(calls)

        self.api.GetMessageW.side_effect = receive
        arguments = ['--demo', '--browser', 'C:/Fixture Browser/browser.exe', '--port', '47615']
        self.assertEqual(hotkey.run('ctrl+alt+h', 'Fixture', arguments), 0)
        argv = self.spawn.call_args.args[0]
        self.assertEqual(argv, [hotkey.sys.executable, '-B', str(hotkey.ENTRY), *arguments])
        self.assertEqual(self.spawn.call_args.kwargs, {
            'creationflags': hotkey.NO_WINDOW, 'close_fds': True,
            'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL})
        self.api.SetForegroundWindow.assert_not_called()
        self.api.UnregisterHotKey.assert_called_once_with(None, 1)

    def test_spawn_error_does_not_end_listener(self):
        calls = iter((1, 0))

        def receive(pointer, *args):
            ctypes.cast(pointer, ctypes.POINTER(wintypes.MSG)).contents.message = hotkey.WM_HOTKEY
            return next(calls)

        self.api.GetMessageW.side_effect = receive
        self.spawn.side_effect = OSError('fixture failure')
        self.assertEqual(hotkey.run('ctrl+alt+h', 'Fixture', []), 0)
        self.assertEqual(self.api.GetMessageW.call_count, 2)
        self.api.UnregisterHotKey.assert_called_once_with(None, 1)

    def test_unrelated_messages_do_not_launch(self):
        self.api.GetMessageW.side_effect = (1, 0)
        self.assertEqual(hotkey.run('ctrl+alt+h', 'Fixture', []), 0)
        self.spawn.assert_not_called()
        self.api.EnumWindows.assert_not_called()


class StartupArguments(unittest.TestCase):
    def command(self, arguments):
        from agent_history import window
        encoder = subprocess.list2cmdline
        python = 'C:/Program Files/Fixture Python/pythonw.exe'
        with mock.patch.object(window, 'background_python', return_value=python), \
                mock.patch.object(subprocess, 'list2cmdline', wraps=encoder) as serialize:
            command = hotkey.startup_command(arguments)
        return command, serialize.call_args.args[0], python

    def test_transient_and_abbreviated_modes_are_not_persisted(self):
        command, argv, python = self.command([
            '--install-start', '--uninstall-start', '--hotkey', '--serve', '--no-open',
            '--shortcut=ctrl+alt+j', '--port', '47615',
            '--store', 'pi=C:/Fixture Histories', '--store', 'claude=D:/Fixture Other',
            '--only', 'pi,claude', '--cache-dir', 'C:/Fixture Cache',
            '--browser=C:/Program Files/Fixture Browser/browser.exe'])
        self.assertEqual(argv, [python, '-B', str(hotkey.ENTRY), '--hotkey', '--shortcut', 'ctrl+alt+j',
                               '--port', '47615', '--store', f'pi={Path("C:/Fixture Histories")}',
                               '--store', f'claude={Path("D:/Fixture Other")}', '--only=pi,claude',
                               f'--cache-dir={Path("C:/Fixture Cache")}',
                               '--browser=C:/Program Files/Fixture Browser/browser.exe'])
        self.assertEqual(command, subprocess.list2cmdline(argv))
        self.assertIn('"C:/Program Files/Fixture Python/pythonw.exe"', command)
        self.assertEqual(argv.count('--hotkey'), 1)
        for option in ('--install-start', '--uninstall-start', '--serve', '--no-open'):
            self.assertNotIn(option, argv)

    def test_demo_and_default_shortcut_are_preserved(self):
        _, argv, python = self.command(['--install-startup', '--demo'])
        self.assertEqual(argv, [python, '-B', str(hotkey.ENTRY), '--hotkey',
                               '--shortcut', 'ctrl+alt+h', '--demo'])

    def test_flag_like_option_value_is_preserved(self):
        from agent_history import window
        _, argv, _ = self.command(['--install-startup', '--browser=--hotkey'])
        self.assertIn('--browser=--hotkey', argv)
        self.assertEqual(window.parser().parse_args(argv[3:]).browser, '--hotkey')


if __name__ == '__main__':
    unittest.main()
