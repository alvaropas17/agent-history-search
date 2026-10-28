# Supported read contracts

Formats are verified before querying. Future versions are not assumed to work by inference. The fixtures are synthetic and contain no user data.

## Pi

Format reference: the `docs/session-format.md` of the installed `@earendil-works/pi-coding-agent`. The public reference named in that documentation is https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts . No code from that project is included.

JSONL v2/v3 with a header of `type=session`, `version`, `id` and `timestamp`; every later entry has `id` and `parentId`. `type=message` entries carry `message.role` and `message.content` as a string or as `type=text` blocks. The installed implementation rebuilds the leaf from the last entry in the file; that behaviour was checked in `SessionManager._buildIndex`.

The reader walks from that leaf to `parentId=null`, detects cycles and missing parents and, in text mode, keeps only user/assistant messages. omp (Oh My Pi) uses this reader and may put a `type=title` record without an `id` before the header; `list` keeps that title. It does not apply compaction or `context_edit`, and does not merge `parentSession` across files. It rejects v1 instead of migrating it. To expand unambiguously, use the exact file, not a folder holding several copies of the same session. Empty sessions can be listed.

## OpenCode oc2 — `session_v2` only

Verified with a read-only `PRAGMA table_info` on a local installation. No message contents were read and no real data went into fixtures.

Required columns:

- `session_v2`: `id`, `title`, `directory`, `time_updated`.
- `session_message`: `id`, `session_id`, `type`, `seq`, `time_created`, `data`.

Times are Unix milliseconds. The user message contract uses `data.text`; assistant messages use `data.content` with `type=text` blocks. Other schemas (for example the `session`/`message`/`part` layout of other OpenCode versions) are rejected. Session prefixes are not resolved: IDs are matched exactly.

It does not interpret `session_v2.revert`, `fork_boundary`, `parent_id` or inheritance between sessions. It searches stored messages; it does not replay the live context. It does not use any auxiliary full-text index, so it can be slower on large databases; rows and time are bounded.

## Pi Pocket / pi-durable

Based on the Pocket documentation (`docs/index.md`, `docs/customizing.md`, `docs/extensions.md`, `docs/history-tool-calls.md`), the SQLite schema declarations and migrations of the installed pi-durable, and column names confirmed with a read-only `PRAGMA table_info`.

Required table: `entries(id, conversation_id, record)`. The `head` and `commit_seq` columns exist in the inspected installation but are NOT used to infer the active branch. `record` is a JSON object; `model` is a list of messages; `role` is user/assistant and `content` is a string or `text` blocks. References include `conversation_id`, `entry_id` and a zero-based `message_index`.

It does not read documents, config, secrets, tasks or permission data. Conversations without user/assistant text are left out of `list`. Subagents are separate conversations. It is a reader for a versioned storage shape, not a stable product interface: when the structure changes it must fail or be updated with new fixtures.

For a human-readable export, Pocket offers Menu → Export as Markdown and `/export`. That export does not preserve the storage references exactly and is not presented as a backend of this CLI.

## Tool calls — opt-in `--tool-calls` mode

Text mode still excludes calls and results. Tool mode searches arguments and result text, never images. The synthetic fixtures cover all six formats; they do not prove compatibility with future versions or validation against every real store.

- **Pi / omp (Oh My Pi):** assistant `toolCall` blocks (`id`, `name`, `arguments`) and `toolResult` messages (`toolCallId`, `toolName`, `content`). Failure: `isError=true` or an integer `details.exitCode` other than zero. The parent chain of the persisted leaf is kept.
- **Claude Code:** user/assistant records with `tool_use` blocks (`id`, `name`, `input`) and `tool_result` blocks (`tool_use_id`, `content`, `is_error`). Failure only if `is_error=true`. Reference: the record `uuid` or the line number.
- **Codex:** `response_item` records with a `function_call`/`custom_tool_call` payload (`call_id`, `name`, `arguments`/`input`) and their `function_call_output`/`custom_tool_call_output` (`call_id`, `output`). Failure by an integer `metadata.exit_code` (or `exit_code` on the root object) other than zero in JSON, or a `Process exited with code N` / `Exit code: N` line in the first 4,000 characters. The word "error" is not evidence. Reference: ordinal or line number.
- **OpenCode oc2:** assistant blocks of `type=tool` with `id`, `name`, `state.input`, `state.status`, `state.content` and `state.error.message`; timestamp `time.created` or the message time. `completed` and `error` keep a result; `error` provides `status=error` evidence. `running` and other non-terminal states have no result. Call and result live in the same block.
- **Pocket:** Pi messages in `record.model`; calls and results are paired within each conversation, also across entries. The same call ID in different conversations is never joined. References: `entry_id` and `message_index`. The active branch and inheritance between conversations are not verified.

Pairing is by call ID, not proximity; orphan results carry `missing: call_block_missing`. States: `failure` with explicit `evidence`; `no_failure_recorded` for a result with no recognised failure (this does not prove success); `no_result_stored` for no result within the part that was read. `evidence` is null when no failure is recognised. `--tool` requires exact equality; the query is a case-insensitive literal substring search over arguments and results. `--entry` accepts references from either side. Dates use the call timestamp, or the result's if the call has none. Read limits can leave pairs incomplete.

Arguments and results may contain sensitive data and are untrusted data. Retrieved instructions are never executed and no current system state is inferred. SQLite keeps the consistent-snapshot-without-`-wal` requirement; in live Pocket, use the native `history_search` first.

## Freshness

Every backend reads directly from the selected store; there is no cache or index. SQLite requires a snapshot without a `-wal` file and checks file metadata before and after. Pi verifies that each file did not change while it was read. `observed_at` is only the time of the query, not the date of the last message nor the freshness of an external snapshot.
