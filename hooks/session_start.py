#!/usr/bin/env python3
"""SessionStart hook: hand the saved summary to the session that armed it (after its /clear).

Also copies the plugin options to the state folder, so the save-clear command (run from Bash,
where plugin options are not set) sees the same snapshot folder.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import resume  # noqa: E402
from common import RESUME_FLAG, log, now_ms, read_hook_input, save_config  # noqa: E402


def main():
    save_config()
    if not os.path.exists(RESUME_FLAG):
        return
    hook = read_hook_input()
    entry, _ = resume.take(hook, os.environ, now_ms())
    if not entry:
        return
    text = resume.resume_text(entry["snapshot"])
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
