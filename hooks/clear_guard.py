#!/usr/bin/env python3
"""PreToolUse on the desktop app's clear_session tool (mcp__ccd_session_mgmt__clear_session).

In the Claude desktop app the skill ends with clear_session("self"). Two things would be lost:
- Background subagents still running. When the turn ends, the app ends this session's process and starts
  a new one, and every subagent dies with the old process (seen on 2.1.330). While one runs (or just
  finished and its result has not reached the conversation), this hook denies the clear and records a
  hold; hooks/stop.py asks Claude to finish the clear once they are all done.
- A message the user sent DURING the save: its answer leaves the screen, or it runs after the turn and
  the clear wipes it. This hook denies the clear ONCE when messages arrived after the skill loaded, asks
  Claude to put them (and the answers) in the snapshot, and lets the second call through.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from common import (APP_WAIT_MAX_AGE_MS, BIN, STATE, SYSTEM_TAG, _text_of as text_of, app_session_info,  # noqa: E402
                    app_wait_expired, describe_subagents, drop_app_wait, ensure_state,
                    hold_resume_requests, log as _log, mark_app_wait, pending_subagents, process_start_ms,
                    read_app_wait, read_hook_input, read_json, release_resume_requests, run_hook_command,
                    write_json)
import re  # noqa: E402

GUARD_STATE = os.path.join(STATE, "clear-guard.json")  # transcript -> number of messages already asked about
SKILL_MARK = "/skills/save-clear"
WINDOW_S = 20 * 60  # without the skill marker: look at the last 20 min only


def log(msg):
    _log("clear-guard", msg)


SYSTEM_RE = re.compile(SYSTEM_TAG)


def is_noise(t):
    t = t.lstrip()
    return (not t or SYSTEM_RE.match(t) or t.startswith("Stop hook feedback")
            or t.startswith("Base directory for this skill") or t.startswith("[Request interrupted"))


def ts_epoch(o):
    try:
        import calendar
        return calendar.timegm(time.strptime((o.get("timestamp") or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def new_messages(entries):
    first = None  # the first entry to look at
    for i, o in enumerate(entries):
        txt = text_of((o.get("message") or {}).get("content"))
        if o.get("type") == "user" and SKILL_MARK in txt and "Base directory for this skill" in txt:
            first = i + 1  # after the skill's own message
    if first is None:  # no marker: every entry of the last WINDOW_S, the first recent one included
        now = time.time()
        first = next((i for i, o in enumerate(entries) if (ts_epoch(o) or 0) >= now - WINDOW_S), len(entries))
    found = []
    for o in entries[first:]:
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
            if m.get("role") != "user" or o.get("isMeta") or o.get("isCompactSummary") or o.get("isSidechain"):
                continue  # a subagent's hand-back (isMeta) or a compaction summary is not the user's words
            tx = text_of(m.get("content"))
            if not is_noise(tx) and "tool_result" not in json.dumps(m.get("content"))[:200]:
                found.append(((o.get("timestamp") or "")[11:19], tx[:80]))
    return found


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))


def hold_for_subagents(data):
    """Deny while this session's background subagents would be cut off (True = denied)."""
    sid = data.get("session_id") or ""
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID") or ""
    info = app_session_info(host)
    if info.get("remoteControlSpawn"):
        # Started from Remote Control: the app never lets a tool clear it (`save-clear-relay request` prints
        # RC-LOCKED, the reply is "You can /clear now."), and a /clear the user types keeps the process, so
        # its subagents live on (seen on 11/11/2026). Nothing to hold.
        if drop_app_wait(sid):
            release_resume_requests(host)
        return False
    try:
        start = process_start_ms()
        agents = pending_subagents(data.get("transcript_path") or "", sid, start)
    except Exception as e:  # a broken check must not block every clear
        log(f"{sid[:8]}: subagent check failed ({e!r}); not held for subagents")
        return False
    if not agents:
        if drop_app_wait(sid):
            log(f"{sid[:8]}: the subagents it waited for are done")
            if info.get("remoteControlUserEnabled"):
                # clear_session called on its own after a hold, not through the Stop hook's finish steps (they
                # run `save-clear-relay request` again, which prints the Remote Control line). The hold's deny
                # asked to turn Remote Control back on, and the app does not clear a session while it is on.
                # Asked once: the hold is gone, so the next call goes through.
                relay = os.path.join(BIN, "save-clear-relay")
                log(f"{sid[:8]}: called after the hold with Remote Control on; asked once to redo the relay steps")
                deny("save-clear: not cleared yet: the background subagents the clear waited for are done, but "
                     "Remote Control is on here, and the app does not clear a session while it is on. Do the "
                     f"desktop app part of step 8 again: run `python3 \"{relay}\" ensure`, then "
                     f"`python3 \"{relay}\" request` (the request made before the wait was taken out of the "
                     "queue), and follow what they print: with RC-OFF-SELF, turn Remote Control off right before "
                     "clear_session (the relay turns it back on after the clear). Then call clear_session again "
                     "in this turn, unless they print RC-KEEP: then do not clear and reply \"You can /clear now.\"")
                return True
            release_resume_requests(host)
        return False
    hold = read_app_wait(sid, start)
    if hold and app_wait_expired(hold):
        release_resume_requests(host)
        drop_app_wait(sid)
        log(f"{sid[:8]}: held for over {APP_WAIT_MAX_AGE_MS // 3600000} h; the clear goes through anyway")
        return False
    running = [a for a in agents if a.get("why") == "running"]
    if not running:
        # Only finished subagents whose result has not reached the conversation. Nothing would make a later
        # turn re-check, so deny ONCE: Claude reads them (TaskOutput, or the notification) and calls again.
        told = set(str(x) for x in (hold.get("reported") or []))
        if all(a["id"] in told for a in agents):
            release_resume_requests(host)
            drop_app_wait(sid)
            log(f"{sid[:8]}: only finished subagents left, already reported once; the clear goes through")
            return False
        mark_app_wait(sid, agents, reported=[a["id"] for a in agents], start_ms=start)
        hold_resume_requests(host)
        log(f"{sid[:8]}: held once, finished subagent(s) not read yet: " + ", ".join(a["id"] for a in agents))
        deny(f"save-clear: not cleared yet: {len(agents)} background subagent(s) of this session just finished "
             f"and their results are not in this conversation yet: {describe_subagents(agents)}. Read each "
             "result now (TaskOutput with its id, or the task notification if it already arrived) and add it to "
             "the snapshot in a section \"## After the save\" (its id, what it returned, what is still open). "
             "Then redo the desktop app part of step 8 (`save-clear-relay request` again: the earlier request "
             "was taken out of the queue) and call clear_session again in this turn: the next call goes through.")
        return True
    mark_app_wait(sid, agents, start_ms=start)
    hold_resume_requests(host)
    since = time.strftime("%H:%M:%S", time.localtime(start / 1000)) if start else "unknown"
    log(f"{sid[:8]}: held, {len(agents)} subagent(s) still pending: " + ", ".join(a["id"] for a in agents)
        + f" (process started {since})")
    deny(f"save-clear: not cleared yet: {len(agents)} background subagent(s) of this session are still at work: "
         f"{describe_subagents(agents)}. In the desktop app, clear_session ends this session's process when the "
         "turn ends, and every subagent still running dies with it (its work stops halfway, its result is lost). "
         "Do not stop them and do not call clear_session again in this turn. If you turned Remote Control off "
         "for the clear, turn it back on now (mcp__ccd_session_mgmt__set_remote_control, session_id \"self\", "
         "enabled true). End the turn with one short line saying the clear waits for these subagents. Each "
         "result arrives here as a task notification: handle it as usual and add it to the snapshot, in a "
         "section \"## After the save\" (its id, what it returned, what is still open). When none is left, the "
         "Stop hook tells you how to finish the clear.")
    return True


def main():
    data = read_hook_input()
    if (data.get("tool_input") or {}).get("session_id") != "self":
        return
    if hold_for_subagents(data):
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
            lines = [line for line in f if line.strip()]
    except Exception as e:
        log(f"no transcript ({e}); clear allowed")
        return True
    entries = []
    for line in lines:  # a cut or broken line is skipped, not a reason to let the clear through
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if isinstance(o, dict):
            entries.append(o)
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
              "3) call clear_session again in this turn: the second call goes through (if you turned Remote Control "
              "off for the clear and turned it back on after this refusal, turn it off again right before the "
              "call). '(queued message)' = "
              "a message still waiting to run; note in the snapshot that it was queued at clear time.")
    deny(reason)
    return False


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"error: {e!r}")
    sys.exit(0)
