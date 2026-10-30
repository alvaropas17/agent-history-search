# Changelog

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
