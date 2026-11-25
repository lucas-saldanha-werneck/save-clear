#!/usr/bin/env python3
"""SessionStart hook: hand the saved summary to the session that armed it (after its /clear).

Before that it runs the user's on_resume_command (if set) and adds what it prints.
Also creates your config file (~/.config/save-clear/config.json) from config.example.json when it
is missing, and copies the plugin options to the state folder, so the save-clear command (run from
Bash, where plugin options are not set) sees the same snapshot folder.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import resume  # noqa: E402
from common import (RESUME_FLAG, RESUME_TURN_DIR, cut_off_subagents, log, now_ms, process_start_ms,  # noqa: E402
                    read_hook_input, resume_turn_marker, run_hook_command, save_config, save_pointer,
                    seed_user_config, subagent_dir, transcript_of, write_json)

T3_WAIT_S = 4  # the hook has 10 s (hooks.json), on_resume_command up to 5; a miss falls back to the first message
RECENT_CUT_MS = 30 * 60 * 1000  # a dead subagent older than this counts only if the snapshot names it


def cut_off(hook, entry):
    """Background subagents of the conversation before the clear that never finished and were last active
    before this process started: the clear ended their process (desktop app) and they will never report.
    In a terminal the /clear keeps the process, so its subagents are alive and none is listed.
    Only recent ones, or ones the snapshot names, so a subagent that died long ago is not brought back."""
    old = entry.get("session_id") or ""
    if not old or old == hook.get("session_id"):
        return []
    tp = hook.get("transcript_path") or ""
    old_tp = os.path.join(os.path.dirname(tp), old + ".jsonl") if tp else ""
    if not (old_tp and os.path.exists(old_tp)):
        old_tp = transcript_of(old)
    if not old_tp:
        return []
    try:
        with open(entry["snapshot"], encoding="utf8") as f:
            summary = f.read()
    except (OSError, KeyError):
        summary = ""
    start = process_start_ms()
    try:
        agents = [a for a in cut_off_subagents(old_tp, old, start)
                  if (start or 0) - a["active_ms"] < RECENT_CUT_MS or a["id"] in summary]
    except Exception as e:
        log("resume", f"subagent check failed: {e!r}")
        return []
    if agents:
        log("resume", f"{len(agents)} subagent(s) of {old[:8]} stopped mid-work by the clear: "
                      + ", ".join(a["id"] for a in agents))
    folder = subagent_dir(old_tp, old)
    return [dict(a, transcript=os.path.join(folder, f"agent-{a['id']}.jsonl")) for a in agents]


def wake_instruction():
    """App sessions only: when the relay's listener is dead (the app was quit or restarted), one session per
    cool-down is asked to wake it. Returns the instruction for Claude, or "" (the usual case)."""
    if not os.environ.get("CLAUDE_CODE_HOST_SESSION_ID"):
        return ""
    relay = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "save-clear-relay")
    try:
        r = subprocess.run([sys.executable, relay, "wake-claim"], capture_output=True, text=True, encoding="utf-8",
                           timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        log("resume", f"wake-claim failed: {e!r}")
        return ""
    line = r.stdout.strip()
    rid, _, msg = line.partition(" ")
    if r.returncode != 0 or not rid.startswith("local_") or not msg:
        return ""
    return ("save-clear housekeeping, silent: before anything else, make exactly one call: "
            f"mcp__ccd_session_mgmt__send_message with session_id={rid} and message exactly: {msg}\n"
            "Do not write any text about it and do not mention it to the user; then carry on normally. "
            "If that tool is not available, skip it without comment.")


def emit(context, message=""):
    out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
    if message:
        out["systemMessage"] = message
    print(json.dumps(out))


def main():
    seed_user_config()  # first run after install: create ~/.config/save-clear/config.json (never overwrites)
    save_pointer()
    save_config()
    wake = wake_instruction()
    if not os.path.exists(RESUME_FLAG):
        if wake:
            emit(wake)
        return
    hook = read_hook_input()
    # T3 Code: the thread that is meant to take a summary can start before `t3 thread start` returned its id
    # to the session that armed it. Wait a few seconds for the id (only while such a start is under way).
    deadline = time.time() + T3_WAIT_S
    while resume.t3_pending(os.environ) and time.time() < deadline:
        time.sleep(0.5)
    entry, _ = resume.take(hook, os.environ, now_ms())
    if not entry:
        if wake:
            emit(wake)
        return
    # The user's on_resume_command runs after the /clear; what it prints goes into the context too.
    rc, extra = run_hook_command("on_resume_command", "resume", {
        "SAVE_CLEAR_SNAPSHOT": entry["snapshot"], "SAVE_CLEAR_SESSION_ID": hook.get("session_id") or "",
        "SAVE_CLEAR_PREVIOUS_SESSION_ID": entry.get("session_id") or "",
        "SAVE_CLEAR_PROJECT_DIR": hook.get("cwd") or ""}, timeout=5)
    text = resume.resume_text(entry["snapshot"], extra if rc == 0 else "", hook.get("session_id") or "",
                              cut_off(hook, entry))
    if not text:
        if wake:
            emit(wake)
        return
    if hook.get("session_id"):  # the resume turn may not ask questions (hooks/resume_turn_guard.py)
        try:
            os.makedirs(RESUME_TURN_DIR, exist_ok=True)
            write_json(resume_turn_marker(hook["session_id"]), {"ts": now_ms(), "snapshot": entry["snapshot"]})
        except OSError as e:
            log("resume", f"resume-turn marker not written: {e!r}")
    emit(text + ("\n\n" + wake if wake else ""),
         "save-clear: summary of the previous session loaded. Send any message (e.g. ok) to see it.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("resume", f"error: {e!r}")
    sys.exit(0)
