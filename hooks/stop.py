#!/usr/bin/env python3
"""Stop hook: if THIS session armed the automatic /clear, start the helper that types it.

No flag: exits at once. Rules:
- Only a flag from the same session counts (or the same `claude --bg` job). An inherited
  TMUX_PANE is not proof: desktop app sessions inherit the pane of the terminal that opened them.
- Modes that type into a terminal (tmux, iterm) only run in a terminal session, and only after
  `save-clear-send --check` confirms the target runs this session.
- A user message that arrived after the save blocks the stop (up to 3 rounds): Claude answers it,
  adds it to the snapshot and re-arms. The message never vanishes in the /clear.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import resume  # noqa: E402
from common import (BIN, CLEAR_FLAG, MARGIN_MS, log, notify, now_ms, read_hook_input,  # noqa: E402
                    read_json, user_messages_since)

MAX_DELTAS = 3
MAX_AGE_MS = 15 * 60 * 1000


def drop_flag():
    try:
        os.unlink(CLEAR_FLAG)
    except OSError:
        pass


def refuse(why):
    log("stop", f"REFUSED: {why}")
    notify(f"save-clear: did not clear - {why}")
    print(json.dumps({"systemMessage": f"save-clear: did NOT clear - {why}. Type /clear yourself."}))
    sys.exit(0)


def my_snapshot(hook):
    """The snapshot in this session's resume entry; '' if only other sessions have one."""
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID")
    for e in resume.read_entries() or []:
        if (e.get("session_id") and e.get("session_id") == hook.get("session_id")) or \
                (host and e.get("app_session") == host):
            return e["snapshot"]
    return ""


def main():
    if not os.path.exists(CLEAR_FLAG):
        return
    flag = read_json(CLEAR_FLAG)
    if not isinstance(flag, dict):
        drop_flag()
        return
    hook = read_hook_input()
    job = os.path.basename(os.environ.get("CLAUDE_JOB_DIR") or "")
    same_session = flag.get("session_id") and flag.get("session_id") == hook.get("session_id")
    same_job = flag.get("mode") == "bg" and flag.get("target") and job == flag.get("target")
    if not (same_session or same_job):
        return
    if now_ms() - int(flag.get("armed_at") or flag.get("ts") or 0) > MAX_AGE_MS:
        drop_flag()
        return

    if flag.get("mode") in ("tmux", "iterm") and not job:
        ep = os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "cli"
        if ep != "cli":
            drop_flag()
            refuse(f"this session is {ep}, not a terminal; the {flag['mode']} target {flag.get('target')} "
                   "would be another session")
        try:
            chk = subprocess.run([sys.executable, os.path.join(BIN, "save-clear-send"), "--check", flag["mode"],
                                  flag.get("target") or "", flag.get("session_id") or ""],
                                 capture_output=True, text=True, timeout=7)
            if chk.returncode == 1:
                drop_flag()
                refuse(chk.stdout.strip() or f"target {flag.get('target')} does not run this session")
        except subprocess.TimeoutExpired:
            pass  # unknown for now: the helper checks again before every key

    deltas = int(flag.get("deltas") or 0)
    fresh = user_messages_since(hook.get("transcript_path"), int(flag.get("ts") or 0) - MARGIN_MS)
    if fresh and deltas < MAX_DELTAS:
        snap = my_snapshot(hook) or "(the one you wrote; it is this session's entry in the save-clear status output)"
        quoted = "; ".join('"' + " ".join(m[:300].split()) + '"' for m in fresh[-5:])
        print(json.dumps({
            "decision": "block",
            "reason": (f"save-clear: a message from the user arrived after the save: {quoted}. Not cleared yet. "
                       "1) If you have not answered it, answer it (if it asks for work, do it). "
                       f"2) Add to the snapshot {snap} a section \"## After the save\" with only what came "
                       "after: the message, your answer, what changed and any open question. Do not rewrite "
                       f"the rest. 3) Run `python3 \"{os.path.join(BIN, 'save-clear')}\" rearm` and end the "
                       "turn with one short line. The /clear follows by itself."),
        }))
        return

    drop_flag()  # consumed: fires at most once; the helper re-arms when needed
    if job and flag.get("mode") != "bg":
        flag["mode"], flag["target"] = "bg", job  # no terminal of its own: attach instead
    subprocess.Popen([sys.executable, os.path.join(BIN, "save-clear-send"), flag.get("mode") or "",
                      flag.get("target") or "", str(flag.get("session_id") or ""), json.dumps(flag),
                      str(hook.get("transcript_path") or "")],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    log("stop", f"helper started: mode={flag.get('mode')} target={flag.get('target')}")
    print(json.dumps({"suppressOutput": True,
                      "systemMessage": "save-clear: automatic /clear as soon as the session is free"}))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # never break the session over this hook
        log("stop", f"error: {e!r}")
    sys.exit(0)
