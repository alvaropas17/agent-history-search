# Using it from each agent

The integration is a CLI plus instructions/skill: it is not a plugin, an MCP server or a native tool, and there is no automatic installer. Before changing any configuration, read the documentation of the version you target and make an exclusive backup of every file you edit.

## One shared installation

Keep the package folder at a stable path of your choice and run its `history_search.py` by absolute path. The skill installation must keep `SKILL.md` together with its local reference `references/usage.md`; do not copy only the Markdown file and leave broken links. Record the absolute path in a local instruction (for example in `CLAUDE.md` or `AGENTS.md`), never credentials.

The folder name you expose must be `agent-history-search`, the same as the frontmatter `name`. Link to the package's `skill/` directory under that name, or register that specific path if the agent allows it. Do not expose a whole collection of other skills just to add this one.

## Pi

Pi loads skills from `~/.pi/agent/skills/` and `~/.agents/skills/`, plus project skills subject to trust. Link the package's `skill/` folder as `~/.pi/agent/skills/agent-history-search`. On Windows a junction avoids copying; on Unix use a symlink. Check that the destination does not already exist. Do not change trust settings or replace global instructions.

After setup, run `/reload` in Pi and check that the skill is discovered with `/skill:agent-history-search`. Verify the exact commands in your installed version before automating them. The skill runs the CLI through the agent's normal tools; it does not register `history_search` as a native tool.

Usual Pi store: `~/.pi/agent/sessions`. That is a format hint, not an automatic choice. If your agent directory is customised, use that sessions folder. For omp (Oh My Pi), pass `--host omp` and its own sessions folder; the reader accepts the same JSONL v2/v3 format. Never mix stores.

## OpenCode (oc2 / v2)

The format this reader supports is the **oc2/v2** database, not OpenCode v1. In the reference installation, oc2 reads the `skill`/`skills` folders of its config directory and specific paths from the `skills` array in `opencode.json`. That is evidence from one installation, not a universal promise.

If your version confirms the same contract, add only the stable path of the package's `skill/` folder to the existing array and keep every other value. This is an illustrative fragment, not a replacement for the whole file:

~~~json
{
  "skills": ["/stable/path/agent-history-search/skill"]
}
~~~

If your oc2 derives the skill identifier from the folder name instead of the frontmatter, first expose a link named `agent-history-search` and register that link. Never paste this block over an existing configuration. If the key does not exist, do not force a guessed format: check the schema of your version. After approval, restart the session and check discovery. There is no native integration and no new account access.

Reference store: `~/.local/share/opencode/opencode.db` with the `session_v2` and `session_message` tables. It may vary, so always pass `--store` explicitly. Only consistent SQLite snapshots without a `-wal` file are read, and not v1 or session-message-part databases.

## Pi Pocket

Pocket shares skill files with Pi. Its `docs/customizing.md` says it rereads them within about 30 seconds and that project skills need trust. Adding a skill does not require editing Pocket, enabling a drop-in or restarting the server.

If your Pocket already offers a `history_search` tool, use that first. This CLI must not replace it after an error, a timeout or an empty result, and you should not install a second tool with that name: the name is reserved in the version checked.

For independent offline use, pick a snapshot of the instance you are authorised to read, never someone else's database. `PI_POCKET_DIR` can change where the instance lives; this CLI does not read that variable to guess it. The native tool knows its instance and stays preferable for live work.

For a human-readable export, Pocket offers Menu → Export as Markdown and `/export`. This package does not automate authenticated access and does not parse that export as if its IDs were equivalent. It makes no claim that an official portable search API exists.

## Codex and Claude Code

Both use the same CLI, with no plugin or native tool. Expose the `skill/` folder as `agent-history-search` through the skills mechanism your installed version supports. Common locations are `~/.codex/skills` and `~/.claude/skills`; check discovery in a new session instead of assuming every version loads those paths.

Codex: `--host codex --store /path/.codex/sessions`. Claude Code: `--host claude --store /path/.claude/projects`. Use `list` to list, `search --query "topic"` to search and `read --session EXACT-ID --store FILE.jsonl` to read. Use the path returned by the listing so the CLI does not scan the whole profile. Archived files, extra profiles and nested subagents need explicit paths. Only user/assistant messages are read, with no tools or reasoning, and neither the active branch nor in-memory selection is verified. No cloud service is queried.

## Verifying without personal data

1. Run the offline test suite.
2. Use `--plan` with the host and store you approved; check the source and path.
3. Check skill discovery in a new or reloaded session.
4. Only with permission to read real history, run `list` with `--limit 1`, a short timeout and a consistent snapshot. Do not record private output in the repository.
5. Report results and limits. A model reading a skill once does not guarantee it will always use it.
