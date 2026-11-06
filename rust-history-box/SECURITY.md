# Local application security boundary

The application serves private conversation excerpts. It listens on IPv4 loopback
only and requires a per-start token plus an exact Host header. It does not enable
CORS, accept mutations, or execute commands supplied by HTTP clients. Browser text
is rendered with DOM text nodes rather than history-derived HTML.

SQLite search connections are read-only. Query parameters are bound, and FTS terms
are quoted as literal phrases. The Python adapter alone refreshes the existing
derived index. It retains the existing reader's coverage limitations; stored order
does not prove the active branch of a conversation.

The Edge executable receives the locally generated app URL as a single argument,
without a command shell. The refresh program is embedded application code, not
HTTP input. Direct Python execution avoids an extra `py` launcher process. Child
cleanup is bounded, but this implementation does not sandbox Python or constrain
arbitrary descendants with a Windows Job Object.

The token is stored under the current Windows user profile and uses inherited ACLs.
It is not a security boundary against another process running as that user. The
launcher checks the expected ping response but does not cryptographically identify
a service that maliciously occupies its local port. Do not expose this server on a
LAN, forward its port, publish its token, or use it as a multi-user service.

The launcher uses the existing Edge profile, as the Python version does. Window
reuse currently checks the exact visible title; it does not cryptographically prove
window ownership. Interactive focus and Windows reboot behavior require separate
desktop verification.

Reference consulted during implementation: OWASP OS Command Injection Defense
Cheat Sheet, https://cheatsheetseries.owasp.org/cheatsheets/OS_Command_Injection_Defense_Cheat_Sheet.html.
Controls applied: direct process APIs, fixed options, separate arguments, and bounded
input validation. These controls do not establish overall OWASP compliance.
