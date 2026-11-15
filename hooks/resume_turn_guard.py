#!/usr/bin/env python3
"""Keep the first turn after a save-clear /clear (the "resume turn") free of question dialogs.

On a phone (Remote Control) and in the app, a question dialog hides the text written before it, so the
user never sees the summary. SessionStart writes a marker (state/resume-turn/<session_id>.json) when it
delivers a resume. Two modes:

  ask     PreToolUse (AskUserQuestion, mcp__ccd_turn__wrap_up): while the marker exists, deny the question
          and tell Claude to write the summary as text and ask in the next turn.
  prompt  UserPromptSubmit: the resume message itself keeps the marker; any other user prompt deletes it
          (the 2nd message below included, which is what lets the questions through in turn 2).
          Continuations pushed by Stop hooks are not user prompts, so the block holds through them.
  stop    Stop: when the summary turn flagged a decision (`save-clear decision-waiting` sets "pending" in the
          marker), delete the marker and send the 2nd message FOLLOWUP_MSG once, through the same channel as
          the resume message: tmux (`save-clear-send`, same safety checks) in a terminal, the relay in the
          app. Not sent when the user typed something since the resume, or no channel is proven.

Never locks forever: a marker older than 12 h is deleted and ignored; unreadable state allows.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from common import (BIN, FOLLOWUP_MSG, STATE, app_host, is_resume_text, log as _log, now_ms,  # noqa: E402
                    read_hook_input, read_json, resume_turn_marker, tmux_bin, user_messages_since)

MAX_AGE_MS = 12 * 3600 * 1000
REASON = ("save-clear: this is the first turn after a /clear, and the user reads it on a screen where a "
          "question dialog hides the text before it. Do NOT ask now. Write the summary of the previous "
          "session as a plain text message and end the turn with no question and no AskUserQuestion. If a "
          "decision is waiting, run the `decision-waiting` command from the resume text (once) before ending "
          "the turn: save-clear then sends you a second message by itself, and you ask it there.")


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


def send_tmux(hook, m):
    """Terminal: hand the 2nd message to save-clear-send (same checks as the resume message). Returns why-not or ''."""
    pane = os.environ.get("TMUX_PANE") or ""
    sid = hook.get("session_id") or ""
    if not pane or not tmux_bin():
        return "no tmux pane to type into"
    try:
        chk = subprocess.run([sys.executable, os.path.join(BIN, "save-clear-send"), "--check", "tmux", pane, sid],
                             capture_output=True, text=True, timeout=7)
    except subprocess.TimeoutExpired:
        return f"pane {pane} could not be checked in time"
    if chk.returncode != 0:
        return chk.stdout.strip() or f"pane {pane} does not run this session"
    subprocess.Popen([sys.executable, os.path.join(BIN, "save-clear-send"), "tmux", pane, sid,
                      json.dumps({"followup": True, "ts": m["ts"]}), str(hook.get("transcript_path") or "")],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return ""


def send_relay(host):
    """App: queue the 2nd message in the relay inbox (the relay delivers it with send_message)."""
    relay = read_json(os.path.join(STATE, "relay.json"))
    if not isinstance(relay, dict) or not relay.get("local_id"):
        return "no relay configured"
    r = subprocess.run([sys.executable, os.path.join(BIN, "save-clear-relay"), "job", "--to", host,
                        "--message", FOLLOWUP_MSG, "--in", "3", "--why", "followup"],
                       capture_output=True, text=True, timeout=10)
    return "" if r.returncode in (0, 2) else (r.stderr.strip() or f"relay job exited {r.returncode}")


def stop(hook):
    sid = hook.get("session_id") or ""
    path = live_marker(sid)
    m = read_json(path) if path else None
    if not isinstance(m, dict) or not m.get("pending"):
        return
    try:  # once only: the marker is gone, so a later Stop finds nothing, and turn 2 may ask
        os.remove(path)
    except OSError:
        return
    if user_messages_since(hook.get("transcript_path"), int(m["ts"])):
        log(f"{sid[:8]}: the user wrote after the summary; 2nd message not sent")
        return
    host = app_host()
    try:
        why = send_relay(host) if host else send_tmux(hook, m)
    except Exception as e:
        why = repr(e)
    log(f"{sid[:8]}: 2nd message " + (f"NOT sent ({why})" if why else f"handed to the {'relay' if host else 'terminal helper'}"))


def main():
    hook = read_hook_input()
    mode = (sys.argv[1:] or ["ask"])[0]
    {"ask": ask, "prompt": prompt, "stop": stop}.get(mode, ask)(hook)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"error: {e!r}")
    sys.exit(0)
