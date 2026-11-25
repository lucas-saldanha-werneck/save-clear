"""Shared code for the save-clear hooks and commands. Standard-library Python 3.8+.

Files (all under the state folder: $SAVE_CLEAR_STATE_DIR, else $CLAUDE_PLUGIN_DATA, else ~/.claude/state/save-clear):
  pending_resume.json  who armed a resume, and which snapshot to deliver (one entry or a list)
  pending_resume.lock  held while pending_resume.json is read, changed and written back
  clear/<key>.json     one auto-/clear request per session (key = session id, or job-<id> for --bg)
  app-wait/<session>.json  desktop app: this session's clear is held until its background subagents finish
  cap/<session>.json   context size when the token-limit hook last asked for a save-clear
  idle/<session>.done  last idle stretch the idle trigger (bin/save-clear-idle) already handled
  config.json          plugin options, copied here by the SessionStart hook so bin/ commands see them
  save-clear.log       one line per decision

Your own settings can also live in ~/.config/save-clear/config.json ($SAVE_CLEAR_CONFIG), outside the
plugin folder, so they survive updates. Precedence: plugin option > env var > that file.
The SessionStart hook (and the save-clear command) create that file from config.example.json the first
time, with every option null (= the built-in default); an existing file is never overwritten.
"""
import calendar
import contextlib
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

# Hook JSON, transcripts and our output are UTF-8. On Windows the default for pipes is the ANSI code
# page, where printing a character like an arrow fails and the command prints nothing.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin")
HOME = os.path.expanduser("~")

DEFAULT_STATE = os.path.join(HOME, ".claude", "state", "save-clear")
# Hooks get $CLAUDE_PLUGIN_DATA; commands run from Bash may not. The SessionStart hook records the
# folder it used in this pointer file so every caller ends up in the same place.
POINTER = os.path.expanduser(os.environ.get("SAVE_CLEAR_POINTER") or os.path.join(HOME, ".claude", "state", "save-clear.path"))


def _own_plugin_data():
    """$CLAUDE_PLUGIN_DATA only when it is ours. Claude Code sets it with CLAUDE_PLUGIN_ROOT for our hooks.
    Another plugin's SessionStart hook can export its own CLAUDE_PLUGIN_DATA into the session
    (CLAUDE_ENV_FILE), and then every Bash command carries a folder that belongs to someone else."""
    data = (os.environ.get("CLAUDE_PLUGIN_DATA") or "").strip()
    if not data:
        return ""
    root = (os.environ.get("CLAUDE_PLUGIN_ROOT") or "").strip()
    if root and os.path.realpath(os.path.expanduser(root)) == os.path.realpath(ROOT):
        return data
    if os.path.basename(os.path.normpath(os.path.expanduser(data))).startswith("save-clear"):
        return data  # <plugin>-<marketplace>, e.g. save-clear-lucas-tools
    return ""


def _resolve_state():
    for value in (os.environ.get("SAVE_CLEAR_STATE_DIR"), _own_plugin_data()):
        if value and value.strip():
            return os.path.expanduser(value.strip())
    try:
        with open(POINTER, encoding="utf-8") as f:
            value = f.read().strip()
        if value and os.path.isdir(value):
            return value
    except OSError:
        pass
    return DEFAULT_STATE


STATE = _resolve_state()


def save_pointer():
    """SessionStart: remember $CLAUDE_PLUGIN_DATA for callers that do not have it."""
    data = _own_plugin_data()
    if not data or os.environ.get("SAVE_CLEAR_STATE_DIR"):
        return
    try:
        with open(POINTER, encoding="utf-8") as f:
            if f.read().strip() == data:
                return
    except OSError:
        pass
    try:
        os.makedirs(data, exist_ok=True)
        os.makedirs(os.path.dirname(POINTER), exist_ok=True)
        with open(POINTER, "w", encoding="utf-8") as f:
            f.write(data + "\n")
    except OSError:
        pass


CONFIG = os.path.join(STATE, "config.json")
LOG = os.path.join(STATE, "save-clear.log")
RESUME_FLAG = os.path.join(STATE, "pending_resume.json")
RESUME_LOCK = os.path.join(STATE, "pending_resume.lock")
CLEAR_DIR = os.path.join(STATE, "clear")
RESUME_TURN_DIR = os.path.join(STATE, "resume-turn")

# Plugin option name -> environment variable name
OPTIONS = {
    "snapshot_dir": "SAVE_CLEAR_SNAPSHOT_DIR",
    "notify_command": "SAVE_CLEAR_NOTIFY",
    "publish_link": "SAVE_CLEAR_PUBLISH_LINK",
    "context_cap_tokens": "SAVE_CLEAR_CAP_TOKENS",
    "idle_minutes": "SAVE_CLEAR_IDLE_MINUTES",
    "idle_min_tokens": "SAVE_CLEAR_IDLE_MIN_TOKENS",
    "language": "SAVE_CLEAR_LANGUAGE",
    "resume_message": "SAVE_CLEAR_RESUME_MESSAGE",
    "extra_steps": "SAVE_CLEAR_EXTRA_STEPS",
    "on_save_command": "SAVE_CLEAR_ON_SAVE",
    "pre_clear_command": "SAVE_CLEAR_PRE_CLEAR",
    "on_resume_command": "SAVE_CLEAR_ON_RESUME",
    "kill_background_tasks": "SAVE_CLEAR_KILL_BACKGROUND_TASKS",
    "notify_success": "SAVE_CLEAR_NOTIFY_SUCCESS",
    "relay": "SAVE_CLEAR_RELAY",
    "relay_model": "SAVE_CLEAR_RELAY_MODEL",
    "relay_title": "SAVE_CLEAR_RELAY_TITLE",
    "rc_idle_off_hours": "SAVE_CLEAR_RC_IDLE_OFF_HOURS",
}

# Your own settings, outside the plugin folder: they survive plugin updates and reinstalls.
# Same keys as the plugin options. Lowest precedence (plugin option and env var win).
USER_CONFIG = os.path.expanduser(os.environ.get("SAVE_CLEAR_CONFIG") or os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "save-clear", "config.json"))
# Starter copied to USER_CONFIG on first run: every option null = the built-in default (same as no file).
EXAMPLE_CONFIG = os.path.join(ROOT, "config.example.json")


def seed_user_config():
    """Create USER_CONFIG from config.example.json if it does not exist yet. Never overwrites or
    changes an existing file (O_EXCL), never raises, prints nothing. True if it created the file."""
    try:
        if not os.path.isabs(USER_CONFIG) or os.path.lexists(USER_CONFIG):  # relative = the project folder
            return False
        with open(EXAMPLE_CONFIG, "rb") as f:
            data = f.read()
        os.makedirs(os.path.dirname(USER_CONFIG) or ".", exist_ok=True)
        fd = os.open(USER_CONFIG, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except Exception:
        return False
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return True
    except Exception:
        try:  # only the half-written file this call just created
            os.remove(USER_CONFIG)
        except Exception:
            pass
        return False

RESUME_MSG = ("save-clear: resuming. Start with the LAST topic (where we stopped and what is still open); "
              "then, in one short paragraph, the rest that was saved and the next steps.")

# The 2nd automatic message: sent after the summary turn ends, only when Claude flagged a waiting decision
# (`save-clear decision-waiting`). Starts with "save-clear: pending questions" so it is never the user's own.
FOLLOWUP_MSG = ("save-clear: pending questions. Now ask, with AskUserQuestion, the decision(s) you said were "
                "waiting after the summary. If the user already moved on to something else, skip this.")

# Records Claude Code writes as user messages, wrapped in one of its own tags. A message the user typed
# that merely starts with "<" (say "<3 thanks") is still the user's.
SYSTEM_TAG = (r"<(?:command-[a-z-]+|local-command-[a-z-]+|task-notification|system-reminder|bash-[a-z-]+|"
              r"user-prompt-submit-hook|channel|teammate-message|cross-session-message|persisted-output|"
              r"new-diagnostics|tick|ide_[a-z_]+|user-memory-input|scheduled-task)[\s>]")
# Messages that are not the user's own words
NOT_HUMAN = re.compile(r"^(" + SYSTEM_TAG + r"|Stop hook feedback|Base directory for this skill|save-clear: resuming|save-clear: pending questions|"
                       r"save-clear \(automatic|\[Request interrupted|Caveat:|"
                       r"This session is being continued from a previous conversation)")
MARGIN_MS = 15000  # a message delivered just before the flag may not be in the snapshot


def ensure_state():
    os.makedirs(STATE, exist_ok=True)


def _saved_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def user_config():
    """~/.config/save-clear/config.json (or $SAVE_CLEAR_CONFIG); {} if missing or unreadable."""
    try:
        with open(USER_CONFIG, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def setting(name, default=""):
    """Plugin option (CLAUDE_PLUGIN_OPTION_<NAME>, or in Bash, where it is not set, the copy the
    SessionStart hook saved in config.json), then env var, then the user's own config file."""
    value = (os.environ.get("CLAUDE_PLUGIN_OPTION_" + name.upper()) or "").strip()
    if value:
        return value
    value = _as_text(_saved_config().get(name))
    if value:
        return value
    var = OPTIONS.get(name, "")
    value = (os.environ.get(var) or "").strip() if var else ""
    return value or _as_text(user_config().get(name)) or default


def _as_text(value):
    """A config value as text. A JSON number 0 or false is a real value ("0" = off), not "unset"."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def resume_msg():
    """The resume request typed after /clear: the `resume_message` option, else the default."""
    return " ".join(setting("resume_message").split()) or RESUME_MSG


def is_resume_text(txt, custom=None):
    """True for the default resume request (its distinctive start is enough) or the configured one
    (the whole text: a short custom request like "go on" must not swallow "go on with the refactor")."""
    t = (txt or "").lstrip()
    if t.startswith(RESUME_MSG[:20]):
        return True
    custom = resume_msg() if custom is None else custom
    return " ".join(t.split()) == custom


def language():
    return setting("language")


def extra_steps():
    """The `extra_steps` option: inline text, or the content of the file it names."""
    value = setting("extra_steps")
    if not value:
        return ""
    path = os.path.expanduser(value)
    if len(value) < 1024 and "\n" not in value and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError as e:
            log("config", f"extra_steps file unreadable: {e}")
            return ""
    return value


def split_command(cmd):
    """Words of a command line. On Windows backslashes stay as they are and quotes are removed."""
    if os.name != "nt":
        return [os.path.expanduser(w) if w.startswith("~") else w for w in shlex.split(cmd)]
    words = shlex.split(cmd, posix=False)
    return [w[1:-1] if len(w) > 1 and w[0] == w[-1] and w[0] in "\"'" else w for w in words]


def run_hook_command(name, event, extra_env=None, timeout=20, args=()):
    """Run the user's <name> command (an option such as on_save_command). Never raises.

    The command gets SAVE_CLEAR_EVENT=<event> plus extra_env. Returns (exit code or None, stdout)."""
    cmd = setting(name)
    if not cmd:
        return None, ""
    env = dict(os.environ)
    env["SAVE_CLEAR_EVENT"] = event
    env.update({k: str(v) for k, v in (extra_env or {}).items() if v is not None})
    try:
        r = subprocess.run(split_command(cmd) + list(args), env=env, timeout=timeout, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    except Exception as e:
        log(event, f"{name} failed: {e!r}")
        return None, ""
    if r.returncode != 0:
        log(event, f"{name} exited {r.returncode}: {(r.stderr or '').strip()[:200]}")
    else:
        log(event, f"{name} ran ({len(r.stdout or '')} bytes of output)")
    return r.returncode, (r.stdout or "").strip()


def save_config():
    """Called by the SessionStart hook: plugin options are in the hook's env, not in Bash's."""
    current = {}
    for name in OPTIONS:
        value = (os.environ.get("CLAUDE_PLUGIN_OPTION_" + name.upper()) or "").strip()
        if value:
            current[name] = value
    if current == _saved_config():
        return
    try:
        ensure_state()
        write_json(CONFIG, current)
    except OSError:
        pass


def snapshot_dir():
    return os.path.abspath(os.path.expanduser(setting("snapshot_dir") or os.path.join(HOME, ".claude", "snapshots")))


ON_VALUES = ("1", "on", "true", "yes")


def publish_link():
    return setting("publish_link").lower() in ON_VALUES


def kill_background_tasks():
    """The `kill_background_tasks` option: on = before the automatic /clear, Claude stops the background
    tasks this session left running. Anything but on/true/yes/1 (empty, null, a typo) = off."""
    return setting("kill_background_tasks").lower() in ON_VALUES


# Snapshot section where Claude lists the background tasks it stopped before the automatic /clear.
STOPPED_SECTION = "## Stopped background tasks"
# Snapshot section where Claude lists the subagents still running at the save. In a terminal they survive
# the /clear and their results reach the new conversation, which reads this section to know what each one
# is for; in the desktop app the clear waits for them and their results are added under "After the save".
RUNNING_SECTION = "## Running subagents"
# Snapshot section with the user's messages sent DURING the save and the answers given: the /clear wipes
# those answers from the screen, so the new session shows them first.
ANSWERS_SECTION = "## Answers given during the save"


def stop_tasks_instruction(when, snapshot=""):
    """What Claude is told to do when kill_background_tasks is on. Only Claude can stop its own
    background tasks (TaskStop); a hook cannot tell them apart from other processes, so it never kills."""
    return (f"kill_background_tasks: on. {when} stop every background task THIS session started that is still "
            "running (Bash commands run with run_in_background, Monitor watches): use the TaskStop tool "
            "(KillShell in older versions) with each task id. Never stop a subagent (an Agent tool task): it is "
            "work in progress (in a terminal it survives the /clear; in the desktop app the clear waits for it). "
            "Never stop anything this "
            "session did not start (other sessions, the user's own programs, servers it did not launch). "
            "Then add to the snapshot "
            + (f"{snapshot} " if snapshot else "") + "a "
            f"section \"{STOPPED_SECTION}\" (this heading in English, exactly, whatever the snapshot's language) "
            "with one line per task (id, what it was doing, whether it must be started again), or \"none\" if "
            "nothing was running.")


def claude_bin():
    return os.environ.get("SAVE_CLEAR_CLAUDE") or shutil.which("claude") or os.path.join(HOME, ".local", "bin", "claude")


def tmux_bin():
    found = shutil.which("tmux")
    if found:
        return found
    for p in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux", "/usr/bin/tmux"):
        if os.path.exists(p):
            return p
    return ""


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(tag, msg):
    try:
        ensure_state()
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{stamp()} [{tag}] {msg}\n")
    except OSError:
        pass


def write_json(path, value):
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f)
        f.write("\n")
    os.replace(tmp, path)


@contextlib.contextmanager
def locked(path):
    """Exclusive lock on <path> for the whole block (other processes wait)."""
    ensure_state()
    f = open(path, "a+", encoding="utf-8")
    try:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        else:
            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
        yield
    finally:
        try:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_UN)
            else:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        f.close()


def clear_key(session_id="", job=""):
    """File name of a session's auto-/clear request: the session id, or job-<id> for a --bg job."""
    key = f"job-{job}" if job else str(session_id or "")
    return re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:120]


def clear_path(key):
    return os.path.join(CLEAR_DIR, key + ".json")


def write_clear(flag):
    os.makedirs(CLEAR_DIR, exist_ok=True)
    write_json(clear_path(flag["key"]), flag)


def all_clears():
    try:
        names = sorted(n for n in os.listdir(CLEAR_DIR) if n.endswith(".json"))
    except OSError:
        return []
    return [f for f in (read_json(os.path.join(CLEAR_DIR, n)) for n in names) if isinstance(f, dict)]


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def resume_turn_marker(session_id):
    """Marker file: this session is in the first turn after a resume (no question dialog allowed)."""
    return os.path.join(RESUME_TURN_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", session_id) + ".json")


def now_ms():
    return int(time.time() * 1000)


def under_terminal():
    """True when an ancestor process has a terminal (a terminal `claude`); desktop app sessions have none.
    A terminal session can INHERIT CLAUDE_CODE_ENTRYPOINT / CLAUDE_CODE_HOST_SESSION_ID from a tmux server
    an app session started, so those variables alone do not prove an app session.
    SAVE_CLEAR_UNDER_TERMINAL=1/0 forces the answer (tests). Unknown (no ps, Windows) = False."""
    forced = os.environ.get("SAVE_CLEAR_UNDER_TERMINAL")
    if forced in ("0", "1"):
        return forced == "1"
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,tty="], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    procs = {}
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            procs[int(parts[0])] = (int(parts[1]), parts[2])
    p, seen = os.getpid(), set()
    while p in procs and p not in seen and p > 1:
        seen.add(p)
        ppid, tty = procs[p]
        if tty not in ("??", "?", "-"):
            return True
        p = ppid
    return False


def claude_ancestors():
    """How many processes from this one up are a `claude` (by the base name of their argv[0]); 0 if unknown."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,args="], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    procs = {}
    for ln in out.splitlines():
        parts = ln.split(None, 3)
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            procs[int(parts[0])] = (int(parts[1]), os.path.basename(parts[2]))
    p, seen, found = os.getpid(), set(), 0
    while p in procs and p not in seen and p > 1:
        seen.add(p)
        ppid, name = procs[p]
        found += name == "claude"
        p = ppid
    return found


def nested_claude():
    """True when two or more `claude` processes sit above this one: the caller runs under a claude that was
    started from another claude's tools (`claude -p`, an Agent SDK robot, a script). Such a child inherits
    the outer session's variables (CLAUDE_CODE_ENTRYPOINT, T3CODE_THREAD_ID), which then do not describe it.
    SAVE_CLEAR_NESTED_CLAUDE=1/0 forces the answer (tests). Unknown (no ps, Windows) = False."""
    forced = os.environ.get("SAVE_CLEAR_NESTED_CLAUDE")
    if forced in ("0", "1"):
        return forced == "1"
    return claude_ancestors() >= 2


def app_host(env=None):
    """The desktop app session id (local_...) of this session, or None in a terminal session."""
    env = os.environ if env is None else env
    host = env.get("CLAUDE_CODE_HOST_SESSION_ID") or None
    return None if host and under_terminal() else host


def short(s):
    return str(s or "?")[:8]


def notify_success():
    """The `notify_success` option: off = notify only when something goes wrong, not when the clear
    works. Anything but off/false/no/0 (empty, null, a typo) = on, the old behavior."""
    return setting("notify_success").lower() not in ("0", "off", "false", "no")


def notify(msg, success=False):
    """Optional: run the user's notify command with one line of text. Never fails.
    success=True marks a "it worked" message, skipped when notify_success is off."""
    cmd = setting("notify_command")
    if not cmd:
        return
    if success and not notify_success():
        log("notify", f"not sent (notify_success off): {msg}")
        return
    try:
        subprocess.run(split_command(cmd) + [msg], timeout=20, capture_output=True)
    except Exception as e:
        log("notify", f"notify command failed: {e}")


def read_hook_input():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}


def _text_of(content):
    """The text of a message; an image with no text counts as "[image]" (it is a message too)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        blocks = [b for b in content if isinstance(b, dict)]
        text = " ".join(b.get("text") or "" for b in blocks if b.get("type") == "text")
        if not text.strip() and any(b.get("type") == "image" for b in blocks):
            return "[image]"
        return text
    return ""


def _ts_ms(entry):
    try:
        return calendar.timegm(time.strptime(entry["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")) * 1000
    except Exception:
        return None


def tail_lines(path, max_bytes=3 * 1024 * 1024):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf8", "ignore").splitlines()
    except Exception:
        return []


def turn_over(transcript):
    """(ended, subagents): ended = the newest main-thread record is the turn's own end record
    (system/turn_duration), so no turn is running; subagents = background subagents it left running.

    While background work runs (a subagent, or a run_in_background command, also one a subagent
    started), `claude agents --json` keeps saying "busy" although the turn ended and the prompt is
    empty (checked on 2.1.322). A newer user or assistant record = a new turn started."""
    for line in reversed(tail_lines(transcript) if transcript else []):
        if '"timestamp"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("isSidechain"):
            continue
        if d.get("type") == "system" and d.get("subtype") == "turn_duration":
            try:
                return True, max(0, int(d.get("pendingBackgroundAgentCount") or 0))
            except (TypeError, ValueError):
                return True, 0
        if d.get("type") in ("user", "assistant"):
            return False, 0
    return False, 0


TASK_ID_RE = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")
SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.S)
STATUS_RE = re.compile(r"<status>\s*([^<\s]+)\s*</status>")


def subagent_reports_since(transcript, since_ms):
    """Subagent results that reached the main thread after since_ms: [(task id, status, summary)].

    Claude Code delivers a finished background subagent as a <task-notification> user record whose
    summary starts with "Agent"; background Bash commands use the same tag with another summary."""
    out, seen = [], set()
    for line in tail_lines(transcript) if transcript else []:
        if "task-notification" not in line or '"timestamp"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("type") != "user" or d.get("isSidechain"):
            continue
        t = _ts_ms(d)
        if t is None or t <= since_ms:
            continue
        txt = _text_of((d.get("message") or {}).get("content"))
        for block in txt.split("<task-notification>")[1:]:
            tid, summ = TASK_ID_RE.search(block), SUMMARY_RE.search(block)
            if not tid or not summ or not summ.group(1).strip().startswith("Agent"):
                continue
            if tid.group(1) in seen:
                continue
            seen.add(tid.group(1))
            st = STATUS_RE.search(block)
            out.append((tid.group(1), st.group(1) if st else "", " ".join(summ.group(1).split())[:200]))
    return out


# Background subagents and the desktop app's clear.
# A terminal /clear runs in the same process: background subagents keep running and report to the new
# conversation (seen on 2.1.322 and 2.1.330). The desktop app's clear_session does not: when the turn ends,
# the app ends the session's process and starts a new one (SessionStart source "startup"), and every subagent
# still running dies with the old process (seen on 2.1.330: four subagents stopped mid-work, results lost).
# So in the app the clear waits for them (hooks/clear_guard.py, hooks/stop.py).
APP_WAIT_DIR = os.path.join(STATE, "app-wait")  # app-wait/<session id>.json: a clear held for subagents
APP_WAIT_MAX_AGE_MS = 12 * 3600 * 1000            # an older hold is forgotten
REPORTING_MS = 120 * 1000  # finished this recently, result not in the conversation yet: still on its way
# A "running" subagent whose meta and transcript have not changed for this long is hung (or died without a
# trace): it is not waited for, so a stuck subagent never blocks the clear for the life of the process.
STALE_MS = int(os.environ.get("SAVE_CLEAR_SUBAGENT_STALE_MS") or 30 * 60 * 1000)
UNKNOWN_START_WINDOW_MS = 2 * 3600 * 1000  # process start unknown: active this recently = still running
# ps reports whole seconds, so the computed start is up to 1 s LATER than the real one: an agent of THIS
# process can look up to 1 s older than the start (pending side), never the other way (cut-off side).
START_MARGIN_MS = 2000
# A subagent whose own transcript was written this long after its last result (task notification, or the
# tool result of a foreground one) is at work again: continued with SendMessage, or resumed by the user from
# its transcript view ("it is running again", 2.1.319). Its meta keeps "finished": true and its first
# notification stays in the transcript (101 of 112 subagents with several notifications worked between
# them, seen on 23/11/2026). A finished one's last write is within 0.4 s of its notification (p90).
REVIVED_MS = 10 * 1000


def _etime_seconds(text):
    """`ps -o etime=` ([[dd-]hh:]mm:ss) in seconds, or None."""
    m = re.match(r"^\s*(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)\s*$", text or "")
    if not m:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def claude_pid():
    """This session's Claude Code process: CLAUDE_PID (Claude Code sets it for hooks and Bash), else the
    nearest ancestor whose command is claude. None when unknown (no ps, Windows)."""
    pid = (os.environ.get("CLAUDE_PID") or "").strip()
    if pid.isdigit():
        return int(pid)
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,comm="], capture_output=True, text=True, timeout=5,
                             env=dict(os.environ, LC_ALL="C")).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    procs = {}
    for ln in out.splitlines():
        parts = ln.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            procs[int(parts[0])] = (int(parts[1]), parts[2].strip())
    p, seen = os.getppid(), set()
    while p in procs and p not in seen and p > 1:
        seen.add(p)
        ppid, comm = procs[p]
        if os.path.basename(comm).lower() == "claude":
            return p
        p = ppid
    return None


def process_start_ms():
    """When this session's Claude Code process started (ms since the epoch), or None if unknown.
    SAVE_CLEAR_PROCESS_START_MS overrides it (tests; a non-number = unknown)."""
    forced = os.environ.get("SAVE_CLEAR_PROCESS_START_MS")
    if forced is not None:
        return int(forced) if forced.strip().isdigit() else None
    pid = claude_pid()
    if not pid:
        return None
    try:
        out = subprocess.run(["ps", "-o", "etime=", "-p", str(pid)], capture_output=True, text=True, timeout=5,
                             env=dict(os.environ, LC_ALL="C")).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    secs = _etime_seconds(out)
    return None if secs is None else now_ms() - secs * 1000


def transcript_of(session_id):
    """A session's main transcript (<config dir>/projects/<project>/<session id>.jsonl), or ""."""
    if not session_id:
        return ""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
    hits = glob.glob(os.path.join(base, "projects", "*", glob.escape(session_id) + ".jsonl"))
    return max(hits, key=os.path.getmtime) if hits else ""


def subagent_dir(transcript, session_id=""):
    """Where Claude Code keeps a session's subagents: <project>/<session id>/subagents, next to the
    session's transcript <project>/<session id>.jsonl."""
    if not transcript:
        return ""
    folder, name = os.path.split(transcript)
    sid = session_id or (name[:-len(".jsonl")] if name.endswith(".jsonl") else "")
    return os.path.join(folder, sid, "subagents") if sid else ""


def _notifications(transcripts, tool_ids=()):
    """(ended, read, returned) from the tails of these transcripts:
    ended: {task id: newest time (ms, 0 = no timestamp) a <task-notification> for it was written (the task
    ended; also "was resumed by the user"), or its hand-back}; read: {task id: newest time Claude read one}:
    a user record (it started a turn), the queue's `remove` record (taken in mid-turn; reason
    absorbed_mid_turn or none), or the subagent's hand-back: a user record whose origin has handback true and
    the agent id in `from` (its final report; terminal, 2.1.330). The `enqueue` record and the queued_command
    attachment are written as soon as the task ends, before Claude reads it.
    returned: {tool use id in tool_ids: time its tool result was written} (a foreground subagent returned)."""
    ended, read, returned = {}, {}, {}
    wanted = [t for t in tool_ids if t]
    for transcript in transcripts:
        for line in tail_lines(transcript) if transcript else []:
            note = "<task-notification>" in line or '"handback"' in line
            result = bool(wanted) and '"tool_result"' in line and any(t in line for t in wanted)
            if not (note or result):
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            t = _ts_ms(d) or 0
            if result and d.get("type") == "user":
                content = (d.get("message") or {}).get("content")
                for b in content if isinstance(content, list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in wanted:
                        returned[b["tool_use_id"]] = max(returned.get(b["tool_use_id"], 0), t)
            if not note:
                continue
            origin = d.get("origin") if isinstance(d.get("origin"), dict) else {}
            if d.get("type") == "user" and origin.get("handback") and origin.get("from"):
                k = str(origin["from"])
                ended[k] = max(ended.get(k, 0), t)
                read[k] = max(read.get(k, 0), t)
            is_read = d.get("type") == "user" or (d.get("type") == "queue-operation" and d.get("operation") == "remove"
                                                  and d.get("reason") in (None, "absorbed_mid_turn"))
            written = d.get("type") == "user" or (d.get("type") == "queue-operation" and d.get("operation") == "enqueue")
            for block in line.split("<task-notification>")[1:]:
                tid = TASK_ID_RE.search(block)
                if not tid:
                    continue
                k = tid.group(1)
                ended[k] = max(ended.get(k, 0), t if written else 0)
                if is_read:
                    read[k] = max(read.get(k, 0), t)
    return ended, read, returned


def _interrupted(path):
    """True when a subagent's transcript ends with "[Request interrupted by user...": it was stopped
    (TaskStop, or the user) and will not go on. Claude Code then writes neither "finished" nor
    "stoppedByUser" into its meta (seen on 23/11/2026)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        try:
            d = json.loads(line)
        except ValueError:
            continue  # a partial first line of the tail
        if not isinstance(d, dict):
            return False
        return d.get("type") == "user" and _text_of((d.get("message") or {}).get("content")).lstrip() \
            .startswith("[Request interrupted by user")
    return False


def app_sessions_dir():
    """Where the desktop app keeps one JSON per session (as in bin/save-clear-relay)."""
    env = os.environ.get("SAVE_CLEAR_APP_SESSIONS")
    if env:
        return os.path.expanduser(env)
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Claude/claude-code-sessions")
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code-sessions")
    return os.path.expanduser("~/.config/Claude/claude-code-sessions")


def app_session_info(host_id):
    """The desktop app's JSON for local_<id> (cliSessionId, remoteControlSpawn, remoteControlUserEnabled...),
    or {} when it cannot be found."""
    if not re.fullmatch(r"local_[0-9a-f-]{36}", host_id or ""):
        return {}
    try:
        hits = glob.glob(os.path.join(glob.escape(app_sessions_dir()), "**", host_id + ".json"), recursive=True)
        hits.sort(key=os.path.getmtime, reverse=True)
    except OSError:
        return {}
    for p in hits:
        v = read_json(p)
        if isinstance(v, dict):
            return v
    return {}


def earlier_conversations(transcript, session_id, host_id=None):
    """[(transcript, session id)] of the earlier conversation of this desktop app session that may still run in
    THIS process: a /clear typed in the app keeps the process (SessionStart source "clear", seen twice on
    11/11/2026) and its background subagents run on under the earlier session id, which the app's JSON still
    names as cliSessionId. A desktop-app clear_session would end them with the process."""
    host = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID") if host_id is None else host_id
    old = str(app_session_info(host).get("cliSessionId") or "")
    if not re.fullmatch(r"[0-9a-f-]{36}", old) or old == session_id or not transcript:
        return []
    tp = os.path.join(os.path.dirname(transcript), old + ".jsonl")
    if not os.path.exists(tp) and not os.path.isdir(os.path.join(os.path.dirname(transcript), old)):
        tp = transcript_of(old)
    return [(tp, old)] if tp else []


def subagents(transcript, session_id="", earlier=()):
    """A session's background subagents, read from Claude Code's agent-<id>.meta.json files:
    [{id, description, state, active_ms, delivered}], oldest activity first. state: "finished" (the meta
    says so, or its task notification is in the transcript), "stopped" (stopped by the user) or "running"
    (none of these: running, or killed with its process; or written again after its last result: continued).
    A foreground subagent ends inside its turn and is left out, unless it was continued after its tool
    result (SendMessage): then it runs on and reports like a background one (seen on 2.1.299).
    active_ms: last write to its meta or transcript. delivered: Claude read its newest result.
    earlier: more (transcript, session id) whose subagents count too (see earlier_conversations)."""
    convs = [(transcript, session_id)] + list(earlier)
    found = []
    for tp, sid in convs:
        folder = subagent_dir(tp, sid)
        try:
            names = os.listdir(folder) if folder else []
        except OSError:
            continue
        for n in names:
            if not (n.startswith("agent-") and n.endswith(".meta.json")):
                continue
            meta = read_json(os.path.join(folder, n))
            if isinstance(meta, dict) and meta.get("requestShape") in ("background", "foreground"):
                found.append((folder, n[len("agent-"):-len(".meta.json")], meta))
    if not found:
        return []
    tool_ids = [str(m.get("toolUseId") or "") for _, _, m in found if m.get("requestShape") == "foreground"]
    ended, read, returned = _notifications([tp for tp, _ in convs], tool_ids)
    out = []
    for folder, aid, meta in found:
        jsonl = os.path.join(folder, f"agent-{aid}.jsonl")
        times = []
        for p in (os.path.join(folder, f"agent-{aid}.meta.json"), jsonl):
            try:
                times.append(os.path.getmtime(p))
            except OSError:
                pass
        try:
            wrote = int(os.path.getmtime(jsonl) * 1000)
        except OSError:
            wrote = 0
        last = ended.get(aid, 0)
        if meta.get("requestShape") == "foreground":
            last = max(last, returned.get(str(meta.get("toolUseId") or ""), 0))
            if not last or (wrote <= last + REVIVED_MS and aid not in ended):
                continue  # still inside its turn, or returned and done: nothing runs on after the turn
        revived = bool(last) and wrote > last + REVIVED_MS
        if (meta.get("finished") or aid in ended) and not revived:
            state = "finished"
        elif (meta.get("stoppedByUser") and not revived) or _interrupted(jsonl):
            state = "stopped"
        else:
            state = "running"
        # The record that reads a notification can carry a slightly older timestamp than its enqueue (1 of
        # 2379 pairs a second earlier, real transcripts 23/11/2026): a few seconds of slack.
        out.append({"id": aid, "description": str(meta.get("description") or "")[:80], "state": state,
                    "active_ms": int(max(times) * 1000) if times else 0,
                    "delivered": aid in read and read[aid] >= ended.get(aid, 0) - 5000})
    return sorted(out, key=lambda a: a["active_ms"])


def pending_subagents(transcript, session_id="", start_ms=-1):
    """Subagents a desktop-app clear would cut off: [subagent + why]. why "running": still running in
    THIS process; "reporting": finished in the last REPORTING_MS, its result not in the conversation yet.
    One last active before this process started ran in a process that is gone (an app restart, a resumed
    session): it is not waited for. Includes the earlier conversation of the same app session when a typed
    /clear kept this process (earlier_conversations)."""
    start = process_start_ms() if start_ms == -1 else start_ms
    now = now_ms()
    out = []
    for a in subagents(transcript, session_id, earlier_conversations(transcript, session_id)):
        if a["state"] == "running":
            if now - a["active_ms"] >= STALE_MS:
                continue  # no write for STALE_MS: hung, not waited for
            if (a["active_ms"] >= start - START_MARGIN_MS) if start else (now - a["active_ms"] < UNKNOWN_START_WINDOW_MS):
                out.append(dict(a, why="running"))
        elif a["state"] == "finished" and not a["delivered"] and now - a["active_ms"] < REPORTING_MS:
            out.append(dict(a, why="reporting"))
    return out


def cut_off_subagents(transcript, session_id, start_ms=-1):
    """Subagents of an earlier conversation that never finished and were last active before THIS process
    started: a clear ended their process (desktop app), so they will never report. Unknown start = []."""
    start = process_start_ms() if start_ms == -1 else start_ms
    if not start:
        return []
    return [a for a in subagents(transcript, session_id)
            if a["state"] == "running" and a["active_ms"] < start]


def describe_subagents(agents, limit=8):
    """One line: id ("description", note), ... for messages to Claude."""
    notes = {"reporting": "just finished, its result is not in this conversation yet"}
    parts = []
    for a in agents[:limit]:
        extra = notes.get(a.get("why", ""), "")
        desc = f'"{a["description"]}"' if a.get("description") else ""
        inner = ", ".join(x for x in (desc, extra) if x)
        parts.append(a["id"] + (f" ({inner})" if inner else ""))
    more = len(agents) - limit
    return "; ".join(parts) + (f"; and {more} more" if more > 0 else "")


def app_wait_path(session_id):
    return os.path.join(APP_WAIT_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "") + ".json")


def read_app_wait(session_id, start_ms=None):
    """This session's hold ({session_id, ts, agents, reported}), or {}. With start_ms (this process's start),
    a hold made before this process started is {}: it was left by a process that is gone (the app quit or
    restarted during the wait) and its subagents died with it."""
    hold = read_json(app_wait_path(session_id)) if session_id else None
    if not isinstance(hold, dict):
        return {}
    if start_ms and int(hold.get("ts") or 0) < start_ms - START_MARGIN_MS:
        return {}
    return hold


def app_wait_expired(hold):
    return now_ms() - int(hold.get("ts") or 0) > APP_WAIT_MAX_AGE_MS


def mark_app_wait(session_id, agents, reported=None, start_ms=None):
    """Remember that THIS session's desktop-app clear is held for these subagents (the Stop hook asks
    Claude to finish the clear once none is pending). Keeps the first hold's time, unless that hold expired.
    reported: ids of "reporting" subagents Claude was already told about (the next clear goes through)."""
    if not session_id:
        return
    path = app_wait_path(session_id)
    old = read_app_wait(session_id, start_ms)
    if old and app_wait_expired(old):
        old = {}
    prev = old.get("agents") if isinstance(old.get("agents"), list) else []
    ts = old.get("ts") or now_ms()
    seen = list(dict.fromkeys([str(x) for x in prev] + [a["id"] for a in agents]))
    rep = old.get("reported") if isinstance(old.get("reported"), list) else []
    rep = list(dict.fromkeys([str(x) for x in rep] + [str(x) for x in (reported or [])]))
    try:
        os.makedirs(APP_WAIT_DIR, exist_ok=True)
        write_json(path, {"session_id": session_id, "ts": ts, "agents": seen[-50:], "reported": rep[-50:]})
    except OSError as e:
        log("app-wait", f"could not record the hold for {short(session_id)}: {e!r}")


RELAY_INBOX = os.path.join(STATE, "relay-inbox")  # bin/save-clear-relay's queue (INBOX there)


def hold_resume_requests(host_id):
    """A held clear never happens now, so the resume requests this desktop app session queued for it
    (step 8 runs `save-clear-relay request` before clear_session) must not be delivered: move them out of
    the relay's queue (relay-inbox/out/<name>.held). The real clear, later, queues a fresh one.
    Returns how many were moved."""
    if not host_id:
        return 0
    moved = 0
    out = os.path.join(RELAY_INBOX, "out")
    for folder in (RELAY_INBOX, os.path.join(RELAY_INBOX, "claimed")):
        for p in glob.glob(os.path.join(folder, "*.json")):
            job = read_json(p)
            if not (isinstance(job, dict) and job.get("why") == "save-clear" and job.get("to") == host_id):
                continue
            try:
                os.makedirs(out, exist_ok=True)
                os.replace(p, os.path.join(out, os.path.basename(p) + ".held"))
                moved += 1
            except OSError as e:
                log("app-wait", f"could not hold the resume request {os.path.basename(p)}: {e!r}")
    if moved:
        log("app-wait", f"{moved} resume request(s) of {host_id[6:14]} taken out of the relay queue: the clear is held")
    return moved


def release_resume_requests(host_id):
    """The clear goes through after a hold: put the newest resume request taken out by
    hold_resume_requests back in the relay's queue, as if made now, unless a newer one is already queued
    (Claude ran `save-clear-relay request` again). So a clear_session called without that step still gets
    its resume. Returns True when one was put back."""
    if not host_id:
        return False
    out = os.path.join(RELAY_INBOX, "out")
    held, queued = [], False
    for p in glob.glob(os.path.join(out, "*.json.held")):
        job = read_json(p)
        if isinstance(job, dict) and job.get("why") == "save-clear" and job.get("to") == host_id:
            held.append((float(job.get("created") or 0), p, job))
    for folder in (RELAY_INBOX, os.path.join(RELAY_INBOX, "claimed")):
        for p in glob.glob(os.path.join(folder, "*.json")):
            job = read_json(p)
            if isinstance(job, dict) and job.get("why") == "save-clear" and job.get("to") == host_id:
                queued = True
    if not held or queued:
        return False
    _, p, job = max(held, key=lambda h: h[0])
    now = time.time()
    job.update(created=now, due=now)
    job.pop("attempts", None)
    job.pop("claimed", None)
    name = f"{int(now * 1000)}-save-clear-{host_id[6:14]}.json"
    try:
        write_json(os.path.join(RELAY_INBOX, name), job)
        os.replace(p, p[:-len(".held")] + ".released")
    except OSError as e:
        log("app-wait", f"could not put the resume request of {host_id[6:14]} back: {e!r}")
        return False
    log("app-wait", f"resume request of {host_id[6:14]} back in the relay queue: the held clear goes through")
    return True


def desktop_app_session(env=None):
    """A Claude desktop app session (the only place with clear_session): a local_<id> host id, an entrypoint
    other than cli, and no terminal above it. IDE and SDK sessions have no local_ host id."""
    env = os.environ if env is None else env
    return bool(re.fullmatch(r"local_[0-9a-f-]{36}", env.get("CLAUDE_CODE_HOST_SESSION_ID") or "")) and \
        (env.get("CLAUDE_CODE_ENTRYPOINT") or "cli") != "cli" and not under_terminal()


def t3_session(env=None):
    """A session run by T3 Code (github.com/pingdotgg/t3code): it starts `claude` in SDK mode with
    CLAUDE_CODE_ENTRYPOINT=t3-code and T3CODE_THREAD_ID set. No tty, no tmux, no clear_session tool: a
    "clear" there is a new thread in the same project. A terminal below it (T3's built-in terminal) inherits
    the variables, so a terminal above the process means it is not the T3 session itself."""
    env = os.environ if env is None else env
    if not (env.get("T3CODE_THREAD_ID") and (env.get("CLAUDE_CODE_ENTRYPOINT") or "").startswith("t3")):
        return False
    # Inherited variables, as with an inherited claude-desktop entrypoint: a terminal above (a tmux pane, or
    # T3's own terminal, started from a T3 session) or a claude started by a claude (`claude -p`, a robot).
    # TMUX_PANE alone proves nothing (T3 itself may have been opened from a tmux shell): save-clear arm-clear
    # lets the pane win only when its check proves the pane runs this session.
    return not under_terminal() and not nested_claude()


def t3_cli(env=None):
    """Path of the T3 Code CLI (T3CODE_CLI_PATH, set by the app, else `t3` on PATH), or ""."""
    env = os.environ if env is None else env
    p = env.get("T3CODE_CLI_PATH") or ""
    if p and os.path.isfile(p) and os.access(p, os.X_OK):
        return p
    return shutil.which("t3", path=env.get("PATH")) or ""


def t3_start_command(cli, folder, message, title="", provider="", model=""):
    """argv for `t3 thread start`: a new thread in the project that holds `folder`, message from stdin.
    Provider and model go together (the CLI needs both, and a project without a default model needs them)."""
    cmd = [cli, "thread", "start", "--json"]
    if title:
        cmd += ["--title", title]
    if provider and model:
        cmd += ["--provider", provider, "--model", model]
    return cmd + [folder, "-"]


def drop_app_wait(session_id):
    try:
        os.unlink(app_wait_path(session_id))
        return True
    except OSError:
        return False


def user_messages_since(transcript, since_ms):
    """The user's own messages (typed or queued) in the transcript after since_ms.

    Queued messages count from their `queue-operation` enqueue record, even before Claude reads them.
    A later `remove` with a reason other than absorbed_mid_turn (the user took it back) cancels it."""
    out, cancelled = [], set()
    if not transcript:
        return out
    custom = resume_msg()
    for line in tail_lines(transcript):
        if '"queue-operation"' in line and '"remove"' in line:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("operation") == "remove" and d.get("reason") not in (None, "absorbed_mid_turn"):
                cancelled.add(str(d.get("content") or "").strip())
    for line in tail_lines(transcript):
        if '"timestamp"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("isSidechain") or d.get("isMeta") or d.get("isCompactSummary"):
            continue  # a compaction summary is a user-type record, but not the user's words
        t = _ts_ms(d)
        if t is None or t <= since_ms:
            continue
        txt = ""
        att = d.get("attachment") or {}
        if d.get("type") == "attachment" and att.get("type") == "queued_command":
            txt = att.get("prompt", "")
        elif d.get("type") == "queue-operation" and d.get("operation") == "enqueue":
            c = d.get("content")
            txt = c if isinstance(c, str) and c.strip() not in cancelled else ""
        elif d.get("type") == "user":
            c = (d.get("message") or {}).get("content")
            if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                continue
            txt = _text_of(c)
        txt = (txt or "").strip()
        if txt and not NOT_HUMAN.match(txt) and not is_resume_text(txt, custom) and txt not in out:
            out.append(txt)
    return out


# ----- tmux screen and process tree (shared by bin/save-clear-send and bin/save-clear-idle) -----
DIM = re.compile(r"\x1b\[2m.*?(\x1b\[(0|22)m|$)")    # the empty-prompt hint is dim; a real draft is not
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
BORDER = re.compile(r"^[\s\u2500-\u257f]*$")          # box-drawing lines around the prompt


def clean(line):
    return ANSI.sub("", DIM.sub("", line)).replace("\u00a0", " ")


def _prompt_line(lines):
    """Index of the input prompt's \u276f line, or None. The slash-command menu under the input box marks
    its selected item with \u276f too, indented and also right under a border (seen live:
    "  \u276f /clear  Start a new session..."). So the unindented \u276f line right under a box border
    wins; otherwise the last \u276f line (older layouts, test screens)."""
    marks = [i for i, ln in enumerate(lines) if "\u276f" in ln]
    for i in reversed(marks):
        above = clean(lines[i - 1]) if i else ""
        if "\u2500" in above and BORDER.match(above) and clean(lines[i]).startswith("\u276f"):
            return i
    return marks[-1] if marks else None


def prompt_text(text):
    """Text typed after the \u276f prompt (without the dim hint); None if there is no prompt."""
    lines = text.splitlines()
    i = _prompt_line(lines)
    if i is None:
        return None
    return clean(lines[i].split("\u276f", 1)[1]).strip().strip("\u2502").strip()


def prompt_block(text):
    """Everything after the \u276f prompt, wrapped lines included; None if there is no prompt."""
    lines = text.splitlines()
    i = _prompt_line(lines)
    if i is None:
        return None
    parts = [clean(lines[i].split("\u276f", 1)[1])]
    for ln in lines[i + 1:]:
        c = clean(ln)
        if not c.strip() or BORDER.match(c):
            break
        parts.append(c)
    return " ".join(p.strip().strip("\u2502").strip() for p in parts).strip()


def squash(t):
    return "".join((t or "").split())


def draft_why(screen_text):
    """Reason string for a non-empty prompt. Without colors (NO_COLOR in tmux) the dim hint cannot be
    told apart from a draft, so say so."""
    if "\x1b" not in screen_text:
        return "draft (screen has no colors: may be only the hint; check NO_COLOR in tmux)"
    return "draft"


def ps_parents():
    par = {}
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        out = ""
    for ln in out.splitlines():
        try:
            a, b = ln.split()
            par[int(a)] = int(b)
        except ValueError:
            pass
    return par


def ancestors(pid, par):
    out = []
    for _ in range(30):
        out.append(pid)
        pid = par.get(pid, 0)
        if pid in (0, 1):
            break
    return out
