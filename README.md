# save-clear

Say "save and clear" at the end of a Claude Code session. Claude writes the decisions, findings and
next steps to a summary file, checks that the file is on disk, clears its own context, and the next
session starts from that summary. You keep what matters from a long session without doing the save
and the `/clear` by hand.

You do not even have to ask: when the context passes 300k tokens (30% of a 1M window), Claude runs
it by itself at the end of the turn. Change the number with `context_cap_tokens`, or set it to `0`
to turn it off.

Optionally, a session you leave idle for 50 minutes can run it by itself too, before the prompt cache
expires (see [Idle trigger](#idle-trigger-optional)). That one is off until you schedule it.

## What Claude sees

After the `/clear`, the plugin's SessionStart hook adds the summary to the new session's context.
You see one line:

```
save-clear: summary of the previous session loaded. Send any message (e.g. ok) to see it.
```

Claude sees this (output of a real run, path shortened):

```
## Previous session (auto-resume)

Snapshot: `/home/you/.claude/snapshots/SESSION-SUMMARY-20261108-101500-billing.md`

ORDER: write the summary as a TEXT message before asking any question. A question first ends the turn and the user never sees the summary.

---
session_id: 3f2a9c1e-5b7d-4e2a-9f10-2c4b6d8e0a11
previous_snapshot: SESSION-SUMMARY-20261107-183000-billing.md
project: billing
---
# Session 2026-11-08 - billing export
## Last topic
CSV export works; the open question is whether totals include tax.
## What was done
...

---
Continue from the point above. If the user brings a completely new topic, ignore this context and do what they ask.
```

When the clear was typed for you (tmux or `claude --bg`) and the helper confirmed it took, it also types
`save-clear: resuming. Start with the LAST topic (where we stopped and what is still open); then, in
one short paragraph, the rest that was saved and the next steps.` so the new session opens with a
recap.

## Install

```
/plugin marketplace add lucas-saldanha-werneck/save-clear
/plugin install save-clear@save-clear
```

Restart the session so the hooks load. Then say "save and clear" (also "save-clear" or
"salvalimpa"), or "checkpoint" / "save the session" to save without clearing.

Requires `python3` 3.8 or newer. The automatic `/clear` also needs the `claude` CLI on `PATH`
(the helper runs `claude agents --json` to check which session it is typing into). macOS or Linux.
On Windows, save and resume work, but the automatic `/clear` does not (see below). The hooks call
`python3`, so that command must start Python there.

## Settings

Set them with `/plugin configure save-clear@save-clear`, or with environment variables.

| Option | Environment variable | Default | What it does |
|---|---|---|---|
| `snapshot_dir` | `SAVE_CLEAR_SNAPSHOT_DIR` | `~/.claude/snapshots` | Where summaries and the compaction backups go |
| `notify_command` | `SAVE_CLEAR_NOTIFY` | empty (off) | A program called with one line of text when the automatic `/clear` fails, is refused or stops halfway, and when it succeeds in tmux |
| `publish_link` | `SAVE_CLEAR_PUBLISH_LINK` | `off` | `on`: the skill publishes the summary as a private claude.ai page (when the Artifact tool is available), and the next session shows that link first |
| `context_cap_tokens` | `SAVE_CLEAR_CAP_TOKENS` | `300000` | When the context passes this many tokens, Claude runs save-clear at the end of the turn. `0` = off. With a 200k window, set it lower (e.g. `150000`), or compaction comes first |
| `idle_minutes` | `SAVE_CLEAR_IDLE_MINUTES` | `50` | Idle trigger only (after `./install.sh --idle`): minutes a session must be idle before it runs save-clear. `0` = off |
| `idle_min_tokens` | `SAVE_CLEAR_IDLE_MIN_TOKENS` | `80000` | Idle trigger only: sessions with a smaller context are left alone |
| (none) | `SAVE_CLEAR_LAUNCHD_PREFIX` | `com.claude-tools` | Idle trigger only: prefix of the launchd job label (`<prefix>.save-clear-idle`) |
| (none) | `SAVE_CLEAR_STATE_DIR` | `$CLAUDE_PLUGIN_DATA`, else `~/.claude/state/save-clear` | Flags, relay inbox and the logs `save-clear.log` and `relay.log` |

## Optional requirements

You need none of these. Without them everything works except the automatic `/clear`: Claude tells
you to type `/clear`, and the summary still comes back by itself.

- tmux: automatic `/clear` in a tmux pane, and for `claude --bg` sessions. tmux does not run
  natively on Windows, so on Windows you always type `/clear` yourself.
- A notifier for `notify_command`, for example a script that sends you a push message.
- A relay session in the Claude desktop app, so the resume arrives right after the clear instead of
  with your next message. See [Desktop app relay](#desktop-app-relay).
- A scheduler for the idle trigger: launchd on macOS (`./install.sh --idle` sets it up), cron on
  Linux (`./install.sh --idle` prints the line). Without it the idle trigger simply never runs.
- Auto memory: if the project has a `~/.claude/projects/<project>/memory/` folder, Claude updates it
  too. Otherwise that step is skipped.

## How it works

The skill tells Claude to:

1. List what must survive: decisions with the reason, verified state, next steps, open items, and
   paths or commands that were hard to find.
2. Update auto memory, if the project has it.
3. Write `SESSION-SUMMARY-<timestamp>-<slug>.md` in the snapshot folder. Its frontmatter points to
   the previous summary, so a project's summaries form a chain.
4. Re-read the files it wrote.
5. Run `save-clear arm-resume <file>`, which records which session asked for the resume.
6. In "save and clear" mode, run `save-clear arm-clear`, which picks how the `/clear` can be sent.

The SessionStart hook hands the summary only to the session that armed it, after its `/clear`.
A terminal or IDE session is matched by process id (the `/clear` must happen in the same process),
a desktop app session by its app session id, a `claude --bg` session by its job id. Other sessions
and other folders get nothing. If none of these ids is available when the resume is armed, the
summary is not handed out automatically: `arm-resume` prints a warning, and after the `/clear` you
ask Claude to read the summary file. Unclaimed
entries are dropped after 7 days. Arming and handing out a summary each hold a file lock while they
read and rewrite the list, so sessions that save at the same moment keep each other's entries.

Each session's automatic `/clear` request is its own file in the state folder (`clear/<session
id>.json`, or `clear/job-<id>.json` for `claude --bg`). One session arming never replaces another's
request, and `save-clear rearm` only changes the calling session's request.

How the `/clear` happens:

| Where Claude runs | What happens |
|---|---|
| tmux pane | The Stop hook starts a helper. Before every key it checks that the pane still runs this session and the same process. It types `/clear` only when the session is idle and the prompt is empty. Right before pressing Enter it checks again: same session and process, still idle, and the prompt holds the text it typed and nothing else (spaces and line wraps are ignored). If anything changed, it does not press Enter, leaves the text in the prompt and stops. It confirms that the same process now has a new session id, then types the resume request the same way. |
| iTerm2 tab (macOS), no tmux | Claude tells you to type `/clear`. Through AppleScript the helper cannot see whether the prompt is empty or the session idle, so it does not type there. |
| `claude --bg` session | The helper waits up to 60 seconds for the job to leave the working state; if it does not, it types nothing and re-arms. It also types nothing if a message arrived after the save. Otherwise it attaches in a hidden tmux session, waits for an idle, empty prompt, types `/clear`, checks the prompt again before Enter, and detaches. It types the resume request only if the job's state file then shows a new conversation id; otherwise the summary comes with your next message. Needs tmux. |
| Any other terminal | Claude tells you to type `/clear`. |
| Claude desktop app | Claude calls the app's `clear_session` tool with `session_id: "self"`. The summary arrives with your next message, or right away if you set up the relay. |

If you send a message after the save, the Stop hook does not clear. It asks Claude to answer it, add
it to the summary under `## After the save`, and run `save-clear rearm <session id>`. This repeats up
to 3 times. A message still waiting in the queue counts too. The helper checks for new messages
again right before it presses Enter on `/clear`; if one arrived, it erases the `/clear` it typed,
does not press Enter, and re-arms the request for the next end of turn.
In the desktop app, a PreToolUse hook holds the first `clear_session` call the same way.

A second Stop hook watches the context size. When Claude ends a turn and the last reply used more
than `context_cap_tokens` (input plus cache tokens, subagents not counted), it blocks the stop once
and asks Claude to run save-clear in "save and clear" mode. It never acts in the middle of a task.
It asks again only after the context grows another 50k tokens, or after the context drops below the
limit (for example after a compaction) and crosses it again. It skips `claude -p` and SDK sessions,
a session that already armed its `/clear` or armed a resume in the last 15 minutes, and the desktop
app relay session.

Two backups run without the skill. Before any compaction, the full transcript is saved gzipped with
an `EMERGENCY-COMPACT-<timestamp>-<id>.md` marker. After it, the summary Claude Code wrote is kept as
`COMPACT-SUMMARY-<timestamp>-<id>.md`.

### Idle trigger (optional)

Anthropic's prompt cache keeps a session's context for up to 60 minutes. Coming back to a big
session before that reads the context cheaply from the cache; after that, the whole context is
written to the cache again at full price. The idle trigger runs save-clear in a session you left
idle for `idle_minutes` (default 50), so the next time you come back the session holds a short
summary instead of the big context.

It is opt-in. Turn it on once, from a clone of this repo or from the installed plugin folder:

```
./install.sh --idle               # macOS: launchd job, every 2 minutes
./install.sh --idle --dry-run     # show the job without writing or loading it
./install.sh --uninstall          # remove it
```

On Linux the script prints a cron line for you to add; it changes nothing. The job points at the
folder you ran `install.sh` from, so run it again if that folder moves (a plugin update installs a
new folder).

Every run, `bin/save-clear-idle` asks `claude agents --json` for interactive, idle sessions and
types this into a session's tmux pane:

```
save-clear (automatic: session idle for 52 min, context 120k; the prompt cache expires at 60 min). Run the save-clear skill now in "save and clear" mode. ...
```

only when all of these hold:

- nothing in the session changed (transcript, subagents, background task output) for between
  `idle_minutes` and `idle_minutes` + 8 minutes. Later than that the cache is gone anyway, so it
  leaves old sessions alone;
- the context is at least `idle_min_tokens` (default 80000);
- the session has not armed its own `/clear`, and did not arm a resume in the last 15 minutes;
- after an automatic resume, the context grew 40k tokens or more (a freshly resumed session is not
  saved again every hour);
- it did not already act during this idle stretch;
- the session runs in a tmux pane, that pane runs exactly this session, and the prompt is empty.
  Right before Enter it checks again that the session is idle and the prompt holds exactly the typed
  text. If not, it does not press Enter and does not type again during that idle stretch.

Sessions outside tmux (a plain terminal, iTerm2 without tmux, the desktop app, an IDE) are skipped:
there is no safe way to check for an empty prompt there. Decisions go to `save-clear.log` with the
tag `[idle]`. See what it would do right now with `bin/save-clear-idle --dry-run`.

### Desktop app relay

A desktop app session can clear itself but cannot send itself a message, so the resume waits for
your next message. The relay is one app session that serves the others: it watches an inbox folder,
waits for a session's clear, and sends that session the resume request with `send_message`.

Setup, once:

1. Open a new desktop app session in any folder, name it "relay", and use it only for this.
2. In it, ask Claude to run `python3 <plugin folder>/bin/save-clear-relay setup`. This records the
   session's id and prints the message that starts the listener.
3. Send that message (`RELAY listen. Run ... rules and follow the cycle from step 1.`).

After that, "save and clear" in another app session queues a request with
`save-clear-relay request`. If the relay is not listening, that session wakes it with
`send_message`.

A request stays pending until the relay acknowledges it with `save-clear-relay ack <name> ok` after
`send_message`. If the relay reports a failure, or its turn is lost before the ack, the request is
handed out again (after 10 minutes without an ack), at most 3 times. After that it is kept as failed
and `status` lists it. A lost ack can make the resume request arrive twice.

Other commands:

- `save-clear-relay status`: relay id, whether it is listening, how many requests are queued,
  handed out but not acknowledged (`unacked`) or given up (`failed`), and the state folder.
- `save-clear-relay off` and `on`: while off, requests are refused.
- `save-clear-relay setup <relay id> <waker id>`: when the relay's own context fills up it saves
  and clears itself (see `relay/RULES.md`), and the second session wakes it after that clear. Without
  a waker, requests wait in the inbox until someone messages the relay.

The relay detects a clear by finding `Clearing session <id>` in the app's log. On macOS that is
`~/Library/Logs/Claude/main.log`. The code also guesses a path on Linux and Windows; set
`SAVE_CLEAR_APP_LOG` to override it. With no readable log, the relay delivers 60 seconds after the
request (`SAVE_CLEAR_RELAY_GRACE`).

## Uninstall

```
/plugin uninstall save-clear@save-clear
```

Your summaries stay in the snapshot folder. Claude Code deletes the plugin's data folder on
uninstall, which is where the state lives by default. If you set `SAVE_CLEAR_STATE_DIR`, or the
state ended up in `~/.claude/state/save-clear`, delete that folder yourself, plus the pointer file
`~/.claude/state/save-clear.path`. Archive the relay session if you made one. If you scheduled the
idle trigger, run `./install.sh --uninstall` first (on Linux, remove the cron line).

## Limits and what was not tested

- Tested: the hooks and commands, with simulated input (`python3 tests/test_hooks.py`, 70 tests,
  also under Python 3.8), and the tmux path against a fake Claude in a separate tmux server
  (`bash tests/run.sh`, 32 checks). The `claude --bg` helper is tested against a fake tmux and a
  fake job state file. CI runs both on Ubuntu and macOS. On Windows, CI runs the unit tests
  only, and skips the ones for the automatic `/clear`, which needs tmux. Install and uninstall were run with the
  `claude plugin` CLI.
- Not tested live: the desktop app relay round trip (request, clear, `send_message`, ack, resume),
  the waker, and `claude --bg` sessions. The unit tests cover their decision logic only. The
  `claude --bg` clear confirmation reads `resumeSessionId` from `~/.claude/jobs/<id>/state.json`;
  that field was seen in real state files, but not watched across a real clear. If it does not
  change, the helper only skips the resume request. The desktop app session matching relies on the `CLAUDE_CODE_HOST_SESSION_ID` variable,
  which was not checked from inside the app here. IDE sessions were not tested live; matching them
  by process id assumes `/clear` there keeps the same process, as in the terminal.
- The idle trigger is tested with a fake `claude agents --json`, a fake tmux and transcripts with
  set modification times, and `install.sh` with a fake HOME and a fake `launchctl`. The real launchd
  job was not loaded in the tests. On Windows, CI skips the tests that type into tmux and the
  `install.sh` tests.
- The app log format was checked against a real macOS log. The Linux and Windows log paths were not.
- Windows was tested only through the unit tests in CI, not in a real Claude session. The
  automatic `/clear` does not work on Windows, because it needs tmux.
- The tmux helper reads the screen to see an empty prompt. If colors are off in tmux, it may treat
  the dim placeholder text as a draft and not type.

## Test

```
python3 tests/test_hooks.py -v
bash tests/run.sh        # tmux integration on a separate tmux server; skips without tmux
```

## License

MIT

Built with Claude Code
