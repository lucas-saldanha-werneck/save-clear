# save-clear

[![CI](https://github.com/lucas-saldanha-werneck/save-clear/actions/workflows/test.yml/badge.svg)](https://github.com/lucas-saldanha-werneck/save-clear/actions/workflows/test.yml)
[![License](https://img.shields.io/github/license/lucas-saldanha-werneck/save-clear)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.16.1-blue)](CHANGELOG.md)
[![save-clear score](https://img.shields.io/badge/skill%20score-98.8%20A-brightgreen)](https://github.com/NVIDIA/SkillEvaluator)
[![plugin score (proposal)](https://img.shields.io/badge/plugin%20score%20(proposal)-97.5%20A-brightgreen)](https://github.com/NVIDIA/SkillEvaluator/issues/508)

Say "save and clear" at the end of a Claude Code session. Claude writes the decisions, findings and
next steps to a summary file, checks that the file is on disk, clears its own context, and the next
session starts from that summary. You keep what matters from a long session without doing the save
and the `/clear` by hand.

[![save-clear demo: tap to open the video](https://github.com/user-attachments/assets/c69f7507-4d92-49b4-9298-57287662640e)](https://lucas-saldanha-werneck.github.io/save-clear/demo.mp4)

<table>
<tr>
<td align="center"><img src="https://github.com/user-attachments/assets/9a8ee53c-06f2-4fbd-a0e9-4c687810b861" alt="The terminal when the context passes the cap" width="320"><br>In the terminal</td>
<td align="center"><img src="https://github.com/user-attachments/assets/b9633c73-ce7e-402c-9392-baad5e6c1168" alt="The desktop app after saying save and clear" width="320"><br>In the desktop app</td>
<td align="center"><img src="https://github.com/user-attachments/assets/ffbdd8ef-f84f-4adc-bdbf-42380f2cf598" alt="Summary: you focus on the work, it handles the context" width="320"><br>The idea in short</td>
</tr>
</table>

You do not even have to ask: when the context passes 150k tokens, Claude runs
it by itself at the end of the turn. Change the number with `context_cap_tokens`, or set it to `0`
to turn it off.

Optionally, a session you leave idle for 50 minutes can run it by itself too, before the prompt cache
expires (see [Idle trigger](#idle-trigger-optional)). That one is off until you schedule it.

Optionally, the summary is also published as a private claude.ai page, and the next session's first
line is its link: `Previous conversation: https://claude.ai/artifact/...`. The old conversation stays
one tap away, which helps on the phone, where the chat is wiped by the `/clear`. Turn it on with
`publish_link` (needs the Artifact tool).

## What Claude sees

After the `/clear`, the plugin's SessionStart hook adds the summary to the new session's context. While that first turn runs, a PreToolUse hook denies `AskUserQuestion` (the question dialog hides the summary on a phone); the next message lifts the block. If a decision is waiting, Claude flags it with `save-clear decision-waiting`; when the summary turn ends, save-clear sends a second message ("save-clear: pending questions") by itself, typed into the tmux pane in a terminal or delivered by the relay in the app, and Claude asks the decision then. Nothing is sent when no decision is waiting, when you already typed something, or when the prompt or session is not provably free. It is sent once.
You see one line:

```
save-clear: summary of the previous session loaded. Send any message (e.g. ok) to see it.
```

Claude sees this (output of a real run, path shortened):

```
## Previous session (auto-resume)

Snapshot: `/home/you/.claude/snapshots/SESSION-SUMMARY-20261108-101500-billing.md`

ORDER: your first turn after the /clear ends with the summary as a plain TEXT message and NO AskUserQuestion (or any other question dialog) in that turn. On a phone (Remote Control) and in the app, the question dialog hides the text written before it, and the user never sees the summary. If a decision is waiting (something the user has to choose or approve), do NOT ask it now and do not announce it in the text. Before writing the summary, run once: `python3 ".../bin/save-clear" decision-waiting <session id>` (nothing else: no extra text about it). When the turn ends, save-clear sends you a second message by itself ("save-clear: pending questions"); ask the waiting decisions then, with AskUserQuestion. If no decision is waiting, do not run it: nothing follows the summary.

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
| `context_cap_tokens` | `SAVE_CLEAR_CAP_TOKENS` | `150000` | When the context passes this many tokens, Claude runs save-clear at the end of the turn. `0` = off. A long conversation re-reads its whole context on every turn, so clearing around 150k saves usage. Before 0.13.0 the default was `300000` |
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
| `confirm_seconds` | `SAVE_CLEAR_CONFIRM_SECONDS` | 6 | Terminal (tmux) only: before the automatic `/clear`, Claude asks "clear now?" and goes ahead by itself after this many seconds without an answer. Empty = 6, `0` = no question. See [Ask before the clear](#ask-before-the-clear) |
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
- In T3 Code: its `t3` CLI (the app sets `T3CODE_CLI_PATH`; otherwise `t3` on PATH).
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
| [T3 Code](https://github.com/pingdotgg/t3code) | A T3 thread cannot be cleared, so save-clear starts a new thread in the same project with the T3 CLI (`t3 thread start`, same provider and model, title "<old title> (continued)") and the summary goes to that thread only. The old thread stays as it is; Claude renames it "(continued in <new thread>)" when T3's `t3_thread_update` tool is there. While a background subagent of the old thread is still at work, the move waits for it (its result can only come back to the old thread). If the T3 CLI is missing or fails, Claude tells you why and you start the new thread yourself. |
| Claude desktop app | Claude calls the app's `clear_session` tool with `session_id: "self"`. The summary arrives with your next message, or right away if you set up the relay. While a background subagent of the session is still at work, the clear waits for it (see [Subagents still running](#subagents-still-running)). |

If you send a message after the save, the Stop hook does not clear. It asks Claude to answer it, add
it to the summary under `## After the save`, and run `save-clear rearm <session id>`. A message still
waiting in the queue counts too, even one queued before the save. The request is re-armed at most
once in all (for a message, a subagent result or a busy session): the next time, the clear is called
off with no loop. Claude still writes what came after into the summary, does not re-arm, and says the
clear waits for your word ("save-clear", or `/clear`). The clear is also called off when the answer to
a message sent after the save is long (1500 characters or more, `SAVE_CLEAR_LONG_REPLY_CHARS`): you
are probably reading it. The helper checks for new messages
again right before it presses Enter on `/clear`; if one arrived, it erases the `/clear` it typed,
does not press Enter, and re-arms the request for the next end of turn.
In the desktop app, a PreToolUse hook holds the first `clear_session` call the same way.

A second Stop hook watches the context size. When Claude ends a turn and the last reply used more
than `context_cap_tokens` (input plus cache tokens, subagents not counted), it blocks the stop once
and asks Claude to run save-clear in "save and clear" mode. It never acts in the middle of a task.
It asks again only after the context grows another 50k tokens, or after the context drops below the
limit (for example after a compaction) and crosses it again. It skips `claude -p` and SDK sessions,
a session that already armed its `/clear` or armed a resume in the last 15 minutes, and the desktop
app relay session. It also waits while a message from you sits in the queue (answering it comes
first), and right after a long reply (1500 characters or more of Claude's text since your last
message; you are probably reading it): it asks at the end of a shorter turn, at most 3 turns later.

### Ask before the clear

An automatic `/clear` can wipe the screen while you are still reading the last answer. By
default (`confirm_seconds` empty = 6 seconds; set `0` to turn it off), Claude asks first, with
AskUserQuestion: "clear now?", options "go ahead" and "wait". Since 0.19.0 the question comes BEFORE
the save: the skill's step 0 runs `save-clear ask`, which starts the helper and tells Claude to ask.
"Wait" then saves nothing and clears nothing, and the screen stays as it is while you decide. After
"go ahead", `arm-clear` does not ask a second time. (If step 0 was skipped, `arm-clear` asks after the
save, as in 0.18.)

- No answer in that many seconds: a helper (`bin/save-clear-confirm`) presses Enter in the tmux pane,
  which picks "go ahead", and the `/clear` follows as usual. Claude Code's own question timeout cannot
  do this: its shortest wait is 60 s and it never counts down while Remote Control is connected.
- "Wait" before the save: nothing is saved or cleared; say "save and clear" when you are done.
- "Wait" after the save (the `arm-clear` question): Claude runs `save-clear disarm`. Nothing is cleared, the summary stays
  saved, and the resume stays armed. Say "save and clear" (or just "clear") when you are done; Claude
  then runs `save-clear arm-clear --now`, which does not ask again.
- The helper presses nothing when the dialog is gone (you answered, in the terminal or from a phone)
  or changed (you moved the selection or started to type). It finds the dialog by its header,
  `save-clear`, and compares only the question block, so a clock elsewhere on the screen is ignored.

This works only in a terminal session running inside tmux. In the desktop app (and in a terminal
without tmux) nothing can press a key for you, so there the clear works as before, with no question.
On by default; set `confirm_seconds` to `0` to turn it off.

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

A background subagent (Agent tool) is work in progress, not a leftover. What a clear does to it depends
on where the session runs.

**Desktop app: the clear waits for it.** `clear_session` does not clear in place: when the turn ends,
the app ends the session's process and starts a new one (the new session's SessionStart says `startup`,
not `clear`), and every subagent still running dies with the old process. Seen on Claude Code 2.1.330
on 22/11/2026: four subagents stopped mid-work at the automatic clear (one of their shell commands was
killed with exit code 137, the new process started 7 seconds after their last write), no result ever
arrived, and `SendMessage` to them failed in the new session. Up to 0.16.1 save-clear cleared anyway,
because it took the terminal behavior below for granted. Since 0.17.0:

- A background subagent counts as still at work when its `agent-<id>.meta.json` (Claude Code writes it
  under `<project>/<session id>/subagents/`) has neither `finished` nor `stoppedByUser`, no task
  notification for it is in the transcript, and it wrote something after this session's process started
  (`CLAUDE_PID`, `ps` elapsed time; without `ps`, as on Windows: active in the last 2 hours). One with no
  write for 30 minutes (hung), or whose transcript ends with "[Request interrupted by user" (stopped with
  TaskStop, which writes neither key), does not count. One that finished in the last 2 minutes and whose
  newest notification Claude has not read yet holds the clear once, so Claude reads it first.
- A subagent continued after its result (`SendMessage`, or resumed by you from its transcript view)
  counts as at work again when its own transcript was written more than 10 seconds after its newest
  notification: Claude Code keeps `finished` in its meta and the first notification in the transcript
  (in real files on 23/11/2026, 101 of 112 subagents with several notifications worked between them).
  A foreground subagent counts only in that case, when it was written after its tool result.
- The check also covers the conversation before a `/clear` you typed in the app: that `/clear` keeps the
  process (SessionStart `clear`, seen twice in the save-clear log on 11/11/2026), its subagents run on
  under the earlier session id, and the app's session file still names that id (`cliSessionId`).
- Only desktop app sessions wait (a `local_` host id and the `clear_session` tool); IDE and SDK sessions
  keep the old "type /clear" step. A session started from Remote Control does not wait either: the app
  never lets a tool clear it (`RC-LOCKED`, "You can /clear now."), and the `/clear` you type keeps the
  process and its subagents.
- `save-clear arm-clear` checks first. With a subagent at work it prints `subagents running: ...`, and
  Claude ends the turn without the relay steps or `clear_session`.
- The clear-guard hook refuses `clear_session` on the same check, as often as needed (also when a
  subagent started after `arm-clear`), and records a hold in `app-wait/<session id>.json`. It also takes
  the session's resume request out of the relay queue (the clear did not happen), and puts it back when
  the clear goes through.
- Each result arrives as a notification and Claude adds it under `## After the save`. At the end of the
  first turn with none left, the Stop hook blocks once and asks Claude to finish the clear: relay
  `ensure` and `request` again, then `clear_session`. The relay delivers only the newest resume request
  of a session, so the request made before the wait is not sent twice. If you wrote after the hold, the
  Stop hook does not clear: it shows a note, and you say "save-clear" when you want it. A hold older than
  12 hours lets the clear through; a hold left by an earlier process (the app quit during the wait) is
  dropped. When Claude calls `clear_session` on its own once none is left, with Remote Control on (the
  hold asked to turn it back on), the guard refuses once and asks for relay `ensure` and `request` again,
  so the `RC-OFF-SELF` step comes back.
- Safety net: when a clear cuts subagents off anyway (one made outside this check), the next session's
  resume lists each one that never finished and was last active before the new process started (in the
  last 30 minutes, or named in the snapshot), with its transcript, and asks Claude to see how far it got
  and start it again if the work is still needed, never repeating something that cannot be undone
  without checking first.

**Terminal: it survives the clear.** A `/clear` in a terminal (tmux, `--bg`, or typed by you) runs in
the same process: the subagent keeps running and its result arrives in the new conversation as a
notification (watched on 2.1.322, and again on 2.1.330 on 23/11/2026: a subagent busy for 50 seconds
across a typed `/clear` wrote its file and reported to the new conversation). save-clear does not wait
for it. While one runs, `claude agents` reports the session as `busy` even after the turn ended with
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
  `save-clear rearm <session id> --subagents`. This counts toward the single re-arm, like your own
  messages: a second result after that calls the clear off.
- The stop-tasks instruction (`kill_background_tasks`) says never to stop a subagent.
- A subagent's permission prompt (or any open dialog) makes `claude agents` say `waiting`. The helper
  never types then (keys would answer the dialog); it waits until the prompt is answered, then clears.
- A result that starts a turn while `/clear` sits in the prompt: the helper sees the turn begin before
  pressing Enter, erases `/clear` (only when the prompt holds exactly that) and does not press Enter.
  A result whose turn ran while the helper held the request is caught before the next `/clear` try:
  Claude is asked to record it under `## After the save` first. All three were seen live on 2.1.322.
- The slash-command menu under the input box marks its item with the same sign as the prompt; the
  helper now reads the input box itself, so a typed `/clear` with the menu open is still recognized.
- The skill asks Claude to hand long work to background subagents: in a terminal they survive the
  clear, and in the desktop app the clear waits for them, so their work is never cut off.

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

**It wakes itself after an app restart.** Quitting or restarting the app kills the relay's listener, and
nothing messages the relay afterwards. So the SessionStart hook of every app session asks
`save-clear-relay wake-claim` whether the listener has been dead for 5 minutes (`SAVE_CLEAR_RELAY_WAKE_AFTER_S`).
If so, and the relay is configured, not archived, not turned off and not the starting session itself, that one
session gets a silent instruction to make one `send_message` call to the relay with the wake text. At most one
wake per 10 minutes (`SAVE_CLEAR_RELAY_WAKE_COOLDOWN_S`), claimed atomically, so many sessions opening together
send one. Terminal sessions never do this. `relay.log` records each claim.

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

- Tested: the hooks and commands, with simulated input (`python3 tests/test_hooks.py`, 226 tests,
  also under Python 3.8), and the tmux path against a fake Claude in a separate tmux server
  (`bash tests/run.sh`, 43 checks). The `claude --bg` helper is tested against a fake tmux and a
  fake job state file. CI runs both on Ubuntu and macOS. On Windows, CI runs the unit tests
  only, and skips the ones for the automatic `/clear`, which needs tmux. Install and uninstall were run with the
  `claude plugin` CLI.
- The automatic relay setup (`ensure`) is tested with a fake `claude -p`, a fake deep-link opener
  and fake app session files. Not tested live: that a session imported this way honors the
  `--model` and `--permission-mode` given to `claude -p` (the `DO` lines correct it if the app shows
  otherwise), and the deep link on Linux and Windows.
- The subagent hold (0.17.0) was run live on Claude Code 2.1.330 in throwaway terminal sessions (own
  tmux server, no user settings), with a fake `ccd_session_mgmt` MCP server whose `clear_session` ends
  the session's process group 20 seconds later, as the app did. With 0.16.1 the clear went through
  while a 50-second background subagent ran, and the subagent never finished. With 0.17.0 the
  clear-guard held it, the subagent wrote its file, and the clear came after (once through the Stop
  hook's request, once with Claude calling `clear_session` again on its own). In the same setup a
  `/clear` typed in a terminal left a running subagent alive. Not run: a real desktop app clear with
  0.17.0. A `/clear` typed in the app kept the process twice in the save-clear log (11/11/2026, one of
  them in a session started from Remote Control); not watched with a running subagent.
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

## Security

See [SECURITY.md](SECURITY.md) for how to report a vulnerability and what this plugin accesses.
