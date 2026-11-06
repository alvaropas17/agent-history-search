//! Windows hotkey and Edge app launcher. No shell, PATH lookup, or desktop work in tests.

use std::ffi::c_void;
use std::io;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::ptr;

const WINDOW_TITLE: &str = "Buscar en el historial";
const HOTKEY_ID: i32 = 1;
const MOD_ALT: u32 = 0x0001;
const MOD_CONTROL: u32 = 0x0002;
const MOD_NOREPEAT: u32 = 0x4000;
const VK_H: u32 = 0x48;
const WM_HOTKEY: u32 = 0x0312;
const SW_RESTORE: i32 = 9;
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

type Hwnd = *mut c_void;

#[repr(C)]
#[derive(Default)]
struct Point {
    x: i32,
    y: i32,
}

#[repr(C)]
#[derive(Default)]
struct Message {
    hwnd: Hwnd,
    message: u32,
    w_param: usize,
    l_param: isize,
    time: u32,
    pt: Point,
    // Reserved Win32 field; also occupies MSG's trailing padding on Win64.
    private: u32,
}

#[link(name = "user32")]
unsafe extern "system" {
    fn RegisterHotKey(hwnd: Hwnd, id: i32, modifiers: u32, key: u32) -> i32;
    fn UnregisterHotKey(hwnd: Hwnd, id: i32) -> i32;
    fn GetMessageW(message: *mut Message, hwnd: Hwnd, min: u32, max: u32) -> i32;
    fn EnumWindows(callback: unsafe extern "system" fn(Hwnd, isize) -> i32, data: isize)
        -> i32;
    fn IsWindowVisible(hwnd: Hwnd) -> i32;
    fn GetWindowTextW(hwnd: Hwnd, text: *mut u16, capacity: i32) -> i32;
    fn IsIconic(hwnd: Hwnd) -> i32;
    fn ShowWindow(hwnd: Hwnd, command: i32) -> i32;
    fn SetForegroundWindow(hwnd: Hwnd) -> i32;
}

struct HotkeyRegistration;

impl Drop for HotkeyRegistration {
    fn drop(&mut self) {
        // SAFETY: Registration and this guard stay on the same message-loop thread.
        unsafe { UnregisterHotKey(ptr::null_mut(), HOTKEY_ID) };
    }
}

struct WindowSearch {
    title: Vec<u16>,
    found: Hwnd,
}

unsafe extern "system" fn check_window(hwnd: Hwnd, data: isize) -> i32 {
    // SAFETY: EnumWindows invokes this callback synchronously with the live context.
    let search = unsafe { &mut *(data as *mut WindowSearch) };
    // This buffer is larger than the target title, so truncation cannot match it.
    let mut title = [0u16; 256];

    // SAFETY: hwnd comes from EnumWindows and title is a writable UTF-16 buffer.
    if unsafe { IsWindowVisible(hwnd) } != 0 {
        let length = unsafe { GetWindowTextW(hwnd, title.as_mut_ptr(), title.len() as i32) };

        if length > 0 && title[..length as usize] == search.title {
            search.found = hwnd;
            return 0;
        }
    }

    1
}

fn focus_existing_window() -> Result<bool, String> {
    let mut search = WindowSearch {
        title: WINDOW_TITLE.encode_utf16().collect(),
        found: ptr::null_mut(),
    };

    // SAFETY: The stack context remains live throughout synchronous enumeration.
    let result = unsafe { EnumWindows(check_window, &mut search as *mut WindowSearch as isize) };

    if search.found.is_null() {
        if result == 0 {
            return Err(format!("EnumWindows failed: {}", io::Error::last_os_error()));
        }

        return Ok(false);
    }

    // SAFETY: The handle was returned by EnumWindows. Win32 checks stale handles.
    unsafe {
        if IsIconic(search.found) != 0 {
            ShowWindow(search.found, SW_RESTORE);
        }

        if SetForegroundWindow(search.found) == 0 {
            // SetForegroundWindow does not promise a meaningful GetLastError value.
            return Err("Windows did not allow the existing History Box window to receive focus".into());
        }
    }

    Ok(true)
}

fn hidden_command(executable: &Path) -> Command {
    let mut command = Command::new(executable);
    command
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    command
}

fn handle_hotkey() -> Result<(), String> {
    if focus_existing_window()? {
        return Ok(());
    }

    let executable = std::env::current_exe()
        .map_err(|error| format!("Cannot locate the current executable: {error}"))?;

    hidden_command(&executable)
        .arg("open")
        .spawn()
        .map_err(|error| format!("Cannot start the History Box launcher: {error}"))?;
    Ok(())
}

/// Register Ctrl+Alt+H and process hotkey messages until WM_QUIT or an error.
/// This function must stay on its calling thread. Normal returns unregister the key.
/// Forced process termination does not run Drop; Windows removes the registration.
pub fn run_hotkey() -> Result<(), String> {
    // SAFETY: A null HWND binds registration to the current thread's message queue.
    if unsafe {
        RegisterHotKey(
            ptr::null_mut(),
            HOTKEY_ID,
            MOD_CONTROL | MOD_ALT | MOD_NOREPEAT,
            VK_H,
        )
    } == 0
    {
        return Err(format!(
            "Cannot register Ctrl+Alt+H (another process may own it): {}",
            io::Error::last_os_error()
        ));
    }

    let _registration = HotkeyRegistration;
    let mut message = Message::default();

    loop {
        // SAFETY: message has the native MSG layout and remains writable.
        match unsafe { GetMessageW(&mut message, ptr::null_mut(), 0, 0) } {
            -1 => return Err(format!("GetMessageW failed: {}", io::Error::last_os_error())),
            0 => return Ok(()),
            _ => {
                if message.message == WM_HOTKEY && message.w_param == HOTKEY_ID as usize {
                    if let Err(error) = handle_hotkey() {
                        // A denied focus request must not disable the global shortcut.
                        crate::log_error(&error);
                    }
                }
            }
        }
    }
}

fn edge_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();

    for variable in ["ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"] {
        if let Some(root) = std::env::var_os(variable) {
            let root = PathBuf::from(root);

            if root.is_absolute() {
                candidates.push(root.join("Microsoft/Edge/Application/msedge.exe"));
            }
        }
    }

    candidates.push(PathBuf::from(
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ));
    candidates.push(PathBuf::from(
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ));
    candidates
}

fn select_edge_candidate(
    candidates: &[PathBuf],
    is_file: impl Fn(&Path) -> bool,
) -> Option<PathBuf> {
    candidates
        .iter()
        .find(|path| path.is_absolute() && is_file(path))
        .cloned()
}

fn app_argument(url: &str) -> Result<String, String> {
    let remainder = url
        .strip_prefix("http://")
        .or_else(|| url.strip_prefix("https://"))
        .ok_or_else(|| "The app URL must use http:// or https://".to_string())?;

    if url.len() > 8192
        || url.chars().any(|c| c.is_control() || c.is_whitespace())
        || remainder.split(['/', '?', '#']).next().unwrap_or("").is_empty()
    {
        return Err("The app URL is empty, too long, or contains invalid characters".into());
    }

    Ok(format!("--app={url}"))
}

/// Open an HTTP(S) URL in Edge app mode using an absolute, existing executable.
/// Candidate verification checks file presence, not the executable's signature.
pub fn open_app(url: &str) -> Result<(), String> {
    let argument = app_argument(url)?;
    let executable = select_edge_candidate(&edge_candidates(), Path::is_file)
        .ok_or_else(|| "Microsoft Edge was not found in its standard installation directories".to_string())?;

    hidden_command(&executable)
        .arg(argument)
        .spawn()
        .map_err(|error| format!("Cannot start Microsoft Edge app mode: {error}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hotkey_has_required_modifiers() {
        assert_eq!(MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x4003);
        assert_eq!(VK_H, u32::from(b'H'));
    }

    #[test]
    fn rejects_non_web_urls_and_control_characters() {
        for url in [
            "",
            "file:///C:/Windows/explorer.exe",
            "javascript:alert(1)",
            "--remote-debugging-port=9222",
            "http://",
            "http:///path",
            "http://?query",
            "http://127.0.0.1/\0",
            "http://127.0.0.1/\n",
            "http://127.0.0.1/ --other-option",
        ] {
            assert!(app_argument(url).is_err(), "accepted {url:?}");
        }
        assert!(app_argument(&format!("http://localhost/{}", "a".repeat(8192))).is_err());
    }

    #[test]
    fn url_is_one_argument_without_shell_interpolation() {
        let executable = Path::new(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe");
        let url = "http://127.0.0.1:47613/?t=a&literal=\"quoted\";$value";
        let mut command = hidden_command(executable);
        command.arg(app_argument(url).unwrap());

        assert_eq!(command.get_program(), executable.as_os_str());
        assert_eq!(
            command.get_args().collect::<Vec<_>>(),
            [std::ffi::OsString::from(format!("--app={url}"))]
        );
    }

    #[test]
    fn rejects_relative_candidates_even_when_the_file_probe_accepts_them() {
        let candidates = vec![
            PathBuf::from("msedge.exe"),
            PathBuf::from(r"C:\missing\msedge.exe"),
            PathBuf::from(r"D:\Apps\Microsoft\Edge\Application\msedge.exe"),
        ];
        let expected = candidates[2].clone();
        assert_eq!(
            select_edge_candidate(&candidates, |path| path != candidates[1]),
            Some(expected)
        );
    }

    #[test]
    fn absent_candidates_do_not_fall_back_to_path() {
        assert!(select_edge_candidate(&edge_candidates(), |_| false).is_none());
    }

    #[test]
    fn launcher_command_has_only_the_open_argument() {
        let executable = Path::new(r"D:\tools\History Box\history-box.exe");
        let mut command = hidden_command(executable);
        command.arg("open");

        assert_eq!(command.get_program(), executable.as_os_str());
        assert_eq!(command.get_args().collect::<Vec<_>>(), ["open"]);
    }
}
