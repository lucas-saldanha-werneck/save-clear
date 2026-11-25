# Changelog

Versions are the `version` in `.claude-plugin/plugin.json`. Newest first.

## 0.17.2 (2026-11-25)

- T3 Code (github.com/pingdotgg/t3code) support. T3 runs Claude Code in SDK mode (`CLAUDE_CODE_ENTRYPOINT=t3-code`, `T3CODE_THREAD_ID`, no tty), so `arm-clear` used to pick the desktop app mode and point Claude at the app relay and `clear_session`, which do not exist there. Now `arm-clear` picks a `t3` mode: it starts a new thread in the same project with the T3 CLI (`t3 thread start`, same provider and model as the current thread) and binds the summary to that thread id, so only that thread's SessionStart takes it. The first message is the resume request plus the snapshot path, as a fallback. A missing or failing CLI gives `manual` with the reason, never the app mode. Subagents still running hold the move (`t3-wait`): their results can only reach the old thread.
- T3 variables are ignored when inherited: under a terminal (a tmux pane or T3's own terminal), or in a `claude` started by another `claude` (`claude -p`, a robot). A tmux pane wins only when its check proves it runs this session, as with an inherited desktop app entrypoint.
- The desktop app relay refuses T3 sessions (`request` exit 4, `ensure` exit 5).

## 0.17.1 (2026-11-23)

Fixes from the review of 0.17.0 (desktop app subagent hold):

- A subagent continued after its result (`SendMessage`, or resumed from its transcript view) was taken for finished: its meta keeps `finished: true` and its first notification stays in the transcript, so the clear could stop it mid-work and the next resume did not list it. It now counts as at work when its transcript was written more than 10 seconds after its newest notification, and its result counts as read only when the newest notification was read (seen in real files: 101 of 112 subagents with several notifications worked between them).
- A foreground subagent continued with `SendMessage` after its tool result runs on after the turn with its meta still `foreground` (seen on 2.1.299). It now counts the same way, from its tool result.
- A `/clear` typed in the app keeps the process (SessionStart `clear`, seen twice on 11/11/2026), so subagents of the earlier conversation run on under the earlier session id. The hold now checks that conversation too (the app's session file still names it in `cliSessionId`).
- A session started from Remote Control no longer waits: the app never lets a tool clear it (`RC-LOCKED`) and the `/clear` typed there keeps the process, so the hold only delayed "You can /clear now." and forced an extra turn.
- When Claude calls `clear_session` on its own after a hold, with Remote Control on (the hold asked to turn it back on), the guard refuses once and asks for the relay steps again, so `RC-OFF-SELF` comes back. Before, the call went through with Remote Control on and the app did not clear.

## 0.17.0 (2026-11-23)

- Desktop app: the automatic clear no longer stops background subagents. `clear_session` ends the session's process when the turn ends (the new session starts with SessionStart `startup`), and every subagent still running died with it (seen on Claude Code 2.1.330: four subagents stopped mid-work, their results lost). Now `save-clear arm-clear` and the clear-guard hook check the session's subagents (Claude Code's `agent-<id>.meta.json`: not finished, not stopped, active since this process started; or finished less than 2 minutes ago with a result Claude has not read yet) and hold the clear while any is at work. At the end of the first turn with none left, the Stop hook asks Claude once to finish the clear. A hold older than 12 hours is dropped.
- The relay delivers only the newest resume request of a session, so asking again after the wait does not send the resume twice.
- The resume names the subagents a clear cut off anyway (never finished, last active before the new process started), with their transcripts, and asks Claude to check how far each one got and start it again when the work is still needed.
- Terminal: unchanged. A typed or automatic `/clear` keeps the process, so subagents keep running and report to the new conversation (checked again on 2.1.330).
- Skill and README: subagents survive the clear in a terminal only; in the desktop app the clear waits for them.
- Review fixes before release: only desktop app sessions (a `local_` host id) wait, not IDE or SDK sessions; a held clear takes its resume request out of the relay queue, and puts it back when the clear goes through; the Stop hook does not force the clear when the user wrote after the hold (it shows a note instead) or when the hold was left by an earlier process; a subagent with no write for 30 minutes, or stopped with TaskStop (its transcript ends with "[Request interrupted by user"), no longer counts as running; a finished subagent whose result was not read holds the clear only once; a hold older than 12 hours lets the clear through in the guard too. Hand-backs and compaction summaries no longer count as messages sent during the save. The `kill_background_tasks` line now comes right before the real clear, not at the start of the wait.
- Relay: an older resume request is retired before it can start its Remote Control timer, and passes its Remote Control step to the newer one; a request already claimed or delivered counts as newer, so an old one handed back is not sent twice. After `claude plugin update` the listener restarts from the newest installed version by itself, a newer listener replaces an older one, and app sessions wake the relay with the new rules while an older listener runs.

## 0.16.1 (2026-11-15)

- Without the `language` option, the resume now tells Claude to reply in the language the user writes in (the snapshot's language), not in English because the resume message is in English. The skill also says to write the snapshot in the user's language when no language is set. With `language` set, nothing changes.

## 0.16.0 (2026-11-15)

- The relay now wakes itself after the Claude app quits or restarts (before, its listener stayed dead until someone typed a wake message into it, and the resume and "pending questions" messages were lost). The SessionStart hook of any app session runs the new `save-clear-relay wake-claim`; when the listener has been dead for 5 minutes and the relay is usable, that session is told, silently, to make one `send_message` to the relay with the wake text. One wake per 10-minute cool-down (atomic claim, stamp file `relay-wake.stamp`), never from the relay session itself, never in terminal sessions, never when the relay is off or archived.

## 0.15.0 (2026-11-14)

- The questions now come right after the summary, with nothing typed by you. If a decision is waiting, Claude runs `save-clear decision-waiting` in the resume turn (it sets a flag in the session's resume-turn marker). When that turn ends, a Stop hook deletes the marker (so turn 2 may ask) and sends a second message, "save-clear: pending questions", once: in a terminal through `save-clear-send` (same checks as the resume message: right session, idle, empty prompt, Enter re-checked), in the app through the relay. Not sent when no decision is waiting, when you wrote something after the resume, or when the target is not proven. Claude then asks with AskUserQuestion.
- The 2nd message is never counted as one of your own messages.

## 0.14.2 (2026-11-14)

- The first turn after a resume can no longer call AskUserQuestion (or a wrap_up with a question): a PreToolUse hook denies it and asks Claude to write the summary as text and ask in the next turn. SessionStart writes a marker per session; the user's next prompt (not the resume message) lifts it, so Stop-hook continuations stay covered. A marker older than 12 h is ignored.

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
