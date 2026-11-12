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

Optionally, the summary is also published as a private claude.ai page, and the next session's first
line is its link: `Previous conversation: https://claude.ai/artifact/...`. The old conversation stays
one tap away, which helps on the phone, where the chat is wiped by the `/clear`. Turn it on with
`publish_link` (needs the Artifact tool).

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
/plugin marketplace add lucas-saldanha-werneck/lucas-tools
/plugin install save-clear@lucas-tools
```

Restart the session so the hooks load. Then say "save and clear" (also "save-clear" or
"salvalimpa"), or "checkpoint" / "save the session" to save without clearing.

In the Claude desktop app there is nothing else to set up: the first "save and clear" in an app
session creates the [relay](#desktop-app-relay) session by itself (titled "Don't touch it, relay").
Terminal sessions never create one.

Requires `python3` 3.8 or newer. The automatic `/clear` also needs the `claude` CLI on `PATH`
(the helper runs `claude agents --json` to check which session it is typing into). macOS or Linux.
On Windows, save and resume work, but the automatic `/clear` does not (see below). The hooks call
`python3`, so that command must start Python there.

## Settings

Set them with `/plugin configure save-clear@lucas-tools` (or the `/config` panel), with environment
variables, or in your own file `~/.config/save-clear/config.json` (`$XDG_CONFIG_HOME/save-clear/` if
set; another path with `SAVE_CLEAR_CONFIG`). Same keys as the option names:

```json
{
  "language": "Portuguese",
  "notify_command": "my-notify",
  "on_save_command": "~/.config/save-clear/on-save.sh"
}
```

**Your values survive plugin updates.** Plugin options are stored by Claude Code under
`pluginConfigs` in your `settings.json`, and the config file lives outside the plugin folder; an
update replaces only the plugin's code. Precedence: plugin option (in Bash, the copy the SessionStart hook saved), then environment variable, then
the config file. Keep your personal behavior in small scripts next to that file instead of editing
the plugin.

### Config file

Your file is `~/.config/save-clear/config.json` (or the `$XDG_CONFIG_HOME` / `SAVE_CLEAR_CONFIG`
path above; on Windows `~` is `%USERPROFILE%`). You don't have to create it: the plugin writes it
the first time a session starts (or the first time the skill runs), from
[`config.example.json`](config.example.json), with every option set to `null` and a one-line note
per option under `_doc` that shows its default. `null` (or an empty string) means "use the built-in
default", so that file behaves exactly like having no file, and a later version that changes a
default reaches you too. Plugin updates never touch
it, and an existing file is never overwritten, so edit it freely.

| Option | Environment variable | Default | What it does |
|---|---|---|---|
| `snapshot_dir` | `SAVE_CLEAR_SNAPSHOT_DIR` | `~/.claude/snapshots` | Where summaries and the compaction backups go |
| `notify_command` | `SAVE_CLEAR_NOTIFY` | empty (off) | A program called with one line of text when the automatic `/clear` fails, is refused or stops halfway, and when it succeeds in tmux |
| `publish_link` | `SAVE_CLEAR_PUBLISH_LINK` | `off` | `on`: the skill publishes the summary as a private claude.ai page (when the Artifact tool is available), and the next session shows that link first |
| `context_cap_tokens` | `SAVE_CLEAR_CAP_TOKENS` | `300000` | When the context passes this many tokens, Claude runs save-clear at the end of the turn. `0` = off. With a 200k window, set it lower (e.g. `150000`), or compaction comes first |
| `idle_minutes` | `SAVE_CLEAR_IDLE_MINUTES` | `50` | Idle trigger only (after `./install.sh --idle`): minutes a session must be idle before it runs save-clear. `0` = off |
| `idle_min_tokens` | `SAVE_CLEAR_IDLE_MIN_TOKENS` | `80000` | Idle trigger only: sessions with a smaller context are left alone |
| `language` | `SAVE_CLEAR_LANGUAGE` | empty | Language of the snapshot and of the first reply after the resume (e.g. `Portuguese`) |
| `resume_message` | `SAVE_CLEAR_RESUME_MESSAGE` | built-in English request | Text typed after the automatic `/clear` (tmux, `--bg`) and sent by the relay in the desktop app. It is never counted as a message from you |
| `extra_steps` | `SAVE_CLEAR_EXTRA_STEPS` | empty | Your own steps for the ritual, as text or as the path of a file. `save-clear dir` prints them and the skill follows them (e.g. "call plan_steps before clear_session") |
| `on_save_command` | `SAVE_CLEAR_ON_SAVE` | empty | Runs when the skill arms the resume (before any clear). What it prints is appended to the snapshot, so the next session sees it |
| `pre_clear_command` | `SAVE_CLEAR_PRE_CLEAR` | empty | Runs right before the automatic `/clear` (tmux, `--bg`) or before `clear_session` goes through (desktop app). Output ignored; it never stops the clear |
| `on_resume_command` | `SAVE_CLEAR_ON_RESUME` | empty | Runs after the `/clear`, when the summary is delivered (5 s limit). What it prints (exit 0) is added to the new session's context, before the summary |
| `kill_background_tasks` | `SAVE_CLEAR_KILL_BACKGROUND_TASKS` | `off` | `on`: before the automatic `/clear`, Claude stops the background tasks this session left running (Bash `run_in_background` jobs, Monitor watches) and lists them in the summary, and the next session names them. See [Leftover background tasks](#leftover-background-tasks) |
| `notify_success` | `SAVE_CLEAR_NOTIFY_SUCCESS` | `on` | `off`: `notify_command` runs only when the automatic `/clear` fails, is refused or stops halfway, not when it works |
| `relay` | `SAVE_CLEAR_RELAY` | `auto` | Desktop app only. `auto`: the first save-and-clear in an app session creates the relay session when there is none, or when the configured one is archived. `off`: never create one (a relay you set up yourself still works; `save-clear-relay off` turns that off too). See [Desktop app relay](#desktop-app-relay) |
| `relay_model` | `SAVE_CLEAR_RELAY_MODEL` | `haiku` | Model of the relay session the plugin creates |
| `relay_title` | `SAVE_CLEAR_RELAY_TITLE` | `Don't touch it, relay` | Title of the relay session the plugin creates |
| `rc_idle_off_hours` | `SAVE_CLEAR_RC_IDLE_OFF_HOURS` | off | Desktop app: once an hour the relay turns Remote Control off for sessions with no message from you for this many hours (the relay itself and sessions in `~/.claude/rc-auto-keep-on.json` stay on). `save-clear-relay rc-sweep --dry-run` lists them |
| (none) | `SAVE_CLEAR_LAUNCHD_PREFIX` | `com.claude-tools` | Idle trigger only: prefix of the launchd job label (`<prefix>.save-clear-idle`) |
| (none) | `SAVE_CLEAR_STATE_DIR` | `$CLAUDE_PLUGIN_DATA`, else `~/.claude/state/save-clear` | Flags, relay inbox and the logs `save-clear.log` and `relay.log` |

### Commands you plug in

The three `*_command` options are split like a shell line (no shell runs them; put pipes in a
script) and get these environment variables:

| Variable | on_save | pre_clear | on_resume |
|---|---|---|---|
| `SAVE_CLEAR_EVENT` | `save` | `pre_clear` | `resume` |
| `SAVE_CLEAR_SNAPSHOT` | yes | | yes |
| `SAVE_CLEAR_SESSION_ID` | the saving session | the session being cleared | the new session |
| `SAVE_CLEAR_PREVIOUS_SESSION_ID` | | | the session that saved |
| `SAVE_CLEAR_MODE`, `SAVE_CLEAR_TARGET` | | `tmux` + pane, `bg` + job, or `app` + app session | |
| `SAVE_CLEAR_PROJECT_DIR` | yes | | yes |

`on_save_command` runs from the skill's Bash call, so `CLAUDE_PID` is set too: a script can find
the session's own background processes (for example a long-running poll that `/clear` will stop)
and print a note telling the next session to start them again. Failures and timeouts are written to
`save-clear.log` and never block the save or the clear.

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

### Leftover background tasks

A background task this session forgot about (a `run_in_background` Bash job, a Monitor watch) keeps
`claude agents` saying busy. Since 0.12.0 that no longer holds the `/clear` once the turn is over (see
[Subagents still running](#subagents-still-running)); the option is for stopping such leftovers anyway.
With `kill_background_tasks` off (the default) nothing changes. With it on:

- `save-clear dir` and `save-clear arm-clear` print a `kill_background_tasks: on` line, and the skill
  tells Claude to stop the tasks this session started that are still running, with the TaskStop tool,
  and to list them under `## Stopped background tasks` in the summary (or "none").
- If the helper still finds the session busy and re-arms, the next Stop hook blocks once and asks
  Claude for the same thing; the stop after that goes ahead with the `/clear`. That second chance
  needs a later end of turn: if a task keeps the session busy while no turn runs at all, no Stop hook
  comes, so the first step (before the clear is armed) is the one that counts. A re-arm caused by
  your own message after the save does not count as busy.
- After the `/clear`, the resume tells the new session to name the stopped tasks and say which ones
  need to be started again (not when the section only says "none"). The heading stays in English,
  even with `language` set, because the resume looks for it.

The plugin itself never kills a process. Hooks and the helper cannot tell this session's background
tasks apart from other processes (MCP servers, other sessions, your own programs), and only Claude
can stop its own tasks, so this works through instructions to Claude, not from a hook.

### Subagents still running

A background subagent (Agent tool) is work in progress, not a leftover, and it survives `/clear`: it
keeps running and its result arrives in the new conversation as a notification (watched on Claude Code
2.1.322). While one runs, `claude agents` reports the session as `busy` even after the turn ended with
an empty prompt. Before 0.11.0 the helper took that for a leftover task, and with
`kill_background_tasks` on the Stop hook asked Claude to stop "every background task", so subagents
were stopped mid-work and their results lost. Now:

- The helper reads the transcript: when the turn's end record is the newest main-thread record, "busy"
  comes only from background work (subagents, `run_in_background` commands, also a subagent's own) and
  counts as idle (the screen must still show no "esc to interrupt" and an empty prompt), so the
  `/clear` goes ahead at once. Watched live: a subagent's background `sleep 90; date > file` kept
  running across the automatic `/clear` and wrote the file.
- The skill lists each running subagent under `## Running subagents`: id, description, a brief
  summary (goal, repo/worktree/branch, what it will deliver, whether it merges or installs by itself),
  the brief's path and what to do with its result. After the `/clear`, the resume tells the new session
  that results will arrive as notifications and to act on each one by that section.
- A subagent result that reaches the old conversation after the save but before the `/clear` blocks
  the Stop hook once: Claude records it under `## After the save` and runs
  `save-clear rearm <session id> --subagents` (up to 20 rounds; they do not use up the 3 rounds kept
  for your own messages).
- The stop-tasks instruction (`kill_background_tasks`) says never to stop a subagent.
- A subagent's permission prompt (or any open dialog) makes `claude agents` say `waiting`. The helper
  never types then (keys would answer the dialog); it waits until the prompt is answered, then clears.
- A result that starts a turn while `/clear` sits in the prompt: the helper sees the turn begin before
  pressing Enter, erases `/clear` (only when the prompt holds exactly that) and does not press Enter.
  A result whose turn ran while the helper held the request is caught before the next `/clear` try:
  Claude is asked to record it under `## After the save` first. All three were seen live on 2.1.322.
- The slash-command menu under the input box marks its item with the same sign as the prompt; the
  helper now reads the input box itself, so a typed `/clear` with the menu open is still recognized.
- The skill asks Claude to hand long work to background subagents, so save-clear never has to wait.

Two backups run without the skill. Before any compaction, the full transcript is saved gzipped with
an `EMERGENCY-COMPACT-<timestamp>-<id>.md` marker; the backup is readable by you only, and the 10 newest
are kept (`SAVE_CLEAR_EMERGENCY_KEEP`, 0 = keep all). After it, the summary Claude Code wrote is kept as
`COMPACT-SUMMARY-<timestamp>-<id>.md`.

### Idle trigger (optional)

The idle trigger targets the **1-hour prompt cache**. While a session's context is in that cache,
coming back to a big session reads it cheaply; once the cache expires, the whole context is written
to the cache again at full price. It does not check which cache length your account or setup gets:
if your sessions use the 5-minute cache, the cache is already cold at 50 minutes and the trigger
only saves you from re-sending a big context, not from a cache write. The idle trigger runs save-clear in a session you left
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
save-clear (automatic: session idle for 52 min, context 120k; saving before the prompt cache expires). Run the save-clear skill now in "save and clear" mode. ...
```

only when all of these hold:

- nothing in the session changed (transcript, subagents, background task output) for between
  `idle_minutes` and `idle_minutes` + 8 minutes, and never more than 59. Later than that the cache
  is gone anyway, so it leaves old sessions alone. With `idle_minutes` 59 or more there is no window
  and it logs an error and does nothing;
- the context is at least `idle_min_tokens` (default 80000);
- the session has not armed its own `/clear`, and did not arm a resume in the last 15 minutes;
- after an automatic resume, the context grew 40k tokens or more (a freshly resumed session is not
  saved again every hour);
- it did not already act during this idle stretch (a stretch ends when the transcript changes). If
  it pressed Enter and the session wrote nothing at all, it logs `no-response` once and does not
  type again;
- the session was not already asked with nothing from you after it. A session that was asked and
  still has the same id did not clear (a usage limit, a failed `/clear`, or the same session open
  in several panes, which share one transcript); asking again every hour would only save the same
  context again. It asks again after you do something there (a message, a queued message, an
  image, a slash or `!` command, an interrupt);
- it did not give up on the session. A request counts as failed when it landed, you did nothing
  after it, and the same process is still on the same session id (a `/clear` gives a new id).
  After 2 of those it logs `gave-up`, runs your notify command once and leaves that session alone.
  A request you answered, or one typed into another pane of the same session, is not a failure;
- the session runs in a tmux pane, that pane runs exactly this session, and the prompt is empty.
  Right before Enter it checks again that the session is idle and the prompt holds exactly the typed
  text. If not, it does not press Enter, erases its text if the prompt still holds exactly that, and
  does not type again during that idle stretch.

Sessions outside tmux (a plain terminal, iTerm2 without tmux, the desktop app, an IDE) are skipped:
there is no safe way to check for an empty prompt there. Decisions go to `save-clear.log` with the
tag `[idle]`. See what it would do right now with `bin/save-clear-idle --dry-run`.

Most runs start no process: it looks at transcript times first and asks `claude agents --json` only
when one is in the window. The job gets the `PATH` and `CLAUDE_CONFIG_DIR` (if set) of the shell you
ran `install.sh` from; if your sessions use another config folder, set it and run `install.sh` again
(no transcripts found is logged once). The job's own errors go to `idle-job.log` in the state folder.
On Linux, `--interval` must be whole minutes that divide the hour (60, 120, 180, 240, 300, 360, 600, 720,
900, 1200 or 1800 seconds): cron counts in minutes, and `*/45` would run at :00 and :45.

### Desktop app relay

A desktop app session can clear itself but cannot send itself a message, so the resume waits for
your next message. The relay is one app session that serves the others: it watches an inbox folder,
waits for a session's clear, and sends that session the resume request with `send_message`.

**It sets itself up.** On "save and clear" in an app session, the skill runs
`save-clear-relay ensure` before asking for the resume. When no relay is configured (or the
configured one is archived), `ensure`:

1. creates a session with `claude -p --session-id <uuid>` in `<state folder>/relay-session`, on
   `relay_model` (default `haiku`, cheap) and in the same permission mode as the session that creates
   it (plan mode becomes `default`, since the relay must run its listener). Sessions in different
   permission modes may not get each other's messages;
2. opens the deep link `claude://resume?session=<uuid>`, which imports it into the app, and waits
   for the app log line `as Desktop session local_<uuid>`;
3. records it, and prints the app tool calls only a session can make: `set_session_title` with
   `relay_title` (default "Don't touch it, relay"; leave that session alone), and
   `set_session_model` / `set_session_permission_mode` if the app shows something else.

Then `save-clear-relay request` queues the resume, sees the new relay is not listening yet, and the
skill wakes it with `send_message` (the relay starts listening only after `RELAY listen ...`).

`ensure` is idempotent: a relay that exists and is not archived is used as is (one you set up
yourself keeps its title), a lock keeps two sessions from creating two relays, and a failed
creation is not retried for an hour (`relay.log` has the reason). It never runs in terminal
sessions (exit 5) or with `relay: off` (exit 3). The app ignores archived sessions, so an archived
relay is shown by `status` (`"archived": true`), refused by `setup` and `request`, and replaced by
`ensure` (the old one stays archived). The app's session files are read from
`~/Library/Application Support/Claude/claude-code-sessions` on macOS (`SAVE_CLEAR_APP_SESSIONS`
overrides it).

Manual setup (instead of the automatic one): in an app session you keep for this, ask Claude to run
`python3 <plugin folder>/bin/save-clear-relay setup`, then send the message it prints
(`RELAY listen. Run ... rules and follow the cycle from step 1.`).

A request stays pending until the relay acknowledges it with `save-clear-relay ack <name> ok` after
`send_message`. If the relay reports a failure, or its turn is lost before the ack, the request is
handed out again (after 10 minutes without an ack), at most 3 times. After that it is kept as failed
and `status` lists it. A lost ack can make the resume request arrive twice.

**Remote Control.** The app does not clear a session while its Remote Control is on. When it was
switched on in the app (by you or a tool), `request` sees it in the app's session file and, only while
the relay is listening, prints `RC-OFF-SELF`: the session turns its own Remote Control off right before
`clear_session`, and the relay turns it back on (`set_remote_control ... enabled: true`, then
`save-clear-relay rc-on-ack`) right after it sends the resume. If the clear never shows up in the app
log within 3 minutes (`SAVE_CLEAR_RELAY_RC_MAX`), the relay turns Remote Control back on anyway; the
resume keeps waiting for the clear (up to 15 minutes). It turns it off only when the listener beat in
the last 15 seconds. A session started from Remote Control (phone or claude.ai) has its switch locked by the app,
so it is never cleared by a tool: `request` prints `RC-LOCKED` and the skill says "You can /clear now."
When the relay is not listening it prints `RC-KEEP` and nothing is turned off.

Other commands:

- `save-clear-relay status`: relay id, whether it is listening, whether the app shows it archived
  (with its title, model and permission mode), how many requests are queued,
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
/plugin uninstall save-clear@lucas-tools
```

Your summaries stay in the snapshot folder. Claude Code deletes the plugin's data folder on
uninstall, which is where the state lives by default. If you set `SAVE_CLEAR_STATE_DIR`, or the
state ended up in `~/.claude/state/save-clear`, delete that folder yourself, plus the pointer file
`~/.claude/state/save-clear.path`. Archive the relay session if you made one. If you scheduled the
idle trigger, run `./install.sh --uninstall` first (on Linux, remove the cron line).

## Limits

- Tested: the hooks and commands, with simulated input (`python3 tests/test_hooks.py`, 139 tests,
  also under Python 3.8), and the tmux path against a fake Claude in a separate tmux server
  (`bash tests/run.sh`, 32 checks). The `claude --bg` helper is tested against a fake tmux and a
  fake job state file. CI runs both on Ubuntu and macOS. On Windows, CI runs the unit tests
  only, and skips the ones for the automatic `/clear`, which needs tmux. Install and uninstall were run with the
  `claude plugin` CLI.
- The automatic relay setup (`ensure`) is tested with a fake `claude -p`, a fake deep-link opener
  and fake app session files. Not tested live: that a session imported this way honors the
  `--model` and `--permission-mode` given to `claude -p` (the `DO` lines correct it if the app shows
  otherwise), and the deep link on Linux and Windows.
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

## Tests

```
python3 tests/test_hooks.py -v
bash tests/run.sh        # tmux integration on a separate tmux server; skips without tmux
```

## License

MIT

Built with Claude Code
