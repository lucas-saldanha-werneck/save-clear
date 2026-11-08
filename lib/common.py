"""Shared code for the save-clear hooks and commands. Standard-library Python 3.8+.

Files (all under the state folder: $SAVE_CLEAR_STATE_DIR, else $CLAUDE_PLUGIN_DATA, else ~/.claude/state/save-clear):
  pending_resume.json  who armed a resume, and which snapshot to deliver (one entry or a list)
  pending_resume.lock  held while pending_resume.json is read, changed and written back
  clear/<key>.json     one auto-/clear request per session (key = session id, or job-<id> for --bg)
  cap/<session>.json   context size when the token-limit hook last asked for a save-clear
  idle/<session>.done  last idle stretch the idle trigger (bin/save-clear-idle) already handled
  config.json          plugin options, copied here by the SessionStart hook so bin/ commands see them
  save-clear.log       one line per decision

Your own settings can also live in ~/.config/save-clear/config.json ($SAVE_CLEAR_CONFIG), outside the
plugin folder, so they survive updates. Precedence: plugin option > env var > that file.
The SessionStart hook (and the save-clear command) create that file from config.example.json the first
time, with every option at its default; an existing file is never overwritten.
"""
import calendar
import contextlib
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


def _resolve_state():
    for value in (os.environ.get("SAVE_CLEAR_STATE_DIR"), os.environ.get("CLAUDE_PLUGIN_DATA")):
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
    data = (os.environ.get("CLAUDE_PLUGIN_DATA") or "").strip()
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
}

# Your own settings, outside the plugin folder: they survive plugin updates and reinstalls.
# Same keys as the plugin options. Lowest precedence (plugin option and env var win).
USER_CONFIG = os.path.expanduser(os.environ.get("SAVE_CLEAR_CONFIG") or os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "save-clear", "config.json"))
# Starter copied to USER_CONFIG on first run: every option at its default (same behavior as no file).
EXAMPLE_CONFIG = os.path.join(ROOT, "config.example.json")


def seed_user_config():
    """Create USER_CONFIG from config.example.json if it does not exist yet. Never overwrites or
    changes an existing file (O_EXCL), never raises, prints nothing. True if it created the file."""
    try:
        if os.path.lexists(USER_CONFIG):
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

# Messages that are not the user's own words
NOT_HUMAN = re.compile(r"^(<|Stop hook feedback|Base directory for this skill|save-clear: resuming|"
                       r"save-clear \(automatic|\[Request interrupted|Caveat:)")
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
    """Plugin option (CLAUDE_PLUGIN_OPTION_<NAME>), then env var, then the copy in config.json,
    then the user's own config file."""
    for var in ("CLAUDE_PLUGIN_OPTION_" + name.upper(), OPTIONS.get(name, "")):
        value = (os.environ.get(var) or "").strip() if var else ""
        if value:
            return value
    return (str(_saved_config().get(name) or "").strip() or str(user_config().get(name) or "").strip()
            or default)


def resume_msg():
    """The resume request typed after /clear: the `resume_message` option, else the default."""
    return " ".join(setting("resume_message").split()) or RESUME_MSG


def is_resume_text(txt):
    """True for the default resume request or the configured one (the start of it is enough)."""
    t = (txt or "").lstrip()
    custom = resume_msg()
    return t.startswith(RESUME_MSG[:20]) or bool(custom) and t.startswith(custom[:20])


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
        subprocess.run(split_command(cmd) + [msg], timeout=20, capture_output=True)
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
    """The user's own messages (typed or queued) in the transcript after since_ms.

    Queued messages count from their `queue-operation` enqueue record, even before Claude reads them.
    A later `remove` with a reason other than absorbed_mid_turn (the user took it back) cancels it."""
    out, cancelled = [], set()
    if not transcript:
        return out
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
        if d.get("isSidechain") or d.get("isMeta"):
            continue
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
        if txt and not NOT_HUMAN.match(txt) and not is_resume_text(txt) and txt not in out:
            out.append(txt)
    return out


# ----- tmux screen and process tree (shared by bin/save-clear-send and bin/save-clear-idle) -----
DIM = re.compile(r"\x1b\[2m.*?(\x1b\[(0|22)m|$)")    # the empty-prompt hint is dim; a real draft is not
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
BORDER = re.compile(r"^[\s\u2500-\u257f]*$")          # box-drawing lines around the prompt


def clean(line):
    return ANSI.sub("", DIM.sub("", line)).replace("\u00a0", " ")


def prompt_text(text):
    """Text typed after the last \u276f prompt (without the dim hint); None if there is no prompt."""
    for ln in reversed(text.splitlines()):
        if "\u276f" in ln:
            return clean(ln.split("\u276f", 1)[1]).strip().strip("\u2502").strip()
    return None


def prompt_block(text):
    """Everything after the \u276f prompt, wrapped lines included; None if there is no prompt."""
    lines = text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if "\u276f" in lines[i]:
            parts = [clean(lines[i].split("\u276f", 1)[1])]
            for ln in lines[i + 1:]:
                c = clean(ln)
                if not c.strip() or BORDER.match(c):
                    break
                parts.append(c)
            return " ".join(p.strip().strip("\u2502").strip() for p in parts).strip()
    return None


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
