"""Shared code for the save-clear hooks and commands. Standard-library Python 3.8+.

Files (all under the state folder: $SAVE_CLEAR_STATE_DIR, else $CLAUDE_PLUGIN_DATA, else ~/.claude/state/save-clear):
  pending_resume.json  who armed a resume, and which snapshot to deliver (one entry or a list)
  pending_clear.json   the auto-/clear request the Stop hook acts on
  config.json          plugin options, copied here by the SessionStart hook so bin/ commands see them
  save-clear.log       one line per decision
"""
import calendar
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin")
HOME = os.path.expanduser("~")

DEFAULT_STATE = os.path.join(HOME, ".claude", "state", "save-clear")
# Hooks get $CLAUDE_PLUGIN_DATA; commands run from Bash may not. The SessionStart hook records the
# folder it used in this pointer file so every caller ends up in the same place.
POINTER = os.path.expanduser(os.environ.get("SAVE_CLEAR_POINTER") or os.path.join(HOME, ".claude", "state", "save-clear.path"))


def _resolve_state():
    for value in (os.environ.get("SAVE_CLEAR_STATE_DIR"), os.environ.get("CLAUDE_PLUGIN_DATA")):
        if value and value.strip():
            return os.path.expanduser(value.strip())
    try:
        with open(POINTER) as f:
            value = f.read().strip()
        if value and os.path.isdir(value):
            return value
    except OSError:
        pass
    return DEFAULT_STATE


STATE = _resolve_state()


def save_pointer():
    """SessionStart: remember $CLAUDE_PLUGIN_DATA for callers that do not have it."""
    data = (os.environ.get("CLAUDE_PLUGIN_DATA") or "").strip()
    if not data or os.environ.get("SAVE_CLEAR_STATE_DIR"):
        return
    try:
        with open(POINTER) as f:
            if f.read().strip() == data:
                return
    except OSError:
        pass
    try:
        os.makedirs(data, exist_ok=True)
        os.makedirs(os.path.dirname(POINTER), exist_ok=True)
        with open(POINTER, "w") as f:
            f.write(data + "\n")
    except OSError:
        pass


CONFIG = os.path.join(STATE, "config.json")
LOG = os.path.join(STATE, "save-clear.log")
RESUME_FLAG = os.path.join(STATE, "pending_resume.json")
CLEAR_FLAG = os.path.join(STATE, "pending_clear.json")

# Plugin option name -> environment variable name
OPTIONS = {
    "snapshot_dir": "SAVE_CLEAR_SNAPSHOT_DIR",
    "notify_command": "SAVE_CLEAR_NOTIFY",
    "publish_link": "SAVE_CLEAR_PUBLISH_LINK",
}

RESUME_MSG = ("save-clear: resuming. Start with the LAST topic (where we stopped and what is still open); "
              "then, in one short paragraph, the rest that was saved and the next steps.")

# Messages that are not the user's own words
NOT_HUMAN = re.compile(r"^(<|Stop hook feedback|Base directory for this skill|save-clear: resuming|"
                       r"save-clear \(automatic|\[Request interrupted|Caveat:)")
MARGIN_MS = 15000  # a message delivered just before the flag may not be in the snapshot


def ensure_state():
    os.makedirs(STATE, exist_ok=True)


def _saved_config():
    try:
        with open(CONFIG) as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def setting(name, default=""):
    """Plugin option (CLAUDE_PLUGIN_OPTION_<NAME>), then env var, then the copy in config.json."""
    for var in ("CLAUDE_PLUGIN_OPTION_" + name.upper(), OPTIONS.get(name, "")):
        value = (os.environ.get(var) or "").strip() if var else ""
        if value:
            return value
    return str(_saved_config().get(name) or "").strip() or default


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


def publish_link():
    return setting("publish_link").lower() in ("1", "on", "true", "yes")


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
        with open(LOG, "a") as f:
            f.write(f"{stamp()} [{tag}] {msg}\n")
    except OSError:
        pass


def write_json(path, value):
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(value, f)
        f.write("\n")
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def now_ms():
    return int(time.time() * 1000)


def short(s):
    return str(s or "?")[:8]


def notify(msg):
    """Optional: run the user's notify command with one line of text. Never fails."""
    cmd = setting("notify_command")
    if not cmd:
        return
    try:
        subprocess.run(shlex.split(cmd) + [msg], timeout=20, capture_output=True)
    except Exception as e:
        log("notify", f"notify command failed: {e}")


def read_hook_input():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
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


def user_messages_since(transcript, since_ms):
    """The user's own messages (typed or queued) in the transcript after since_ms."""
    out = []
    if not transcript:
        return out
    for line in tail_lines(transcript):
        if '"timestamp"' not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("isSidechain") or d.get("isMeta"):
            continue
        t = _ts_ms(d)
        if t is None or t <= since_ms:
            continue
        txt = ""
        att = d.get("attachment") or {}
        if d.get("type") == "attachment" and att.get("type") == "queued_command":
            txt = att.get("prompt", "")
        elif d.get("type") == "user":
            c = (d.get("message") or {}).get("content")
            if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                continue
            txt = _text_of(c)
        txt = (txt or "").strip()
        if txt and not NOT_HUMAN.match(txt) and txt not in out:
            out.append(txt)
    return out
