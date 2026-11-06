# Changelog

## Unreleased

### Added

- Local search window (`history_window.py`): filters all six agents while you type, using a separate derived FTS5 index that holds a copy of conversation text. The CLI stays index-free.
- Demo mode (`--demo`) with invented conversations, a temporary index and its own port.
- Windows global shortcut (`--hotkey`, `--install-startup`, `--uninstall-startup`).

### Reviewed

- Search-window service reuse now checks the complete source/cache/demo configuration and a challenge response. An unrelated listener never receives a health-probe token.
- Launch credentials use a URL fragment; history APIs require the token header. Added Origin/Fetch Metadata checks, duplicate-parameter limits and bounded socket reads.
- Frontend regression tests cover stale search/detail responses and safe copied resume commands.
- Hotkey cleanup handles Windows message-loop errors and releases only its own successful registration. Startup arguments are parsed before persistence.
- No existing hotkey, registry value, startup item or private history was changed during this review.

## 0.4.0 — release candidate

### Changed

- Portable helper default searches return the full literal match plus up to 100 source characters on each side. Explicit context retains the previous total-character behavior. Unicode case-folded boundaries map back to the original source, including expanding characters such as ß.
- Native Pocket already had 120 characters per side; this release does not introduce that behavior.
- Added literal OR query variants with one shared result limit and bounded streaming text reads for Codex and Claude Code.
- Updated English documentation and the connector-first skill route for exposed Codex/ChatGPT tools. The CLI still reads local histories only.
- Added reusable evidence rules: quote full commands with available surrounding source text and distinguish proposed, executed and confirmed actions.

### Added

- Optional dependency-free stdio MCP wrapper with fixed host, authorized stores, source-change rules, protocol negotiation and LF-only responses for strict clients.
- Regression tests for literal context, Unicode boundaries, query variants, streaming and MCP. The distribution includes these tests and this changelog.

### Maintenance

- Package version is defined in `agent_history/__init__.py`; MCP server information and distribution metadata read it. Distributed skill metadata is checked against it.
- No personal instructions, private histories or unrelated external omp helper are included.

## 0.3.0

- Added opt-in stored tool-call and result inspection and omp title records.
- This section records repository history; no prior GitHub release or tag was found during release preparation.
