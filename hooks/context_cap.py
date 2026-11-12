#!/usr/bin/env python3
"""Stop hook: when the context passes a token limit, ask Claude to run save-clear at the end of the turn.

The limit is the plugin option context_cap_tokens (or $SAVE_CLEAR_CAP_TOKENS); default 150000,
0 turns it off. It only acts when Claude ends its turn, never in the middle of a task.
It does nothing when:
- the session is headless (claude -p or the Agent SDK), or the stop hook is already active;
- this session already armed its automatic /clear (by session id, or by job for `claude --bg`), or it
  armed a resume in the last 15 minutes (the other Stop hook, stop.py, may have just consumed the
  /clear request while both hooks run in parallel), or it is the desktop app relay session;
- it already asked in this session and the context has not grown 50k tokens since. When the context
  drops below the limit again (a compaction), the next crossing asks again.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import resume  # noqa: E402
from common import (STATE, clear_key, clear_path, log, now_ms, read_hook_input, read_json, setting,  # noqa: E402
                    tail_lines, write_json)

DEFAULT_CAP = 150000
REPEAT_AFTER = 50000
RECENT_SAVE_MS = 15 * 60 * 1000
MARK_MAX_AGE_S = 7 * 24 * 3600
CAP_DIR = os.path.join(STATE, "cap")


def cap_tokens():
    raw = setting("context_cap_tokens", str(DEFAULT_CAP)).replace("_", "").replace(",", "")
    try:
        return max(0, int(float(raw)))
    except ValueError:
        log("cap", f"bad context_cap_tokens {raw!r}; using {DEFAULT_CAP}")
        return DEFAULT_CAP


def last_context(transcript):
    """Tokens in the context at the last main-thread reply (input + cache read + cache write)."""
    for line in reversed(tail_lines(transcript, 4 * 1024 * 1024)):
        if '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("isSidechain"):
            continue
        msg = d.get("message")
        u = msg.get("usage") if isinstance(msg, dict) else None
        if not isinstance(u, dict):
            continue
        total = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                  "cache_creation_input_tokens"))
        if total:  # a synthetic reply (API error, interrupt) has all zeros: not the context size
            return total
    return 0


def is_relay():
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID")
    relay = read_json(os.path.join(STATE, "relay.json"))
    return bool(host and isinstance(relay, dict) and relay.get("local_id") == host)


def saved_recently(sid):
    """This session armed a resume a moment ago: it is in the middle of its own save-clear."""
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID")
    now = now_ms()
    for e in resume.read_entries() or []:
        mine = e.get("session_id") == sid or (host and e.get("app_session") == host)
        if mine and now - int(e.get("ts") or 0) < RECENT_SAVE_MS:
            return True
    return False


def prune_marks():
    try:
        for n in os.listdir(CAP_DIR):
            p = os.path.join(CAP_DIR, n)
            if time.time() - os.path.getmtime(p) > MARK_MAX_AGE_S:
                os.unlink(p)
    except OSError:
        pass


def main():
    cap = cap_tokens()
    if not cap:
        return
    if (os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "").startswith("sdk"):
        return
    hook = read_hook_input()
    if hook.get("stop_hook_active"):
        return
    sid = str(hook.get("session_id") or "")
    tp = hook.get("transcript_path")
    if not sid or not tp or not os.path.isfile(tp):
        return
    job = os.path.basename(os.environ.get("CLAUDE_JOB_DIR") or "")
    armed = [clear_path(clear_key(sid))] + ([clear_path(clear_key(job=job))] if job else [])
    if is_relay() or any(os.path.exists(p) for p in armed) or saved_recently(sid):
        return
    ctx = last_context(tp)
    mark = os.path.join(CAP_DIR, re.sub(r"[^A-Za-z0-9_-]", "", sid)[:120] + ".json")
    if ctx < cap:
        if ctx and os.path.exists(mark):  # back under the limit (compaction): the next crossing asks again
            try:
                os.unlink(mark)
            except OSError:
                pass
        return
    prev = read_json(mark)
    prev = int(prev.get("ctx") or 0) if isinstance(prev, dict) else 0
    if prev and ctx < prev + REPEAT_AFTER:
        return
    os.makedirs(CAP_DIR, exist_ok=True)
    prune_marks()
    write_json(mark, {"ctx": ctx, "cap": cap})
    log("cap", f"session {sid[:8]} at {ctx} tokens (limit {cap}): asked for save-clear")
    print(json.dumps({
        "decision": "block",
        "reason": (f"save-clear: the context is at {round(ctx / 1000)}k tokens (limit {round(cap / 1000)}k) "
                   "and this turn's task is done. Run the save-clear skill now in \"save and clear\" mode "
                   "(memory + snapshot with findings, plan, files changed and next step; arm the resume and "
                   "the automatic /clear). If you ended the turn with a question to the user, put that "
                   "question in the snapshot's next steps. Do not start new work first."),
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the session over this hook
        log("cap", f"error: {e!r}")
    sys.exit(0)
