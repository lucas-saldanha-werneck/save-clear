#!/usr/bin/env python3
"""Stop hook: if THIS session armed the automatic /clear, start the helper that types it.

No flag: exits at once. Rules:
- Each session has its own flag file (clear/<session id>.json, or clear/job-<id>.json for a
  `claude --bg` job), so one session's request never replaces another's. An inherited
  TMUX_PANE is not proof: desktop app sessions inherit the pane of the terminal that opened them.
- Modes that type into a terminal (tmux) only run in a terminal session, and only after
  `save-clear-send --check` confirms the target runs this session.
- A user message that arrived after the save blocks the stop (up to 3 rounds): Claude answers it,
  adds it to the snapshot and re-arms. The message never vanishes in the /clear.
- A background subagent's result that reached THIS conversation after the save (before the /clear)
  blocks the stop the same way: Claude records it in the snapshot and re-arms. Subagents still running
  are left alone: they survive a terminal /clear and report to the new conversation.
- Desktop app: a clear held for running subagents (app-wait/<session>.json, written by the clear guard or
  `save-clear arm-clear`) waits quietly while any is pending; at the end of the first turn with none left,
  the stop is blocked once and Claude is asked to finish the clear.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import resume  # noqa: E402
from common import (APP_WAIT_DIR, APP_WAIT_MAX_AGE_MS, BIN, CLEAR_DIR, MARGIN_MS, app_wait_path,  # noqa: E402
                    clear_key, clear_path, drop_app_wait, kill_background_tasks, log, notify, now_ms,
                    pending_subagents, process_start_ms, read_app_wait, read_hook_input, read_json, stop_tasks_instruction, subagent_reports_since,
                    user_messages_since, write_json)

MAX_DELTAS = 3
MAX_AGE_MS = 15 * 60 * 1000
MAX_AGENT_ROUNDS = 20


FLAG = {"path": ""}


def drop_flag():
    try:
        os.unlink(FLAG["path"])
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


def find_flag(hook, job):
    """This session's flag (by session id, then by --bg job), or None."""
    sid = hook.get("session_id") or ""
    for key in ([clear_key(sid)] if sid else []) + ([clear_key(job=job)] if job else []):
        path = clear_path(key)
        if not os.path.exists(path):
            continue
        flag = read_json(path)
        FLAG["path"] = path
        if not isinstance(flag, dict):
            drop_flag()
            continue
        same_session = flag.get("session_id") and flag.get("session_id") == sid
        same_job = flag.get("mode") == "bg" and flag.get("target") and job == flag.get("target")
        if same_session or same_job:
            return flag
    return None


def app_wait(hook):
    """Desktop app: this session's clear was held for its background subagents. Any still pending: nothing
    to do (each result starts a new turn). None left: block once and ask Claude to finish the clear.
    True = the stop was blocked."""
    sid = hook.get("session_id") or ""
    path = app_wait_path(sid)
    if not sid or not os.path.exists(path):
        return False
    start = process_start_ms()
    hold = read_app_wait(sid, start)  # {} when unreadable or left by an earlier process (its agents died)
    if not hold or now_ms() - int(hold.get("ts") or 0) > APP_WAIT_MAX_AGE_MS:
        drop_app_wait(sid)
        log("stop", f"{sid[:8]}: subagent hold expired, unreadable or left by an earlier process; dropped")
        return False
    tp = hook.get("transcript_path") or ""
    if any(a.get("why") == "running" for a in pending_subagents(tp, sid, start)):
        return False  # a result that is only on its way ("reporting") does not wait: the guard asks once
    drop_app_wait(sid)  # asked once: never again for this hold
    ids = ", ".join(str(x) for x in (hold.get("agents") or [])[:8]) or "see the snapshot"
    if user_messages_since(tp, int(hold.get("ts") or 0)):
        # The user wrote after the hold: the conversation moved on, and a clear now could wipe what they
        # are doing. Do not force it; tell them how to finish it.
        log("stop", f"{sid[:8]}: the subagents the clear waited for are done, but the user wrote since; not cleared")
        print(json.dumps({"systemMessage": (
            f"save-clear: the background subagents the clear was waiting for are done ({ids}). The clear was "
            "not finished because you wrote since then. Say \"save-clear\" when you want to clear.")}))
        return False
    snap = my_snapshot(hook) or "(the one you wrote; it is this session's entry in the save-clear status output)"
    relay = os.path.join(BIN, "save-clear-relay")
    log("stop", f"{sid[:8]}: the subagents the clear waited for are done; asked Claude to finish the clear")
    print(json.dumps({
        "decision": "block",
        "reason": (f"save-clear: the background subagents this session's clear was waiting for are done ({ids}). "
                   f"Finish the save-and-clear now. 1) Make sure the snapshot {snap} has each of their results in "
                   "a section \"## After the save\" (id, what it returned, what you did with it, what is still "
                   "open) and marks them finished under \"## Running subagents\"; if the conversation moved on "
                   "since the save, update \"Last topic\" and \"Next steps\" too. 2) Do the desktop app part of "
                   f"step 8 of the save-clear skill again: run `python3 \"{relay}\" ensure`, then "
                   f"`python3 \"{relay}\" request` (the request made before the wait was taken out of the queue), and follow "
                   "what they print (DO lines, the Remote Control line). 3) As the very last action, call "
                   "clear_session with session_id \"self\". If the user asked you not to clear, skip 2) and 3) "
                   "and say so in one line.")
                  + (" " + stop_tasks_instruction("Before calling clear_session,") if kill_background_tasks() else ""),
    }))
    return True


def main():
    waits = os.path.isdir(APP_WAIT_DIR) and bool(os.listdir(APP_WAIT_DIR))
    clears = os.path.isdir(CLEAR_DIR) and bool(os.listdir(CLEAR_DIR))
    if not waits and not clears:
        return
    hook = read_hook_input()
    if waits and app_wait(hook):
        return
    if not clears:
        return
    job = os.path.basename(os.environ.get("CLAUDE_JOB_DIR") or "")
    flag = find_flag(hook, job)
    if not flag:
        return
    if now_ms() - int(flag.get("armed_at") or flag.get("ts") or 0) > MAX_AGE_MS:
        drop_flag()
        return

    if flag.get("mode") == "iterm":
        drop_flag()
        refuse("the iTerm2 mode was removed: an empty prompt cannot be checked there")
    if flag.get("mode") == "tmux" and not job:
        # A non-cli entrypoint here is usually inherited from a tmux server started by an app session. The
        # pane is then trusted only when the check positively proves it runs this session (unknown = refuse).
        ep = os.environ.get("CLAUDE_CODE_ENTRYPOINT") or "cli"
        try:
            chk = subprocess.run([sys.executable, os.path.join(BIN, "save-clear-send"), "--check", flag["mode"],
                                  flag.get("target") or "", flag.get("session_id") or ""],
                                 capture_output=True, text=True, timeout=7)
            if chk.returncode == 1 or (ep != "cli" and chk.returncode != 0):
                drop_flag()
                refuse(chk.stdout.strip() or f"target {flag.get('target')} does not run this session ({ep})")
        except subprocess.TimeoutExpired:
            if ep != "cli":
                drop_flag()
                refuse(f"this session is {ep} and target {flag.get('target')} could not be checked in time")
            # cli: unknown for now; the helper checks again before every key

    deltas = int(flag.get("deltas") or 0)
    fresh = user_messages_since(hook.get("transcript_path"), int(flag.get("ts") or 0) - MARGIN_MS)
    reports = subagent_reports_since(hook.get("transcript_path"), int(flag.get("ts") or 0) - MARGIN_MS)
    listed = "; ".join(f"{tid} ({st or 'done'}): {summ}" for tid, st, summ in reports[-5:])
    if fresh and deltas < MAX_DELTAS:
        snap = my_snapshot(hook) or "(the one you wrote; it is this session's entry in the save-clear status output)"
        quoted = "; ".join('"' + " ".join(m[:300].split()) + '"' for m in fresh[-5:])
        rearm = f"python3 \"{os.path.join(BIN, 'save-clear')}\" rearm {flag.get('session_id') or ''}".strip()
        print(json.dumps({
            "decision": "block",
            "reason": (f"save-clear: a message from the user arrived after the save: {quoted}. Not cleared yet. "
                       "1) If you have not answered it, answer it (if it asks for work, do it). "
                       f"2) Add to the snapshot {snap} a section \"## After the save\" with only what came "
                       "after: the message, your answer, what changed and any open question"
                       + (f", and these subagent results: {listed}" if reports else "") + ". Do not rewrite "
                       f"the rest. 3) Run `{rearm}` and end the turn with one short line. "
                       "The /clear follows by itself."),
        }))
        return

    if reports and int(flag.get("agent_rounds") or 0) < MAX_AGENT_ROUNDS:
        snap = my_snapshot(hook) or "(the one you wrote; it is this session's entry in the save-clear status output)"
        rearm = f"python3 \"{os.path.join(BIN, 'save-clear')}\" rearm {flag.get('session_id') or ''} --subagents"
        log("stop", f"asked Claude to record {len(reports)} subagent result(s) that came after the save")
        print(json.dumps({
            "decision": "block",
            "reason": (f"save-clear: background subagent result(s) arrived after the save: {listed}. Not cleared "
                       "yet. 1) Handle each result as you normally would (check what it claims). "
                       f"2) Add to the snapshot {snap}, in a section \"## After the save\", one line per subagent: "
                       "its id, what it returned (PR, commit, numbers, paths), what you did with it and what is "
                       "still open; mark it finished under \"## Running subagents\". Do not rewrite the rest. "
                       f"3) Run `{rearm}` and end the turn with one short line. Do not stop the subagents "
                       "still running: they survive the /clear, which follows by itself."),
        }))
        return

    if flag.get("busy_rearm") and not flag.get("bg_asked") and kill_background_tasks():
        # The helper found the session busy and re-armed. A leftover background task can keep it busy
        # forever; only Claude can stop its own tasks, so ask once, then let the next Stop go ahead.
        flag["bg_asked"] = True
        try:
            write_json(FLAG["path"], flag)
        except OSError as e:  # cannot remember that we asked: do not risk asking every turn
            log("stop", f"could not mark the background-task request, clearing without it: {e!r}")
        else:
            log("stop", "asked Claude to stop leftover background tasks before the /clear")
            print(json.dumps({
                "decision": "block",
                "reason": ("save-clear: the automatic /clear waited, but the session stayed busy. "
                           + stop_tasks_instruction("Now,", my_snapshot(hook) or "(the one you wrote)")
                           + " End the turn with one short line; the /clear follows."),
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
