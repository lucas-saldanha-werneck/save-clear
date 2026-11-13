---
name: save-clear
description: >-
  End-of-session ritual. Saves persistent memory and a verified SESSION-SUMMARY snapshot, arms the auto-resume, and (in "save and clear" mode) clears the context by itself so the next session starts from the summary. Use whenever the user says "save and clear", "save-clear", "salvalimpa", "checkpoint", "save the session", "wrap up the session", "prepare for /clear", "can I clear?", or when the context is close to full and state must be preserved. Also when a message starts with "save-clear (automatic" (the context limit or the idle trigger asking for it): run it in "save and clear" mode. Also for "what needs to survive this session?".
metadata:
  author: Lucas Saldanha Werneck <lucas@2olhares.com>
---

# save-clear

Golden rule: **nothing important may live only in the context.** The next session (or the resume
after /clear) must not rediscover anything.

Two modes:
- **"save" / "checkpoint"**: steps 1–7. The user types /clear.
- **"save and clear" / "save-clear" / "salvalimpa" / "wrap up the session"**: steps 1–8. The /clear
  happens by itself when possible.

The command used below is `python3 "${CLAUDE_PLUGIN_ROOT}/bin/save-clear"` (written `save-clear` from here on).

## Instructions

1. List what must survive the session (decisions, verified state, next steps, open items).
2. Update the project's persistent memory, then write the snapshot with `save-clear dir`.
3. Verify the files on disk, then arm the auto-resume with the command in step 6.
4. In "save and clear" mode, also arm the automatic /clear (step 8); the next session starts from the summary.

The numbered steps below are the full procedure.

## Steps

1. **List what must survive.** Go through the session: decisions (with the why), state that was
   actually **verified** (not what was planned), next steps, open items, paths/commands/IDs that were
   costly to find. Turn relative dates ("yesterday", "next week") into absolute dates. An open item
   inherited from an earlier summary goes in only after you checked it against the real state (file,
   log, process): done → leave it out; could not check → mark it "(not checked)". Items the user
   postponed go in with their trigger ("before the meeting"). Subagents this session started that are
   still running (Agent tool, background) are work in progress, not leftovers: never stop them for the
   save. They survive the /clear and their results arrive in the new conversation, so list each one
   under `## Running subagents` (step 3) with enough to act on its result there.

2. **Update persistent memory, if the project has it.** Claude Code's auto memory lives in
   `~/.claude/projects/<project>/memory/` with a `MEMORY.md` index. If that folder exists:
   update an existing file on the same topic before creating a new one; a new fact = a new file with
   frontmatter (name, description, type) + one line in `MEMORY.md`. Memory keeps the durable
   (decisions, rules, state); the episodic goes in the snapshot. No memory folder → skip this step.
   Do not edit `CLAUDE.md` unless the user asked.

3. **Write the snapshot.** Run `save-clear dir`; it prints the snapshot folder (first line) and
   whether `publish_link` is on. If it also prints `language: <L>`, write the snapshot (headings
   included) and your replies in <L>. If it prints an `extra steps` block, those are the user's own
   steps for this ritual: follow them, at the point they name (no point named = before step 6). If it
   prints a line starting with `kill_background_tasks: on` and this is save-and-clear mode, do what that
   line says now: stop the background tasks THIS session started that are still running (TaskStop with
   each task id) and list them under `## Stopped background tasks` in the snapshot (that heading stays in
   English, exactly, even when the snapshot is in another language: the resume looks for it). Never stop anything
   this session did not start, and never stop a subagent (Agent tool task): it survives the /clear. That option off (no such line) = leave background tasks alone.
   Write `<folder>/SESSION-SUMMARY-<YYYYMMDD-HHMMSS>-<short-slug>.md`:

   ```markdown
   ---
   session_id: <this session's id: $CLAUDE_CODE_SESSION_ID>
   previous_snapshot: <the snapshot that opened this session through auto-resume; else the newest SESSION-SUMMARY of the same project in the folder; null if this is the first>
   project: <short project/topic slug>
   summary_url: <only if step 3b published one>
   ---
   # Session <date> - <topic>
   ## Last topic (REQUIRED, always first: what we were doing in the last minutes, where it stopped, and the open question or test with the user. The resume starts here. If the last reply carried a result - search, number, table, link - paste the essentials here: the /clear wipes the screen before the user reads it)
   ## What was done
   ## Findings (what we learned that is not obvious: confirmed cause, measured number, what worked)
   ## What failed (approaches tried that did NOT work, and why; one line "nothing relevant" if none)
   ## Verified state (what was checked on disk or in production, with the proof)
   ## Next steps (in order)
   ## Open items / risks
   ## Useful paths, commands and IDs
   ## Running subagents (ONLY when subagents of this session are still running. One entry each: agent id; description; brief summary - goal, repo/worktree/branch, what it will deliver, whether it merges or installs by itself; brief path; what to do when its result arrives)
   ## Stopped background tasks (ONLY when kill_background_tasks is on: one line per task - id, what it did, restart needed? - or "none")
   ```

   Both headings stay in English, exactly, whatever the snapshot's language: the resume looks for them.

   The frontmatter makes a **chain of snapshots**: each points to the previous one, so a multi-session
   project's history can be rebuilt.

   3b. **Optional link.** Only if `save-clear dir` printed `publish_link: on` and the Artifact tool is
   available: publish the snapshot as a private page and put its URL in `summary_url:`. The next session
   shows that link first, which helps when the user reads from a phone (the /clear wipes the screen).
   If it fails, go on without the line and note the error under "What failed".

4. **If the folder is a git repo:** record the last commit hash and whether the tree is clean. Do not
   commit or push unless asked.

5. **Verify before claiming.** Re-read the files you wrote (memory + snapshot) and confirm the content
   is there. Report only after that.

6. **Arm the auto-resume.** Run (do not write the flag by hand):

   ```bash
   save-clear arm-resume "<absolute path of the snapshot>"
   ```

   Check that it prints `auto-resume armed: ...`; a non-zero exit means it did not arm (say so). If the
   user set an `on_save_command`, it runs first and a line `on_save_command: ...` says what it added
   to the snapshot (a failure there is only noted; the resume is still armed). The
   SessionStart hook delivers the summary only to THIS session after its /clear (terminal: same
   process; desktop app: same folder and same app session). Other sessions get nothing. If it prints
   a warning that the summary is NOT delivered automatically, tell the user to ask you to read the
   snapshot after the /clear. If the user says they will NOT continue this topic, skip this step.

7. **Reply in this fixed, short format:**

   > Saved: memory (<files touched, or "none">), summary in `<snapshot path>`, auto-resume armed. You can /clear now.

   In save-and-clear mode, end with "clearing by itself in a moment." instead, but only when step 8
   armed an automatic clear.

8. **Save-and-clear mode: arm the automatic /clear.** Only when asked, and ALWAYS with step 6 armed.
   Run:

   ```bash
   save-clear arm-clear
   ```

   It prints `save-clear: mode=<mode> ...`. If it also prints a `kill_background_tasks: on` line and a
   background task of this session is still running, stop it now (before ending the turn or calling
   `clear_session`) and add it to the snapshot section. What to do per mode:
   - **`tmux` or `bg`**: armed. End the turn right away with the short reply from step 7. Run
     nothing else after it. The Stop hook starts a helper that types `/clear` and then the resume
     request. It types only into an idle session with an empty prompt, checks again right before
     each Enter, and types the resume request only after it confirmed the clear took.
   - **`manual`** (a terminal without tmux, including iTerm2): nothing can type for you. Reply with the
     "save" form of step 7: "You can /clear now." The summary comes back by itself after the /clear.
   - **`app`** (Claude desktop app, IDE, SDK): the flag is NOT written, because nothing must type into a
     terminal that may belong to another session. If the `mcp__ccd_session_mgmt__clear_session` tool
     is missing (load it with ToolSearch if it is only listed), reply "You can /clear now." and stop.
     Otherwise:
     1. **Make sure the relay exists.** A session cannot message itself, so the resume comes from one
        helper app session, the relay. Run `python3 "${CLAUDE_PLUGIN_ROOT}/bin/save-clear-relay" ensure`
        (it can take up to a minute the first time: it creates the relay session):
        - exit **0**: a relay exists. If it printed `DO <n>:` lines, make each of those app tool calls
          now, in order (load them with ToolSearch if only listed); a failed one is only noted. Never
          create a relay any other way: `ensure` is the only creator, and it never makes two.
        - exit **3** (off), **5** (not an app session) or **6** (could not create it; reason printed):
          go on to the next step; it reports what to say.
     2. **Ask the relay for the resume.** Run
        `python3 "${CLAUDE_PLUGIN_ROOT}/bin/save-clear-relay" request`:
        - exit **0**: the relay is listening; it sends the resume right after the clear.
        - It may also print one Remote Control line (the app never clears a session while its Remote
          Control is on):
          - `RC-OFF-SELF`: do what it says in sub-step 3: turn this session's Remote Control off right
            before `clear_session`. The relay turns it back on after the resume.
          - `RC-KEEP` or `RC-LOCKED`: leave Remote Control alone, skip `clear_session`, and reply
            "You can /clear now." The summary comes back after the /clear.
        - exit **2**: queued, but the relay is not listening. Wake it: `send_message` to the relay id
          with the exact text printed on stderr ("no sign of starting within 20s" is a false alarm on a
          cold start). If that fails, say the resume arrives with the user's next message.
        - exit **3**: the relay was turned off on purpose. Do not wake it; say the resume arrives with
          the user's next message.
        - exit **4**: no usable relay (none, or archived in the app). Same: the resume arrives with the
          user's next message; say so, with the reason it printed.
        - exit **1** saying "this session IS the relay": stop here and follow only the "Context full"
          part of `python3 "${CLAUDE_PLUGIN_ROOT}/bin/save-clear-relay" rules`.
     3. Write the step 7 reply (after `RC-OFF-SELF`, add: "Remote Control drops for a moment and comes
        back with the summary."). Then, as the very last actions: if `request` printed `RC-OFF-SELF`,
        call `mcp__ccd_session_mgmt__set_remote_control` with `session_id: "self"`, `enabled: false`;
        then call `clear_session` with `session_id: "self"`. The app clears only this session when the
        turn ends. If `clear_session` refuses, turn Remote Control back on at once (`enabled: true`) when
        you turned it off, and change the reply to "You can /clear now."

   **Subagents still running.** Do not stop them and do not wait for them: a background subagent
   survives the /clear, and its result arrives in the new conversation as a notification. The
   `## Running subagents` section is what lets the next session understand that result, so fill it in
   fully. If a result reaches THIS conversation after the save but before the /clear, the Stop hook asks
   you to record it under `## After the save` and run the `save-clear rearm <session id> --subagents`
   command it gives. Stop a subagent only when the user asks.

   **Long work goes to background subagents.** During the session, hand long jobs (builds, long
   tests, research, anything over a few minutes) to background subagents (Agent tool with
   `run_in_background`), not to the main thread: subagents survive the /clear, so save-clear never
   has to wait for them.

   **Busy session (kill_background_tasks on).** If the helper found the session still busy, the Stop
   hook blocks once and asks you to stop this session's leftover background tasks (Bash, Monitor; never
   subagents) and list them in the snapshot. Do only that, end the turn with one short line, and the
   /clear follows.

   **A user message during the save.** If one arrives while you save, answer it and put it in the
   snapshot before step 8. If it arrives after step 8, the Stop hook does not clear: it blocks and asks
   for three things: (1) answer it; (2) add to the snapshot a section `## After the save` with only what
   came after; (3) run the `save-clear rearm <session id>` command it gives. In the desktop app the clear-guard hook holds the first
   `clear_session` call the same way; answer, add the section, and call `clear_session` again in the
   same turn (the second call goes through).

## Safety nets (no ritual needed)

- **PreCompact**: before any compaction, the full transcript is saved gzipped with an
  `EMERGENCY-COMPACT-<ts>-<sid>.md` marker in the snapshot folder (the 10 newest are kept).
- **PostCompact**: the summary Claude Code wrote is kept as `COMPACT-SUMMARY-<ts>-<sid>.md`.
- On resume, a curated SESSION-SUMMARY is worth more than these. An EMERGENCY-COMPACT newer than the
  project's last SESSION-SUMMARY means the session compacted without a save: treat it as NOT verified.

## Examples

- The user types "save and clear" at the end of a long session: Claude writes memory and the summary, checks them on disk, clears the context by itself, and the next session opens from the summary.
- The user types "checkpoint" (or "save-clear" without clearing): steps 1-7 only; the user types /clear when ready.
- The context passes the limit and a message starts with "save-clear (automatic": run it in "save and clear" mode without asking.
- The user asks "can I clear?": check what would be lost, save it, and answer in the fixed short format (step 7).
