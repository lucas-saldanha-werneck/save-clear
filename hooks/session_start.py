#!/usr/bin/env python3
"""SessionStart hook: hand the saved summary to the session that armed it (after its /clear).

Before that it runs the user's on_resume_command (if set) and adds what it prints.
Also creates your config file (~/.config/save-clear/config.json) from config.example.json when it
is missing, and copies the plugin options to the state folder, so the save-clear command (run from
Bash, where plugin options are not set) sees the same snapshot folder.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import resume  # noqa: E402
from common import (RESUME_FLAG, log, now_ms, read_hook_input, run_hook_command, save_config,  # noqa: E402
                    save_pointer, seed_user_config)


def main():
    seed_user_config()  # first run after install: create ~/.config/save-clear/config.json (never overwrites)
    save_pointer()
    save_config()
    if not os.path.exists(RESUME_FLAG):
        return
    hook = read_hook_input()
    entry, _ = resume.take(hook, os.environ, now_ms())
    if not entry:
        return
    # The user's on_resume_command runs after the /clear; what it prints goes into the context too.
    rc, extra = run_hook_command("on_resume_command", "resume", {
        "SAVE_CLEAR_SNAPSHOT": entry["snapshot"], "SAVE_CLEAR_SESSION_ID": hook.get("session_id") or "",
        "SAVE_CLEAR_PREVIOUS_SESSION_ID": entry.get("session_id") or "",
        "SAVE_CLEAR_PROJECT_DIR": hook.get("cwd") or ""}, timeout=5)
    text = resume.resume_text(entry["snapshot"], extra if rc == 0 else "")
    if not text:
        return
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text},
        "systemMessage": "save-clear: summary of the previous session loaded. Send any message (e.g. ok) to see it.",
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("resume", f"error: {e!r}")
    sys.exit(0)
