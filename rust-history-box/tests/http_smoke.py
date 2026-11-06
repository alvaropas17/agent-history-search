"""Bounded HTTP smoke test with synthetic data and a child-owned server."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:47614"

with tempfile.TemporaryDirectory(prefix="history-box-test-") as folder:
    folder = Path(folder)
    db = folder / "fixture.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE docs(id INTEGER PRIMARY KEY,host TEXT,sess TEXT,role TEXT,ts INTEGER,text TEXT);
            CREATE TABLE titles(host TEXT,sess TEXT,title TEXT,PRIMARY KEY(host,sess));
            CREATE VIRTUAL TABLE docs_fts USING fts5(text,content='docs',content_rowid='id',tokenize='trigram case_sensitive 0');
            INSERT INTO docs VALUES(1,'claude','fixture-session','assistant',1793832621000,'Texto de prueba: atajo Ctrl+Alt+H y café 🚀.');
            INSERT INTO titles VALUES('claude','fixture-session','Fixture title');
            INSERT INTO docs_fts(docs_fts) VALUES('rebuild');
        """)
    conn.close()
    env = dict(os.environ, HISTORY_INDEX=str(db), HISTORY_BOX_CACHE=str(folder / "cache"))
    executable = os.environ.get("HISTORY_BOX_TEST_EXE", str(ROOT / "target/debug/history-box.exe"))
    child = subprocess.Popen([executable, "serve", "--no-refresh"], env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, token=None, host=None, method="GET"):
        headers = {}
        if token is not None:
            headers["X-Token"] = token
        if host:
            headers["Host"] = host
        req = urllib.request.Request(BASE + path, headers=headers, method=method)
        try:
            with opener.open(req, timeout=3) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    try:
        deadline = time.monotonic() + 5
        token_path = folder / "cache/history-box-rust.token"
        while not token_path.exists():
            if child.poll() is not None:
                raise AssertionError("Owned test server could not bind; stop the test without touching other processes")
            if time.monotonic() > deadline:
                raise AssertionError("Startup timeout")
            time.sleep(0.05)
        token = token_path.read_text()
        assert request("/api/ping")[0] == 403
        assert request("/api/ping", "invalid")[0] == 403
        assert request("/api/ping", token, "evil.example")[0] == 403
        assert request("/api/ping", token, method="POST")[0] == 403
        assert request("/api/ping", token)[0] == 200
        code, page, headers = request("/?t=" + token)
        assert code == 200 and b"Buscar en el historial" in page
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert headers["Cache-Control"] == "no-store"
        code, body, _ = request("/api/search?q=atajo&host=claude", token)
        assert code == 200, body
        result = json.loads(body)["results"][0]
        assert result["title"] == "Fixture title"
        from urllib.parse import urlencode
        code, body, _ = request("/api/expand?" + urlencode({"ref": result["ref"], "context": 1500}), token)
        assert code == 200 and "café 🚀" in json.loads(body)["text"], body
        assert request("/api/expand?ref=bad", token)[0] == 400
        assert request("/api/expand?context=nope", token)[0] == 400
        assert request("/api/search?q=atajo&host=unknown", token)[0] == 400
        assert request("/api/unknown", token)[0] == 404
        assert request("/api/expand?context=6001&ref=bad", token)[0] == 400
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", 47614, timeout=3)
        connection.request("GET", "http://example.invalid/", headers={"Host": "127.0.0.1:47614"})
        assert connection.getresponse().status in (400, 403)
        connection.close()
        assert request("/api/ping", token)[0] == 200
        print("HTTP smoke passed: authentication, Host/method checks, headers, search/expand, bounds, and malformed-request survival. Synthetic fixture only; no GUI or real history used.")
    finally:
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)
