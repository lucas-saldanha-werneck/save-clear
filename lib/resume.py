"""Auto-resume: arm (from the skill) and deliver (from the SessionStart hook).

The flag records who armed it, so only that session gets the summary back:
  {snapshot, session_id, cwd, app_session, entrypoint, pid, job, ts}
With resumes pending from several sessions, the file holds a list and each session takes only its
own entry. A global flag would hand the summary to whichever session starts first, in any folder.
"""
import os
import re

from common import RESUME_FLAG, RESUME_LOCK, ensure_state, language, locked, log, now_ms, read_json, write_json

MAX_AGE_MS = 7 * 24 * 3600 * 1000  # older entries are dropped undelivered


def _short(s):
    s = str(s or "")
    return s[:14] if s.startswith("local_") else (s[:8] or "-")


def norm_dir(d):
    if not d:
        return ""
    try:
        d = os.path.realpath(d)
    except OSError:
        pass
    return d.rstrip("/") or "/"


def read_entries():
    """[] = no flag; None = unreadable."""
    if not os.path.exists(RESUME_FLAG):
        return []
    v = read_json(RESUME_FLAG)
    if v is None:
        return None
    return [e for e in (v if isinstance(v, list) else [v]) if isinstance(e, dict) and e.get("snapshot")]


def write_entries(entries):
    if not entries:
        try:
            os.unlink(RESUME_FLAG)
        except OSError:
            pass
        return
    ensure_state()
    write_json(RESUME_FLAG, entries[0] if len(entries) == 1 else entries)


def too_old(e, now):
    return now - int(e.get("ts") or 0) > MAX_AGE_MS


def same_owner(a, b):
    if a.get("app_session") or b.get("app_session"):
        return a.get("app_session") == b.get("app_session")
    if a.get("job") or b.get("job"):
        return a.get("job") == b.get("job")
    return bool(a.get("session_id")) and a.get("session_id") == b.get("session_id") or \
        bool(a.get("pid")) and a.get("pid") == b.get("pid")


def match(e, hook, env, now):
    """(ok, why): is this new session the one that armed entry e?"""
    src = hook.get("source") or "startup"
    ecwd = norm_dir(e.get("cwd"))
    dirs = [norm_dir(d) for d in (hook.get("cwd"), env.get("CLAUDE_PROJECT_DIR")) if d]
    if not ecwd or not any(d == ecwd or ecwd.startswith(d + "/") for d in dirs):
        return False, f"other folder ({e.get('cwd') or 'flag has no folder'})"
    # The same conversation coming back (resume, compact) still has its context: keep it for the /clear
    if src not in ("startup", "clear"):
        return False, f"source {src}: old conversation, still has its context"
    if e.get("session_id") and hook.get("session_id") == e.get("session_id"):
        return False, "same conversation, still has its context"
    # Desktop app: a clear opens a new transcript in a new process; the host session id stays
    if e.get("app_session"):
        host = env.get("CLAUDE_CODE_HOST_SESSION_ID")
        if host == e["app_session"]:
            return True, f"same app session ({_short(host)})"
        return False, f"armed by app session {_short(e['app_session'])}, this is {_short(host)}"
    if e.get("job"):
        job = os.path.basename(env.get("CLAUDE_JOB_DIR") or "")
        return (True, f"same job {job}") if job == e["job"] else (False, f"armed by job {e['job']}")
    # Terminal or IDE: /clear happens in the SAME process (a recycled pid with source startup does not pass)
    if e.get("pid"):
        if src == "clear" and str(env.get("CLAUDE_PID") or "") == str(e["pid"]):
            return True, f"/clear in the same process (pid {e['pid']})"
        return False, f"armed by process {e['pid']}; this is {env.get('CLAUDE_PID') or '?'} with source {src}"
    # No process id, app session or job: a folder and an age cannot tell two sessions apart
    return False, f"armed without a process id ({e.get('entrypoint') or 'cli'}): not delivered automatically"


def arm(snapshot, session_id=None, env=None):
    """Record a resume for the calling session. Returns (ok, message)."""
    env = os.environ if env is None else env
    snap = os.path.abspath(os.path.expanduser(snapshot or ""))
    if not snapshot or not os.path.isfile(snap):
        return False, f"save-clear: snapshot not found: {snapshot or '(missing path)'}. Resume NOT armed."
    now = now_ms()
    e = {
        "snapshot": snap,
        "session_id": env.get("CLAUDE_CODE_SESSION_ID") or session_id or None,
        "cwd": norm_dir(env.get("CLAUDE_PROJECT_DIR") or os.getcwd()),
        "app_session": env.get("CLAUDE_CODE_HOST_SESSION_ID") or None,
        "entrypoint": env.get("CLAUDE_CODE_ENTRYPOINT") or "cli",
        "pid": int(env["CLAUDE_PID"]) if (env.get("CLAUDE_PID") or "").isdigit() else None,
        "job": os.path.basename(env["CLAUDE_JOB_DIR"]) if env.get("CLAUDE_JOB_DIR") else None,
        "ts": now,
    }
    with locked(RESUME_LOCK):  # read, change and write back as one step: concurrent arms keep every entry
        old = read_entries() or []
        keep = [x for x in old if not same_owner(x, e) and not too_old(x, now)]
        write_entries(keep + [e])
        back = read_entries() or []
    if not any(x.get("snapshot") == snap and x.get("ts") == now for x in back):
        return False, f"save-clear: the resume flag was not written (check {RESUME_FLAG})."
    ids = f"session {_short(e['session_id'])}"
    if e["app_session"]:
        ids += f", app {_short(e['app_session'])}"
    if e["pid"]:
        ids += f", pid {e['pid']}"
    queued = f"; {len(keep)} from other sessions queued" if keep else ""
    log("resume", f"armed {os.path.basename(snap)} by {ids}, folder {e['cwd']}, {e['entrypoint']}{queued}")
    msg = f"auto-resume armed: {os.path.basename(snap)} for this session only ({ids}; folder {e['cwd']})."
    if not (e["app_session"] or e["pid"] or e["job"]):
        msg += (f"\nwarning: no process id or app session id, so this summary is NOT delivered automatically. "
                f"After the /clear, ask Claude to read {snap}.")
    return True, msg


def take(hook, env, now):
    """Find and remove this session's entry. Returns (entry or None, why)."""
    with locked(RESUME_LOCK):  # an arm in another session waits instead of being overwritten
        return _take(hook, env, now)


def _take(hook, env, now):
    entries = read_entries()
    if entries is None or (os.path.exists(RESUME_FLAG) and not entries):
        log("resume", "resume flag unreadable or empty: deleted")
        write_entries([])
        return None, ""
    live = [e for e in entries if not too_old(e, now)]
    if len(live) < len(entries):
        log("resume", f"dropped {len(entries) - len(live)} resume(s) older than 7 days")
    who = (f"session {_short(hook.get('session_id'))} app {_short(env.get('CLAUDE_CODE_HOST_SESSION_ID'))} "
           f"source {hook.get('source') or '?'} folder {hook.get('cwd') or '?'}")
    misses = []
    for i, e in enumerate(live):
        ok, why = match(e, hook, env, now)
        if ok:
            write_entries(live[:i] + live[i + 1:])
            log("resume", f"delivered {os.path.basename(e['snapshot'])} to {who} ({why})")
            return e, why
        misses.append(f"{os.path.basename(e['snapshot'])}: {why}")
    if len(live) < len(entries):
        write_entries(live)
    if live:
        log("resume", f"not delivered to {who} - {'; '.join(misses)}. Flag kept.")
    return None, ""


URL_RE = re.compile(r"^summary_url:\s*(https://\S+)\s*$", re.M)


def resume_text(snapshot_path, extra=""):
    try:
        with open(snapshot_path, encoding="utf8") as f:
            summary = f.read()
    except OSError:
        log("resume", f"snapshot missing: {snapshot_path}")
        return None
    lines = ["## Previous session (auto-resume)", "", f"Snapshot: `{snapshot_path}`", ""]
    url = URL_RE.search(summary)
    if url:
        lines += [f'FIRST LINE of your first reply, exactly: "Previous conversation: {url.group(1)}" '
                  "(the /clear wiped the screen; the user can read the old conversation there).", ""]
    lang = language()
    if lang:
        lines += [f"Write your replies in {lang}.", ""]
    lines += ["ORDER: write the summary as a TEXT message before asking any question. A question first "
              "ends the turn and the user never sees the summary.", ""]
    if extra:
        lines += ["## From your on_resume_command (do this first)", "", extra, ""]
    lines += [summary, "", "---",
              "Continue from the point above. If the user brings a completely new topic, ignore this "
              "context and do what they ask."]
    return "\n".join(lines)
