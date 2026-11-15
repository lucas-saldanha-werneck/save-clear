#!/usr/bin/env python3
"""Keep the first turn after a save-clear /clear (the "resume turn") free of question dialogs.

On a phone (Remote Control) and in the app, a question dialog hides the text written before it, so the
user never sees the summary. SessionStart writes a marker (state/resume-turn/<session_id>.json) when it
delivers a resume. Two modes:

  ask     PreToolUse (AskUserQuestion, mcp__ccd_turn__wrap_up): while the marker exists, deny the question
          and tell Claude to write the summary as text and ask in the next turn.
  prompt  UserPromptSubmit: the resume message itself keeps the marker; any other user prompt deletes it.
          Continuations pushed by Stop hooks are not user prompts, so the block holds through them.

Never locks forever: a marker older than 12 h is deleted and ignored; unreadable state allows.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from common import is_resume_text, log as _log, now_ms, read_hook_input, read_json, resume_turn_marker  # noqa: E402

MAX_AGE_MS = 12 * 3600 * 1000
REASON = ("save-clear: this is the first turn after a /clear, and the user reads it on a screen where a "
          "question dialog hides the text before it. Do NOT ask now. Write the summary of the previous "
          "session as a plain text message and end the turn with no question and no AskUserQuestion. If a "
          "decision is waiting, say so in one line at the end and ask it in the next turn.")


def log(msg):
    _log("resume-turn", msg)


def live_marker(sid):
    """The marker's path when this session is in its resume turn, else None (stale markers are deleted)."""
    if not sid:
        return None
    path = resume_turn_marker(sid)
    if not os.path.exists(path):
        return None
    m = read_json(path)
    ts = m.get("ts") if isinstance(m, dict) else None
    if not isinstance(ts, int) or now_ms() - ts > MAX_AGE_MS:
        try:
            os.remove(path)
        except OSError:
            pass
        log(f"{sid[:8]}: stale or unreadable marker removed")
        return None
    return path


def ask(hook):
    if hook.get("tool_name") == "mcp__ccd_turn__wrap_up" and not (hook.get("tool_input") or {}).get("ask"):
        return  # a wrap_up without a question does not stop the turn
    if not live_marker(hook.get("session_id")):
        return
    log(f"{(hook.get('session_id') or '')[:8]}: denied {hook.get('tool_name')} in the resume turn")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": REASON}}))


def prompt(hook):
    path = live_marker(hook.get("session_id"))
    if not path or is_resume_text(hook.get("prompt") or ""):
        return
    try:
        os.remove(path)
    except OSError:
        pass
    log(f"{(hook.get('session_id') or '')[:8]}: user prompt, resume-turn block lifted")


def main():
    hook = read_hook_input()
    (ask if (sys.argv[1:] or ["ask"])[0] == "ask" else prompt)(hook)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"error: {e!r}")
    sys.exit(0)
