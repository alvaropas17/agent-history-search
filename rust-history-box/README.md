# History Box — Rust desktop shell

A Windows port of the local History Box search application. This project is separate
from the unrelated `feat/mcp-server` pull request. The Python installation and its
Windows startup entry are not replaced by building this project.

## Scope

Rust implements the global Ctrl+Alt+H hotkey, Edge app launcher, HTTP server,
authentication, SQLite searches, relevance ranking and reference expansion. The
HTML interface is reused from the existing application.

**This is not an entirely Python-free port.** Index refresh still runs the existing
shared Python `history_index` module through `refresh.py`. The original conversation
stores stay read-only. Only the shared derived index is updated. Index refresh has
a 15-second timeout and runs in a worker, at most once every 30 seconds, including
failed attempts. Searches can use the previous index while refresh runs. The UI
shows the refresh status; it must not present the previous index as verified current.

## Build and run

Rust 1.85 or later and a Windows C/C++ build toolchain are required. Cargo downloads the dependencies
listed in Cargo.lock. Index refresh requires a direct Python interpreter on PATH
and the existing shared history scripts under
`%USERPROFILE%/.agents/skills/shared-scripts/scripts`. Windows Store aliases are skipped.
Set `HISTORY_BOX_PYTHON` to an absolute Python executable path to select one explicitly.

```text
cargo test
cargo build --release
target/release/history-box.exe open
target/release/history-box.exe hotkey
```

`open` opens Edge in app mode. `hotkey` remains resident and registers Ctrl+Alt+H.
It cannot register while the old Python hotkey process owns that key combination.
No startup entry is created automatically. Do not run both hotkey implementations.

The Rust server listens only on `127.0.0.1:47614`, separate from the Python server's
47613. A fresh random token is written to
`%USERPROFILE%/.agents/cache/history-box-rust.token` after binding the port. The
server rejects requests with an incorrect Host or token, disallows non-GET methods,
and stops after 30 minutes without authenticated requests. Any process running as
the same Windows user can read the token; it is not a boundary against that user.
The app uses the existing Edge profile, as the Python application does.

For read-only diagnostics, `search QUERY [HOST]` searches the existing index without
refreshing it. `serve --no-refresh` is intended for isolated fixture tests. Set
`HISTORY_INDEX` to a fixture database and `HISTORY_BOX_CACHE` to a temporary cache
to avoid reading the real index or changing the installed application's token during tests.

The release build has no console. Errors are also recorded in
`%USERPROFILE%/.agents/cache/history-box-rust.log`. The refresh adapter is embedded
in the executable, but the shared Python indexer remains an external dependency.

## Verification

`tests/http_smoke.py` runs the compiled debug binary against a temporary synthetic
SQLite database. It does not launch Edge, register a hotkey or read real histories.
The test terminates only the child process that it created. Offline tests do not
prove interactive window focus, startup after a reboot, or real-world performance.

The Python startup shortcut is intentionally unchanged until the Rust version has
passed interactive checks. Retain the original shortcut as a backup before switching.
