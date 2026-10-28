# Preparing a local distribution

There are no external Python dependencies and nothing to install. The ZIP holds one folder, `agent-history-search/`, with the CLI, the package, documentation, the skill and the tests. Unzipping it and running `history_search.py` is enough.

## Building

~~~sh
python -B tools/build_distribution.py
~~~

The builder uses a closed list of files; it does not pack whatever happens to be in the repository. Every listed file must exist and must not be a symlink. It writes `dist/agent-history-search-0.3.0.zip` and its `.sha256` in exclusive mode: if they already exist it stops and never overwrites or deletes the previous version. Use `--output /path/new.zip` for another destination. Names inside the archive are relative with no path traversal, and `MANIFEST.json` lists the SHA-256 of every file.

`python -B tools/verify_distribution.py ZIP` checks an archive against its manifest and checksum.

The build runs no history queries and uses no network. `tools/inspect_schema.py` and `tools/snapshot_scaffold.py` are maintainer tools and are not in the ZIP. `tools/sqlite_snapshot.py` is included: the owner runs it to copy a live SQLite database into a snapshot without a `-wal` file. The tests only generate temporary synthetic fixtures.

## AI-guided setup and Pocket

The ZIP includes `docs/SETUP-PROMPT.md`, to set the helper up from any compatible agent, and `docs/POCKET.md`, to use it from Pocket or plan an optional native wrapper later. It includes no histories, no one's authorised paths and no ready-to-enable Pocket extension. Paths and permissions are agreed during local setup. No service is required.

## Before sending it to someone else

1. Keep `LICENSE` with its `Copyright (c) 2026 alvaropas17` notice.
2. Run the tests on the target OS and Python version.
3. Review the ZIP contents and its manifest; never add snapshots, config or private results.
4. Confirm that no schema other than the documented ones is expected to work.
5. Publish only when you decide to. Building the ZIP does not publish a release or create a remote repository.
