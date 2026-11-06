#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod search;
#[cfg(windows)]
mod windows;

use serde_json::{Value, json};
use std::{collections::HashMap, fs, path::PathBuf, process::{Child, Command, Stdio}, sync::mpsc::{self, Receiver, TryRecvError}, time::{Duration, Instant}};
use tiny_http::{Header, Method, Response, Server};

const ADDRESS: &str = "127.0.0.1:47614";
const PAGE: &str = include_str!("../assets/index.html");
fn cache() -> PathBuf {
    if let Some(path) = std::env::var_os("HISTORY_BOX_CACHE") { return PathBuf::from(path); }
    PathBuf::from(std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")).unwrap_or_default()).join(".agents/cache")
}
fn index_path() -> PathBuf {
    std::env::var_os("HISTORY_INDEX").map(PathBuf::from).unwrap_or_else(|| cache().join("history-index-v2.sqlite"))
}
fn token_path() -> PathBuf { cache().join("history-box-rust.token") }
fn token() -> Result<String,String> {
    let mut bytes = [0u8;32];
    getrandom::fill(&mut bytes).map_err(|e| e.to_string())?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}
fn constant_eq(a: &str,b: &str) -> bool {
    if a.len()!=b.len() {return false;}
    a.bytes().zip(b.bytes()).fold(0u8, |d,(a,b)| d|(a^b))==0
}
fn header(name: &str,value: &str) -> Header { Header::from_bytes(name,value).unwrap() }
fn send(request: tiny_http::Request, code: u16, body: String, html: bool) {
    let response=Response::from_string(body).with_status_code(code)
        .with_header(header("Content-Type",if html {"text/html; charset=utf-8"} else {"application/json; charset=utf-8"}))
        .with_header(header("Cache-Control","no-store"))
        .with_header(header("X-Content-Type-Options","nosniff"))
        .with_header(header("Referrer-Policy","no-referrer"))
        .with_header(header("Content-Security-Policy","default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"));
    let _=request.respond(response);
}
struct RefreshOutcome {
    result: Result<(), String>,
    retry_allowed: bool,
}

// Resolve a direct interpreter. Do not start the Windows py launcher, whose
// interpreter child can survive termination of the launcher.
fn python_executable() -> Result<PathBuf, String> {
    if let Some(path) = std::env::var_os("HISTORY_BOX_PYTHON") {
        let path = PathBuf::from(path);
        if path.is_absolute() && path.is_file() { return Ok(path); }
        return Err("HISTORY_BOX_PYTHON must name an absolute Python executable".into());
    }
    let path = std::env::var_os("PATH").unwrap_or_default();
    for directory in std::env::split_paths(&path) {
        if !directory.is_absolute() { continue; }
        // App execution aliases can open the Store instead of an interpreter.
        if directory.components().any(|part| part.as_os_str().to_string_lossy().eq_ignore_ascii_case("WindowsApps")) { continue; }
        let candidate = directory.join(if cfg!(windows) { "python.exe" } else { "python3" });
        if candidate.is_file() { return Ok(candidate); }
    }
    Err("No direct Python interpreter found; set HISTORY_BOX_PYTHON".into())
}

fn terminate_child(child: &mut Child) -> bool {
    let _ = child.kill();
    let deadline = Instant::now() + Duration::from_secs(1);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return true,
            Err(_) => return false,
            Ok(None) => {}
        }
        if Instant::now() >= deadline { return false; }
        std::thread::sleep(Duration::from_millis(10));
    }
}

fn refresh_index() -> RefreshOutcome {
    let execute = || -> Result<Child, String> {
        let mut command = Command::new(python_executable()?);
        command.args(["-B", "-c", include_str!("../refresh.py")])
            .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x08000000); }
        command.spawn().map_err(|e| e.to_string())
    };
    let mut child = match execute() {
        Ok(child) => child,
        Err(error) => return RefreshOutcome { result: Err(error), retry_allowed: true },
    };
    let deadline = Instant::now() + Duration::from_secs(15);
    let error = loop {
        match child.try_wait() {
            Ok(Some(status)) => return RefreshOutcome {
                result: if status.success() { Ok(()) } else { Err("Index refresh failed".into()) },
                retry_allowed: true,
            },
            Err(error) => break format!("Cannot check index refresh: {error}"),
            Ok(None) => {}
        }
        if Instant::now() >= deadline { break "Index refresh timed out".into(); }
        std::thread::sleep(Duration::from_millis(50));
    };
    let terminated = terminate_child(&mut child);
    RefreshOutcome {
        result: Err(if terminated { error } else { format!("{error}; child termination is unconfirmed; further refreshes are disabled") }),
        retry_allowed: terminated,
    }
}

struct IndexUpdate {
    receiver: Option<Receiver<RefreshOutcome>>,
    last_attempt: Option<Instant>,
    status: &'static str,
    message: String,
    enabled: bool,
    retry_allowed: bool,
}

impl IndexUpdate {
    fn new(enabled: bool) -> Self {
        Self {
            receiver: None, last_attempt: None, status: if enabled { "error" } else { "disabled" }, enabled, retry_allowed: true,
            message: if enabled { "Todavía no se ha completado una actualización. No se ha verificado la vigencia del índice." } else { "La actualización automática está desactivada. No se ha verificado la vigencia del índice." }.into(),
        }
    }
    fn poll(&mut self) {
        let Some(receiver) = &self.receiver else { return; };
        let outcome = match receiver.try_recv() {
            Ok(outcome) => outcome,
            Err(TryRecvError::Empty) => return,
            Err(TryRecvError::Disconnected) => RefreshOutcome {
                result: Err("Index refresh worker stopped unexpectedly; further refreshes are disabled".into()),
                retry_allowed: false,
            },
        };
        if let Err(error) = &outcome.result { log_error(error); }
        self.complete(outcome);
    }
    fn complete(&mut self, outcome: RefreshOutcome) {
        self.receiver = None;
        self.retry_allowed = outcome.retry_allowed;
        match outcome.result {
            Ok(()) => {
                self.status = "ready";
                self.message = "La última actualización terminó. Los resultados usan el índice derivado y pueden no incluir cambios posteriores.".into();
            }
            Err(_) => {
                self.status = "error";
                self.message = "La actualización no terminó. Los resultados pueden usar el índice derivado anterior; no se ha verificado su vigencia.".into();
                if !self.retry_allowed { self.message.push_str(" Las actualizaciones quedan suspendidas hasta reiniciar el servidor."); }
            }
        }
    }
    fn start_if_due(&mut self) {
        self.poll();
        if !self.enabled || !self.retry_allowed || self.receiver.is_some()
            || self.last_attempt.is_some_and(|time| time.elapsed() < Duration::from_secs(30)) { return; }
        self.last_attempt = Some(Instant::now());
        let (sender, receiver) = mpsc::channel();
        match std::thread::Builder::new().name("index-refresh".into()).spawn(move || {
            let _ = sender.send(refresh_index());
        }) {
            Ok(_) => {
                self.receiver = Some(receiver);
                self.status = "running";
                self.message = "Actualizando el índice. Los resultados pueden usar el índice derivado anterior; todavía no se ha verificado su vigencia.".into();
            }
            Err(error) => {
                log_error(&format!("Cannot start index refresh worker: {error}"));
                self.status = "error";
                self.message = "No se pudo iniciar la actualización. Los resultados pueden usar el índice derivado anterior; no se ha verificado su vigencia.".into();
            }
        }
    }
    fn json(&self) -> Value {
        json!({"status": self.status, "message": self.message})
    }
}

fn request_url(target: &str) -> Result<url::Url, String> {
    if target.len() > 8192 || !target.starts_with('/') || target.starts_with("//")
        || target.bytes().any(|byte| byte.is_ascii_control() || byte == b' ' || byte == b'\\' || byte == b'#') {
        return Err("Destino de solicitud no válido".into());
    }
    url::Url::parse(&format!("http://{ADDRESS}{target}")).map_err(|_| "Destino de solicitud no válido".into())
}

fn validate_search_input(query: &str, host: &str) -> Result<(), &'static str> {
    if !["all", "pi", "omp", "pocket", "opencode", "claude", "codex"].contains(&host) {
        return Err("Origen de historial no válido.");
    }
    if query.chars().count() > 1600 {
        return Err("La consulta no puede superar los 1600 caracteres.");
    }
    let mut has_search_term = false;
    for word in query.split_whitespace() {
        let length = word.chars().count();
        if length > 200 {
            return Err("Cada término de la consulta debe tener como máximo 200 caracteres.");
        }
        if length >= 3 {
            has_search_term = true;
            // Match downstream Unicode lowercase expansion, not byte length.
            if word.to_lowercase().chars().count() > 200 {
                return Err("Cada término normalizado debe tener como máximo 200 caracteres.");
            }
        }
    }
    // The search engine uses the complete trimmed query as a fallback when all
    // whitespace-separated terms contain fewer than 3 Unicode scalar values.
    if !has_search_term && query.trim().chars().count() > 200 {
        return Err("La consulta de términos cortos no puede superar los 200 caracteres.");
    }
    Ok(())
}

fn expand_bounds(context: &str, offset: Option<&str>) -> Result<(usize, Option<usize>), String> {
    let context = context.parse::<usize>().map_err(|_| "Invalid context")?;
    let offset = offset.map(str::parse::<usize>).transpose().map_err(|_| "Invalid offset")?;
    if !(1..=6000).contains(&context) || offset.is_some_and(|value| value > i32::MAX as usize) {
        return Err("Context must be 1..6000 and offset must not exceed 2147483647".into());
    }
    Ok((context, offset))
}
fn serve(no_refresh: bool) -> Result<(),String> {
    // A separate port and token leave the installed Python application untouched.
    let server=Server::http(ADDRESS).map_err(|e| e.to_string())?;
    let secret=token()?;
    fs::create_dir_all(cache()).map_err(|e|e.to_string())?;
    fs::write(token_path(),&secret).map_err(|e|e.to_string())?;
    let mut last_request=Instant::now();
    let mut index_update = IndexUpdate::new(!no_refresh);
    loop {
        index_update.poll();
        if last_request.elapsed()>Duration::from_secs(1800) && index_update.receiver.is_none(){break;}
        let Some(request)=server.recv_timeout(Duration::from_secs(1)).map_err(|e|e.to_string())? else {continue};
        let host=request.headers().iter().find(|h|h.field.equiv("Host")).map(|h|h.value.as_str());
        if request.method()!=&Method::Get || host!=Some(ADDRESS) || request.url().len()>8192 {
            send(request,403,json!({"error":"forbidden"}).to_string(),false);continue;
        }
        let url = match request_url(request.url()) {
            Ok(url) => url,
            Err(error) => { send(request,400,json!({"error":error}).to_string(),false); continue; }
        };
        let params: HashMap<String,String>=url.query_pairs().into_owned().collect();
        let given=request.headers().iter().find(|h|h.field.equiv("X-Token")).map(|h|h.value.as_str()).or_else(||params.get("t").map(String::as_str)).unwrap_or("");
        if !constant_eq(given,&secret) {send(request,403,json!({"error":"forbidden"}).to_string(),false);continue;}
        last_request=Instant::now();
        if url.path()=="/" {send(request,200,PAGE.to_owned(),true);continue;}
        let get=|name: &str,default: &str|params.get(name).cloned().unwrap_or_else(||default.into());
        let result: Result<Value,String>=match url.path() {
            "/api/ping"=>Ok(json!({"ok":true,"implementation":"rust", "index_update":index_update.json()})),
            "/api/search"=> {
                let query = get("q", "");
                let history_host = get("host", "all");
                if let Err(error) = validate_search_input(&query, &history_host) {
                    send(request,400,json!({"error":error, "index_update":index_update.json()}).to_string(),false);
                    continue;
                }
                index_update.start_if_due();
                match search::search(&index_path(), &query, &history_host) {
                    Ok(mut body) => {
                        body["index_update"] = index_update.json();
                        Ok(body)
                    }
                    Err(_) => {
                        send(request,503,json!({"error":"El índice derivado no está disponible.", "index_update":index_update.json()}).to_string(),false);
                        continue;
                    }
                }
            },
            "/api/expand"=> {
                match expand_bounds(&get("context","1500"), params.get("offset").map(String::as_str)) {
                    Ok((context,offset))=>search::expand(&index_path(),&get("ref",""),context,offset),
                    Err(error)=>Err(error)
                }
            },
            _=>{send(request,404,json!({"error":"not found"}).to_string(),false);continue;}
        };
        match result {Ok(body)=>send(request,200,body.to_string(),false),Err(_)=>send(request,400,json!({"error":"Solicitud no válida o índice no disponible"}).to_string(),false)}
    }
    // Leave the inert token file in place. Never delete another process's state.
    Ok(())
}
const MAX_PING_RESPONSE: usize = 8192;
const MAX_PING_HEADERS: usize = 4096;

// None means that another TCP read is necessary. Reject ambiguous framing.
fn parse_ping_response(response: &[u8]) -> Result<Option<bool>, ()> {
    if response.len() > MAX_PING_RESPONSE { return Err(()); }
    let Some(end) = response.windows(4).position(|part| part == b"\r\n\r\n") else {
        return if response.len() > MAX_PING_HEADERS { Err(()) } else { Ok(None) };
    };
    let body_start = end + 4;
    if body_start > MAX_PING_HEADERS { return Err(()); }
    let headers = std::str::from_utf8(&response[..end]).map_err(|_| ())?;
    let mut lines = headers.split("\r\n");
    let mut status = lines.next().ok_or(())?.split_whitespace();
    if !matches!(status.next(), Some("HTTP/1.0" | "HTTP/1.1")) || status.next() != Some("200") { return Err(()); }
    let mut length = None;
    for line in lines {
        let (name, value) = line.split_once(':').ok_or(())?;
        if name.eq_ignore_ascii_case("Transfer-Encoding") { return Err(()); }
        if name.eq_ignore_ascii_case("Content-Length") {
            if length.is_some() { return Err(()); }
            let value = value.trim();
            if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) { return Err(()); }
            length = Some(value.parse::<usize>().map_err(|_| ())?);
        }
    }
    let length = length.ok_or(())?;
    if length > MAX_PING_RESPONSE - body_start { return Err(()); }
    let expected = body_start + length;
    if response.len() < expected { return Ok(None); }
    if response.len() != expected { return Err(()); }
    let value: Value = serde_json::from_slice(&response[body_start..expected]).map_err(|_| ())?;
    Ok(Some(value.get("ok").and_then(Value::as_bool) == Some(true)
        && value.get("implementation").and_then(Value::as_str) == Some("rust")))
}

fn ping(secret: &str) -> bool {
    use std::{io::{Read,Write,ErrorKind},net::{TcpStream,SocketAddr}};
    if secret.len() != 64 || !secret.bytes().all(|byte| byte.is_ascii_hexdigit()) { return false; }
    let deadline = Instant::now() + Duration::from_millis(750);
    let address: SocketAddr=ADDRESS.parse().unwrap();
    let Ok(mut stream)=TcpStream::connect_timeout(&address,Duration::from_millis(250)) else{return false};
    let request = format!("GET /api/ping HTTP/1.0\r\nHost: {ADDRESS}\r\nX-Token: {secret}\r\nConnection: close\r\n\r\n");
    let mut pending = request.as_bytes();
    while !pending.is_empty() {
        let Some(remaining) = deadline.checked_duration_since(Instant::now()).filter(|time| !time.is_zero()) else { return false; };
        if stream.set_write_timeout(Some(remaining)).is_err() { return false; }
        match stream.write(pending) {
            Ok(0) => return false,
            Ok(count) => pending = &pending[count..],
            Err(error) if error.kind() == ErrorKind::Interrupted => continue,
            Err(_) => return false,
        }
    }
    let mut response = Vec::new();
    loop {
        match parse_ping_response(&response) {
            Ok(Some(valid)) => return valid,
            Err(()) => return false,
            Ok(None) => {}
        }
        let Some(remaining) = deadline.checked_duration_since(Instant::now()).filter(|time| !time.is_zero()) else { return false; };
        if response.len() == MAX_PING_RESPONSE || stream.set_read_timeout(Some(remaining)).is_err() { return false; }
        let mut chunk = [0u8; 1024];
        let limit = chunk.len().min(MAX_PING_RESPONSE - response.len());
        match stream.read(&mut chunk[..limit]) {
            Ok(0) => return false,
            Ok(count) => response.extend_from_slice(&chunk[..count]),
            Err(error) if error.kind() == ErrorKind::Interrupted => continue,
            Err(_) => return false,
        }
    }
}
fn open() -> Result<(),String> {
    let mut secret=fs::read_to_string(token_path()).unwrap_or_default();
    if !ping(&secret) {
        let mut command=Command::new(std::env::current_exe().map_err(|e|e.to_string())?);
        command.arg("serve").stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        #[cfg(windows)] {use std::os::windows::process::CommandExt;command.creation_flags(0x08000000);}
        command.spawn().map_err(|e|e.to_string())?;
        let start=Instant::now();
        loop {
            secret=fs::read_to_string(token_path()).unwrap_or_default();
            if ping(&secret){break;}
            if start.elapsed()>Duration::from_secs(6){return Err("Server did not become ready".into());}
            std::thread::sleep(Duration::from_millis(80));
        }
    }
    #[cfg(windows)] {windows::open_app(&format!("http://{ADDRESS}/?t={secret}"))?;}
    Ok(())
}
fn run() -> Result<(),String> {
    let args: Vec<String>=std::env::args().skip(1).collect();
    match args.first().map(String::as_str).unwrap_or("open") {
        "serve"=>serve(args.iter().any(|a|a=="--no-refresh")),
        "open"=>open(),
        #[cfg(windows)] "hotkey"=>windows::run_hotkey(),
        "search"=> {println!("{}",search::search(&index_path(),args.get(1).ok_or("Missing query")?,args.get(2).map(String::as_str).unwrap_or("all"))?);Ok(())},
        _=>Err("Usage: history-box [open|hotkey|serve [--no-refresh]|search QUERY [HOST]]".into())
    }
}
fn log_error(error: &str) {
    use std::io::Write;
    let _ = fs::create_dir_all(cache());
    if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(cache().join("history-box-rust.log")) {
        let _ = writeln!(file, "History Box: {error}");
    }
}
fn main(){if let Err(error)=run(){eprintln!("History Box: {error}");log_error(&error);std::process::exit(1);}}

#[cfg(test)] mod tests {
    use super::*;
    #[test] fn token_is_random_hex(){let a=token().unwrap();assert_eq!(a.len(),64);assert!(a.bytes().all(|b|b.is_ascii_hexdigit()));assert_ne!(a,token().unwrap());}
    #[test] fn auth_comparison(){assert!(constant_eq("abc","abc"));assert!(!constant_eq("abc","abd"));assert!(!constant_eq("abc",""));}

    #[test]
    fn request_targets_are_origin_form_only() {
        for target in ["*", "http://example.invalid/", "//example.invalid/", "/\\\\example.invalid/", "/a#fragment", "/a\r\nb"] {
            assert!(request_url(target).is_err(), "{target:?}");
        }
        assert_eq!(request_url("/api/ping?q=test").unwrap().path(), "/api/ping");
        assert!(request_url(&format!("/{}", "a".repeat(8192))).is_err());
        // A rejected target must not affect the next request.
        assert!(request_url("*").is_err());
        assert!(request_url("/").is_ok());
    }

    #[test]
    fn search_http_validation_precedes_index_access() {
        for host in ["all", "pi", "omp", "pocket", "opencode", "claude", "codex"] {
            assert!(validate_search_input("needle", host).is_ok());
        }
        assert!(validate_search_input("needle", "unknown").is_err());
        assert!(validate_search_input(&" ".repeat(1600), "all").is_ok());
        assert!(validate_search_input(&" ".repeat(1601), "all").is_err());
        assert!(validate_search_input(&"é".repeat(200), "all").is_ok());
        assert!(validate_search_input(&"é".repeat(201), "all").is_err());
        assert!(validate_search_input(&format!("{}{}", "word ".repeat(8), "x".repeat(201)), "all").is_err());
        assert!(validate_search_input(&"İ".repeat(101), "all").is_err());
        assert!(validate_search_input(&"ab ".repeat(100), "all").is_err());
        assert!(validate_search_input("", "all").is_ok());
    }

    #[test]
    fn numeric_http_bounds() {
        assert_eq!(expand_bounds("1", None).unwrap(), (1, None));
        assert_eq!(expand_bounds("6000", Some("2147483647")).unwrap(), (6000, Some(2147483647)));
        assert_eq!(expand_bounds("1500", Some("0")).unwrap(), (1500, Some(0)));
        for context in ["0", "6001", "-1", "invalid", "184467440737095516160"] {
            assert!(expand_bounds(context, None).is_err());
        }
        for offset in ["2147483648", "-1", "invalid", "184467440737095516160"] {
            assert!(expand_bounds("1", Some(offset)).is_err());
        }
    }

    fn ping_fixture(body: &str) -> Vec<u8> {
        format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{body}", body.len()).into_bytes()
    }

    #[test]
    fn ping_accepts_every_partial_response_split() {
        let response = ping_fixture(r#"{"ok":true,"implementation":"rust"}"#);
        for end in 0..response.len() {
            assert_eq!(parse_ping_response(&response[..end]), Ok(None), "split {end}");
        }
        assert_eq!(parse_ping_response(&response), Ok(Some(true)));
    }

    #[test]
    fn ping_rejects_foreign_and_malformed_responses() {
        assert_eq!(parse_ping_response(&ping_fixture(r#"{"ok":true,"implementation":"python"}"#)), Ok(Some(false)));
        assert_eq!(parse_ping_response(&ping_fixture(r#"{"ok":false,"implementation":"rust"}"#)), Ok(Some(false)));
        assert!(parse_ping_response(&ping_fixture("not JSON")).is_err());
        for response in [
            "HTTP/1.1 2000 OK\r\nContent-Length: 0\r\n\r\n",
            "HTTP/1.1 200 OK\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n",
            "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 0\r\n\r\n",
            "HTTP/1.1 200 OK\r\nContent-Length: 9999999999999999999999999\r\n\r\n",
        ] {
            assert!(parse_ping_response(response.as_bytes()).is_err());
        }
        assert!(parse_ping_response(&vec![b'a'; MAX_PING_HEADERS + 1]).is_err());
    }

    #[test]
    fn update_reports_failure_and_throttles_retry_without_spawning() {
        let mut update = IndexUpdate::new(true);
        update.last_attempt = Some(Instant::now());
        update.complete(RefreshOutcome { result: Err("fixture error".into()), retry_allowed: true });
        assert_eq!(update.json()["status"], "error");
        assert!(update.json()["message"].as_str().unwrap().contains("anterior"));
        update.start_if_due();
        assert!(update.receiver.is_none());
        assert_eq!(update.json()["status"], "error");
        update.complete(RefreshOutcome { result: Ok(()), retry_allowed: true });
        update.start_if_due();
        assert_eq!(update.json()["status"], "ready");
        assert!(update.receiver.is_none());
    }

    #[test]
    fn refresh_does_not_overlap_or_retry_an_unconfirmed_child() {
        let mut update = IndexUpdate::new(true);
        let (_sender, receiver) = mpsc::channel();
        update.receiver = Some(receiver);
        update.status = "running";
        update.start_if_due();
        assert!(update.last_attempt.is_none());
        assert_eq!(update.json()["status"], "running");
        update.complete(RefreshOutcome { result: Err("fixture cleanup error".into()), retry_allowed: false });
        update.start_if_due();
        assert!(update.receiver.is_none());
        assert_eq!(update.json()["status"], "error");
        assert!(update.json()["message"].as_str().unwrap().contains("suspendidas"));
    }

    #[test]
    fn disabled_refresh_has_an_explicit_status() {
        let mut update = IndexUpdate::new(false);
        update.start_if_due();
        assert_eq!(update.json()["status"], "disabled");
        assert!(update.receiver.is_none());
    }
}
