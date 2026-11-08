#!/usr/bin/env python3
"""Stop hook: when the context passes a token limit, ask Claude to run save-clear at the end of the turn.

The limit is the plugin option context_cap_tokens (or $SAVE_CLEAR_CAP_TOKENS); default 300000,
0 turns it off. It only acts when Claude ends its turn, never in the middle of a task.
It does nothing when:
- the session is headless (claude -p or the Agent SDK), or the stop hook is already active;
- this session already armed its automatic /clear, or it is the desktop app relay session;
- it already asked in this session and the context has not grown 50k tokens since.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from common import STATE, clear_key, clear_path, log, read_hook_input, read_json, setting, tail_lines, write_json  # noqa: E402

DEFAULT_CAP = 300000
REPEAT_AFTER = 50000
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
        return sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                 "cache_creation_input_tokens"))
    return 0


def is_relay():
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID")
    relay = read_json(os.path.join(STATE, "relay.json"))
    return bool(host and isinstance(relay, dict) and relay.get("local_id") == host)


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
    if is_relay() or os.path.exists(clear_path(clear_key(sid))):
        return
    ctx = last_context(tp)
    if ctx < cap:
        return
    mark = os.path.join(CAP_DIR, re.sub(r"[^A-Za-z0-9_-]", "", sid)[:120] + ".json")
    prev = read_json(mark)
    prev = int(prev.get("ctx") or 0) if isinstance(prev, dict) else 0
    if prev and ctx < prev + REPEAT_AFTER:
        return
    os.makedirs(CAP_DIR, exist_ok=True)
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
