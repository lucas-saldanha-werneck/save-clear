# Changelog

Versions are the `version` in `.claude-plugin/plugin.json`. Newest first.

## 0.14.1 (2026-11-13)

- Skill: add version and tags, a Purpose section, Prerequisites, Limitations and Troubleshooting; SkillEvaluator badges in the README.
- README: demo GIF and badges; add SECURITY.md and this CHANGELOG.

## 0.14.0 (2026-11-13)

- Show answers given during the save in the new session; no question dialog in the first resume turn.

## 0.13.2 (2026-11-13)

- Skill: add author metadata, Instructions and Examples sections (docs only).

## 0.13.1 (2026-11-12)

- Fix invalid YAML in SKILL.md frontmatter (quote description as folded block).

## 0.13.0 (2026-11-12)

- Run save-clear automatically at 150k tokens instead of 300k.

## 0.12.0 (2026-11-12)

- Clear right away while subagents run; handle results and prompts at the clear.

## 0.11.0 (2026-11-12)

- Wait for running subagents instead of stopping them before the automatic /clear.

## 0.10.5 (2026-11-12)

- Clear guard: an image-only message, or one starting with `<` that is not a Claude Code tag, counts as the user's message; a broken line no longer lets the clear through.
- PreCompact backup is private (0600), written atomically, 10 newest kept.
- Relay: one listener on Windows too; warns when the app log is missing.
- install.sh: quoted cron line, state folder created, interval must divide the hour.

## 0.10.4 (2026-11-11)

- Ignore an inherited app host id in terminal sessions.

## 0.10.3 (2026-11-11)

- Auto /clear in tmux even with an inherited app entrypoint.

## 0.10.2 (2026-11-11)

- Point to the catalog repo's new name, `lucas-tools`.

## 0.10.1 (2026-11-11)

- Turn Remote Control off only for a listener that turns it back on.
- Keep the resume when Remote Control comes back on its own; RC-KEEP when the relay is off; fresher heartbeat.

## 0.10.0 (2026-11-11)

- Desktop app: clear a session whose Remote Control is on.

## 0.9.1 (2026-11-11)

- Catalog renamed to `lucas-tools`.

## 0.9.0 (2026-11-11)

- Relay turns idle Remote Control off (`rc_idle_off_hours`).

## 0.8.1 (2026-11-10)

- Idle trigger: do not ask the same session again until the user writes.

## 0.8.0 (2026-11-09)

- Desktop app relay sets itself up after install.

## 0.7.3 (2026-11-09)

- Short description, catalog install steps, settings headings in the README.

## 0.7.2 (2026-11-09)

- `notify_success` option: notify only on failures when off.

## 0.7.1 (2026-11-08)

- State folder: ignore another plugin's `CLAUDE_PLUGIN_DATA`.

## 0.7.0 (2026-11-08)

- `kill_background_tasks` option: stop this session's leftover background tasks before the automatic /clear.
- `kill_background_tasks`: review fixes.

## 0.6.0 (2026-11-08)

- Config options: language, resume text, extra steps, on-save/pre-clear/on-resume commands, `~/.config` file.
- Create `~/.config/save-clear/config.json` on first run from `config.example.json`; seeded options are null so they follow later defaults.
- Config file: 0 and false are values; a short custom `resume_message` no longer hides the user's words.
- Seed: skip a relative config path (it would land in the project folder).

## 0.5.0 (2026-11-08)

- Idle trigger: a session idle for N minutes runs save-clear by itself (opt-in).
- Idle trigger: fixes from code review.

## 0.4.1 (2026-11-08)

- Windows: UTF-8 output and files; skip tmux-only tests there.

## 0.4.0 (2026-11-08)

- Queued messages count as messages after the save; the helper checks for a new message right before pressing Enter on /clear.
- Resume is handed only to a session with a stable owner (process, app session or job), not by folder and age.
- Context cap: no second ask right after a save; asks again after the context drops below the limit.

## 0.3.0 (2026-11-08)

- Ask for save-clear when the context passes a token limit (default 300k, option `context_cap_tokens`).

## 0.2.0 (2026-11-08)

- Lock the resume queue; one automatic /clear request per session; check the pane before each Enter.
- Desktop app: optional relay session that sends the resume after `clear_session`.
- iTerm2 without tmux no longer types /clear; Claude asks the user to type it.

## 0.1.0 (2026-11-08)

- Save the session, clear the context by itself, resume from the summary.
