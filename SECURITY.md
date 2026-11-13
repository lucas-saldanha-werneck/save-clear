# Security

## Reporting a vulnerability

Please report security problems privately, not in a public issue. Open the repository's
[Security tab](https://github.com/lucas-saldanha-werneck/save-clear/security) and choose
"Report a vulnerability" (GitHub private vulnerability reporting).

## Supported versions

Only the latest release is supported. Fixes land there.

## What this plugin accesses

- Files: it writes session summaries to `~/.claude/snapshots`; before a compaction the PreCompact hook also saves a gzipped copy of the full transcript there (owner-only, 10 kept). The skill updates the project's memory folder `~/.claude/projects/<project>/memory/`. State and logs go to the plugin data folder Claude Code gives it (else `~/.claude/state/save-clear`); options are read from `~/.config/save-clear/config.json` (created owner-only).
- Reads: session transcripts under `~/.claude/projects` (the idle trigger scans all of them), `~/.claude/jobs/<id>/state.json` for `claude --bg` sessions, the process list, and, for the desktop app relay, the app's `main.log` and its session files (`~/Library/Application Support/Claude/claude-code-sessions`).
- Keystrokes: it types `/clear` and the resume request into a tmux pane after checking that the pane runs the session that asked; the optional idle trigger types a save-clear request into idle tmux sessions; for a `claude --bg` session it attaches in a hidden tmux session and types there. It never types into iTerm2 or other apps directly.
- Desktop app: the optional relay creates a helper session with `claude -p` (default model haiku, in the caller's permission mode, which can be bypass), opens a `claude://resume` link, delivers messages to your other app sessions, and turns Remote Control off and back on for a session being cleared (and, only if you set `rc_idle_off_hours`, off for sessions idle that long).
- Commands: `notify_command`, `on_save_command`, `pre_clear_command` and `on_resume_command` run only if you set them, with your own permissions and environment; the notify text is passed as an argument.
- Scheduling: `./install.sh --idle` writes and loads a launchd job (`~/Library/LaunchAgents/com.claude-tools.save-clear-idle.plist`, every 2 minutes); on Linux it only prints a cron line.
- Network: the plugin's own code opens no connections and reads no credentials. The relay's `claude -p` call talks to Anthropic like any Claude session, and with `publish_link: on` the skill publishes the summary as a private claude.ai page.
