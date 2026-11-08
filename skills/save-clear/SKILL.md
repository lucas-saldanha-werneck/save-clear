---
name: save-clear
description: End-of-session ritual. Saves persistent memory and a verified SESSION-SUMMARY snapshot, arms the auto-resume, and (in "save and clear" mode) clears the context by itself so the next session starts from the summary. Use whenever the user says "save and clear", "save-clear", "salvalimpa", "checkpoint", "save the session", "wrap up the session", "prepare for /clear", "can I clear?", or when the context is close to full and state must be preserved. Also for "what needs to survive this session?".
---

# save-clear

Golden rule: **nothing important may live only in the context.** The next session (or the resume
after /clear) must not rediscover anything.

Two modes:
- **"save" / "checkpoint"**: steps 1–7. The user types /clear.
- **"save and clear" / "save-clear" / "salvalimpa" / "wrap up the session"**: steps 1–8. The /clear
  happens by itself when possible.

The command used below is `python3 "${CLAUDE_PLUGIN_ROOT}/bin/save-clear"` (written `save-clear` from here on).

## Steps

1. **List what must survive.** Go through the session: decisions (with the why), state that was
   actually **verified** (not what was planned), next steps, open items, paths/commands/IDs that were
   costly to find. Turn relative dates ("yesterday", "next week") into absolute dates. An open item
   inherited from an earlier summary goes in only after you checked it against the real state (file,
   log, process): done → leave it out; could not check → mark it "(not checked)". Items the user
   postponed go in with their trigger ("before the meeting").

2. **Update persistent memory, if the project has it.** Claude Code's auto memory lives in
   `~/.claude/projects/<project>/memory/` with a `MEMORY.md` index. If that folder exists:
   update an existing file on the same topic before creating a new one; a new fact = a new file with
   frontmatter (name, description, type) + one line in `MEMORY.md`. Memory keeps the durable
   (decisions, rules, state); the episodic goes in the snapshot. No memory folder → skip this step.
   Do not edit `CLAUDE.md` unless the user asked.

3. **Write the snapshot.** Run `save-clear dir`; it prints the snapshot folder (first line) and
   whether `publish_link` is on. Write `<folder>/SESSION-SUMMARY-<YYYYMMDD-HHMMSS>-<short-slug>.md`:

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
   ```

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

   Check that it prints `auto-resume armed: ...`; a non-zero exit means it did not arm (say so). The
   SessionStart hook delivers the summary only to THIS session after its /clear (terminal: same
   process; desktop app: same folder and same app session). Other sessions get nothing. If the user
   says they will NOT continue this topic, skip this step.

7. **Reply in this fixed, short format:**

   > Saved: memory (<files touched, or "none">), summary in `<snapshot path>`, auto-resume armed. You can /clear now.

   In save-and-clear mode, end with "clearing by itself in a moment." instead, but only when step 8
   armed an automatic clear.

8. **Save-and-clear mode: arm the automatic /clear.** Only when asked, and ALWAYS with step 6 armed.
   Run:

   ```bash
   save-clear arm-clear
   ```

   It prints `save-clear: mode=<mode> ...`. What to do per mode:
   - **`tmux`, `iterm` or `bg`**: armed. End the turn right away with the short reply from step 7. Run
     nothing else after it. The Stop hook waits until the session is idle with an empty prompt, types
     `/clear`, confirms it took (same process, new session id) and then types the resume request.
   - **`manual`** (a terminal without tmux or iTerm2): nothing can type for you. Reply with the
     "save" form of step 7: "You can /clear now." The summary comes back by itself after the /clear.
   - **`app`** (Claude desktop app, IDE, SDK): the flag is NOT written, because nothing must type into a
     terminal that may belong to another session. If the `mcp__ccd_session_mgmt__clear_session` tool
     exists (load it with ToolSearch if it is only listed): write the step 7 reply first, then call it
     with `session_id: "self"` as the very last action. The app clears only this session when the turn
     ends; the resume arrives with the user's next message. If the tool is missing or refuses, reply
     "You can /clear now."

   **A user message during the save.** If one arrives while you save, answer it and put it in the
   snapshot before step 8. If it arrives after step 8, the Stop hook does not clear: it blocks and asks
   for three things: (1) answer it; (2) add to the snapshot a section `## After the save` with only what
   came after; (3) run `save-clear rearm`. In the desktop app the clear-guard hook holds the first
   `clear_session` call the same way; answer, add the section, and call `clear_session` again in the
   same turn (the second call goes through).

## Safety nets (no ritual needed)

- **PreCompact**: before any compaction, the full transcript is saved gzipped with an
  `EMERGENCY-COMPACT-<ts>-<sid>.md` marker in the snapshot folder.
- **PostCompact**: the summary Claude Code wrote is kept as `COMPACT-SUMMARY-<ts>-<sid>.md`.
- On resume, a curated SESSION-SUMMARY is worth more than these. An EMERGENCY-COMPACT newer than the
  project's last SESSION-SUMMARY means the session compacted without a save: treat it as NOT verified.
