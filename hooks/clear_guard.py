#!/usr/bin/env python3
"""PreToolUse on the desktop app's clear_session tool (mcp__ccd_session_mgmt__clear_session).

In the Claude desktop app the skill ends with clear_session("self"). A message the user sent
DURING the save would vanish: its answer leaves the screen, or it runs after the turn and the
clear wipes it. This hook denies the clear ONCE when messages arrived after the skill loaded,
asks Claude to put them (and the answers) in the snapshot, and lets the second call through.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from common import (STATE, ensure_state, log as _log, read_hook_input, read_json, run_hook_command,  # noqa: E402
                    write_json)

GUARD_STATE = os.path.join(STATE, "clear-guard.json")  # transcript -> number of messages already asked about
SKILL_MARK = "/skills/save-clear"
WINDOW_S = 20 * 60  # without the skill marker: look at the last 20 min only


def log(msg):
    _log("clear-guard", msg)


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def is_noise(t):
    t = t.lstrip()
    return (not t or t.startswith("<") or t.startswith("Stop hook feedback")
            or t.startswith("Base directory for this skill") or t.startswith("[Request interrupted"))


def ts_epoch(o):
    try:
        import calendar
        return calendar.timegm(time.strptime((o.get("timestamp") or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def new_messages(entries):
    start = None
    for i, o in enumerate(entries):
        txt = text_of((o.get("message") or {}).get("content"))
        if o.get("type") == "user" and SKILL_MARK in txt and "Base directory for this skill" in txt:
            start = i
    if start is None:
        now = time.time()
        start = next((i for i, o in enumerate(entries) if (ts_epoch(o) or 0) >= now - WINDOW_S), len(entries))
    found = []
    for o in entries[start + 1:]:
        t = o.get("type")
        if t == "queue-operation" and o.get("operation") in ("enqueue", "remove"):
            c = o.get("content")
            if isinstance(c, str) and c.lstrip().startswith("<task-notification>"):
                continue
            if o.get("operation") == "remove" and o.get("reason") not in (None, "absorbed_mid_turn"):
                continue
            found.append(((o.get("timestamp") or "")[11:19], (c or "(queued message)")[:80]))
        elif t == "user":
            m = o.get("message") or {}
            if m.get("role") != "user":
                continue
            tx = text_of(m.get("content"))
            if not is_noise(tx) and "tool_result" not in json.dumps(m.get("content"))[:200]:
                found.append(((o.get("timestamp") or "")[11:19], tx[:80]))
    return found


def main():
    data = read_hook_input()
    if (data.get("tool_input") or {}).get("session_id") != "self":
        return
    if decide(data):  # the clear goes through: run the user's pre_clear_command first (never blocks)
        run_hook_command("pre_clear_command", "pre_clear",
                         {"SAVE_CLEAR_SESSION_ID": data.get("session_id") or "", "SAVE_CLEAR_MODE": "app",
                          "SAVE_CLEAR_TARGET": os.environ.get("CLAUDE_CODE_HOST_SESSION_ID") or ""}, timeout=10)


def decide(data):
    """True = allow the clear; False = denied once (the reason was printed)."""
    path = data.get("transcript_path") or ""
    try:
        with open(path, encoding="utf8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
    except Exception as e:
        log(f"no transcript ({e}); clear allowed")
        return True
    found = new_messages(entries)
    name = os.path.basename(path)[:8]
    if not found:
        log(f"{name}: no new messages; clear allowed")
        return True
    state = read_json(GUARD_STATE) or {}
    if state.get(path, 0) >= len(found):
        log(f"{name}: {len(found)} message(s) already asked about; clear allowed")
        return True
    state[path] = len(found)
    try:
        ensure_state()
        write_json(GUARD_STATE, dict(list(state.items())[-50:]))
    except OSError:
        pass
    lines = "\n".join(f"- {w}: {t}" for w, t in found[:10])
    log(f"{name}: held once, {len(found)} message(s) during the save")
    reason = ("save-clear: messages arrived during the save:\n" + lines + "\n"
              "Before clearing: 1) answer what you have not answered; 2) add to the snapshot a section "
              "'## After the save' with the FULL text of each of these messages and your answer in one line; "
              "3) call clear_session again in this turn: the second call goes through. '(queued message)' = "
              "a message still waiting to run; note in the snapshot that it was queued at clear time.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    return False


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"error: {e!r}")
    sys.exit(0)
