# Provenance, license and scope audit

## Independent implementation

The readers in this package are new, small and self-contained. They were written from the documented session formats of each agent and from schema metadata (table and column names). No existing reader, router or Pocket extension was copied, and no third-party code is vendored. Table names, field names and interoperable formats are kept because compatibility requires them, not because an implementation was copied.

Pi Pocket is itself MIT-licensed by its authors. Nothing from its extension is included, and its license is not applied to other files here by proximity. If third-party code is ever added, audit it and add its notices first.

The search window (`agent_history/index.py`, `window.py`, `hotkey.py`) was adapted from the author's own local scripts, not from third-party code. It reuses this package's text helpers. Its record parsing is separate from the CLI readers, because it reads incrementally instead of under a per-query budget.

## License

The new code and documentation are released under the MIT license, `Copyright (c) 2026 alvaropas17`, as stated in [LICENSE](../LICENSE). This does not relicense anyone else's code. Preparing a distribution ZIP is not a release: nothing is published or pushed by the tooling in this repository.

## Data touched while building it

The only direct inspection of real history stores was a read-only `PRAGMA table_info` on the known OpenCode and Pocket tables, printing column names and types only. No real conversations were dumped, no databases were copied, and test fixtures are synthetic.

## Controls and tests

Parameter validation, exact ID equality, parameterised SQL for values and constant SQL identifiers, one worker process per query without a shell, a timeout, and input/output budgets. SQLite is opened `immutable`, which avoids auxiliary writes, and a database with a `-wal` file is refused so recent data is not silently hidden. Snapshots must be consistent and owned by the user. The CLI has no authentication, network access, installation or permission change.

The optional search window differs. It listens on 127.0.0.1 with a per-start token and a `Host` check, and it writes a derived index containing conversation text to the user cache folder. It opens live WAL databases read-only, but only when `-shm` already exists. `--install-startup` adds one per-user Run registry value. Its demo mode and tests use invented data only. Its image in `docs/images` is a capture of demo mode.

The offline tests generate their own databases and JSONL files, compare hashes, check that writes are rejected, and cover explicit sources, branches, invalid formats, limits and the real worker. They do not exercise every concurrency state and they do not prove that an agent will load the skill automatically. Current OWASP guidance was not checked online; known principles were applied, with no claim of certification.

## Validation limits

Tested with Python 3.14 on Windows. The code has no external dependencies and uses `pathlib`, but macOS, Linux and Python 3.10 need the test suite run there before being claimed as validated. No end-to-end CLI run was done against personal conversations or inside every agent. The search window index was built once from the author's local stores to compare message counts and timings; only counts were printed, and that index was deleted. The macOS and Linux paths of the search window have not been run. `--install-startup` is tested against a fake registry only. OpenCode support covers one verified schema, not every product or version called OpenCode.
