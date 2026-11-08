# save-clear

Say "save and clear" at the end of a session. Claude writes what matters to memory and to a
session summary, checks that the files are on disk, clears its own context, and the next session
starts from that summary.

## Why

Long sessions get slow and expensive, and `/clear` or an automatic compaction throws away what
you decided and why. Doing the save by hand every time is tedious, and it's easy to clear before
the save actually landed. save-clear turns it into one phrase:

- a **verified** snapshot (decisions, findings, what failed, verified state, next steps) in a
  folder you choose, chained to the previous one;
- an **auto-resume** that hands the snapshot back only to the session that saved it, after its
  `/clear`, and to no other session in any other folder;
- an **automatic `/clear`** when Claude runs in tmux or iTerm2, typed only once the session is idle,
  the prompt is empty and the target is confirmed to be this very session.

## What Claude sees

After `/clear`, the SessionStart hook adds this to the new session's context:

```
## Previous session (auto-resume)

Snapshot: `~/.claude/snapshots/SESSION-SUMMARY-20261108-101500-billing.md`

ORDER: write the summary as a TEXT message before asking any question. ...

---
session_id: 3f2a...
previous_snapshot: SESSION-SUMMARY-20261107-183000-billing.md
project: billing
---
# Session 2026-11-08 - billing export
## Last topic
CSV export works; the open question is whether totals include tax.
## What was done ...
```

In tmux or iTerm2, the helper then types `save-clear: resuming. Start with the LAST topic ...`,
so the new session opens with a visible recap.

## Install

```
/plugin marketplace add lucas-saldanha-werneck/save-clear
/plugin install save-clear@save-clear
```

Restart the session so the hooks load. Then say **"save and clear"** (or "checkpoint" to save
without clearing). "salvalimpa" also works.

Requires `python3` (3.8+) and the `claude` CLI on `PATH`. macOS or Linux.

## How the clear happens

| Where Claude runs | What happens |
|---|---|
| tmux pane | Automatic `/clear` + resume request |
| iTerm2 tab (macOS), no tmux | Automatic `/clear` + resume request, through AppleScript by tty |
| `claude --bg` background session | Attaches in a hidden tmux, types, detaches (needs tmux) |
| Any other terminal | Claude tells you to type `/clear`; the summary comes back by itself |
| Claude desktop app | Claude calls the app's `clear_session` tool on itself; the summary arrives with your next message |

If a message from you arrives after the save, the Stop hook doesn't clear: Claude answers it, adds
it to the snapshot under `## After the save`, and re-arms.

Safety nets that run without the ritual: before any compaction the full transcript is saved
gzipped (`EMERGENCY-COMPACT-*`), and the compaction summary is kept (`COMPACT-SUMMARY-*`).

## Settings

`/plugin configure save-clear@save-clear`:

- `snapshot_dir`: where summaries go. Default `~/.claude/snapshots`.
- `notify_command`: a program called with one line of text when the automatic `/clear` succeeds,
  fails or is refused. Empty = off.
- `publish_link`: `on` makes the skill publish the summary as a private claude.ai page (when the
  Artifact tool is available) and the next session shows the link first. Default `off`.

The same as environment variables: `SAVE_CLEAR_SNAPSHOT_DIR`, `SAVE_CLEAR_NOTIFY`,
`SAVE_CLEAR_PUBLISH_LINK`. Also `SAVE_CLEAR_STATE_DIR` (flags and log, default
`~/.claude/state/save-clear`). Log: `~/.claude/state/save-clear/save-clear.log`.

## Optional requirements

None of these are needed. Without them, everything works except the automatic `/clear`, and
Claude tells you to type it.

- **tmux**: automatic `/clear` in a tmux pane, and for `claude --bg` sessions.
- **iTerm2** (macOS): automatic `/clear` without tmux. macOS may ask once for Automation permission to control
  iTerm2.
- **A notifier** for `notify_command`, e.g. a script that sends a push or chat message, so you know
  the clear happened when you're away from the screen.
- **Remote Control**: works as long as the session runs in tmux or iTerm2 on your computer. The
  phone keeps the old screen until the next message, which is why `publish_link` exists.
- **Auto memory**: if the project has a `~/.claude/projects/<project>/memory/` folder, Claude
  updates it too. Otherwise that step is skipped.

## Uninstall

```
/plugin uninstall save-clear@save-clear
```

Snapshots stay in your snapshot folder. Remove `~/.claude/state/save-clear` to drop the flags and
the log.

## Test

```
python3 tests/test_hooks.py -v
bash tests/run.sh        # tmux integration, on a separate tmux server; skips without tmux
```

## License

MIT.

Built with Claude Code
