#!/bin/bash
# Schedule (or remove) the optional idle trigger, bin/save-clear-idle.
#
#   ./install.sh --idle               macOS: write and load a launchd job that runs it every 2 minutes
#                                     Linux: print the cron line to add (nothing is changed)
#   ./install.sh --idle --interval N  run every N seconds instead of 120 (Linux: whole minutes that divide the hour)
#   ./install.sh --uninstall          macOS: unload and delete the launchd job
#   --dry-run                         print what would be done, change nothing
#
# The launchd label is <prefix>.save-clear-idle; the prefix is $SAVE_CLEAR_LAUNCHD_PREFIX,
# default com.claude-tools. The job points at THIS folder: run the script again after moving it
# or after a plugin update changes the folder.
# How long a session must be idle is a setting, not part of the job: idle_minutes /
# $SAVE_CLEAR_IDLE_MINUTES (default 50, 0 = off). The job gets the PATH and CLAUDE_CONFIG_DIR (if set)
# of the shell that runs this script; its errors go to idle-job.log in the save-clear state folder.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/bin/save-clear-idle"
PREFIX="${SAVE_CLEAR_LAUNCHD_PREFIX:-com.claude-tools}"
LABEL="$PREFIX.save-clear-idle"
OS="${SAVE_CLEAR_INSTALL_OS:-$(uname -s)}"   # override only for tests
ACTION="" DRY=0 INTERVAL=120

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }
while [ $# -gt 0 ]; do
  case "$1" in
    --idle) ACTION=install ;;
    --uninstall) ACTION=uninstall ;;
    --dry-run) DRY=1 ;;
    --interval) shift; INTERVAL="${1:-}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "install.sh: unknown option $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done
[ -n "$ACTION" ] || { usage; exit 2; }
case "$INTERVAL" in ''|*[!0-9]*) echo "install.sh: --interval needs seconds" >&2; exit 2 ;; esac
[ "$INTERVAL" -ge 30 ] || { echo "install.sh: --interval must be at least 30 seconds" >&2; exit 2; }

PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "install.sh: python3 not found" >&2; exit 1; }

# The state folder save-clear uses (same rules as lib/common.py); the job's errors are logged there.
STATE_DIR="$("$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); import common; print(common.STATE)' "$HERE/lib" 2>/dev/null)"
[ -n "$STATE_DIR" ] || STATE_DIR="$HOME/.claude/state/save-clear"
ERR_LOG="$STATE_DIR/idle-job.log"

xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
# A value for the crontab line: single-quoted for sh, and % escaped (cron turns a bare % into a newline).
cronq() { printf "'%s'" "$(printf '%s' "$1" | sed -e "s/'/'\\\\''/g" -e 's/%/\\%/g')"; }

if [ "$OS" != "Darwin" ]; then
  if [ "$ACTION" = install ]; then
    # cron steps in whole minutes and */N must divide the hour cleanly to mean "every N minutes"
    # (*/45 runs at :00 and :45).
    MIN=$((INTERVAL / 60))
    if [ $((INTERVAL % 60)) -ne 0 ] || [ "$MIN" -gt 30 ] || [ $((60 % MIN)) -ne 0 ]; then
      echo "install.sh: on Linux (cron) --interval must be a multiple of 60 seconds that divides the hour" \
           "(60, 120, 180, 240, 300, 360, 600, 720, 900, 1200 or 1800); got $INTERVAL" >&2
      exit 2
    fi
    CFG=""
    [ -n "${CLAUDE_CONFIG_DIR:-}" ] && CFG="CLAUDE_CONFIG_DIR=$(cronq "$CLAUDE_CONFIG_DIR") "
    mkdir -p "$STATE_DIR"  # the line appends its errors there; cron would fail on a missing folder
    echo "launchd is macOS only. To run the idle trigger on this system, add this line with 'crontab -e':"
    echo
    echo "*/$MIN * * * * ${CFG}PATH=$(cronq "$(dirname "$PY")"):\$PATH:/usr/local/bin:/usr/bin:/bin $(cronq "$PY") $(cronq "$SCRIPT") >/dev/null 2>>$(cronq "$ERR_LOG")"
    echo
    echo "It needs tmux and the claude CLI on that PATH. Only the state folder was created ($STATE_DIR)."
  else
    echo "launchd is macOS only; remove the save-clear-idle line from 'crontab -e' if you added one."
  fi
  exit 0
fi

AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/$LABEL.plist"
DOMAIN="gui/$(id -u)"

# bootout returns before launchd finishes tearing the job down; a bootstrap right after can fail.
wait_gone() {
  local i=0
  while launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -le 20 ] || { echo "install.sh: $LABEL still loaded after 10 s" >&2; return 1; }
    sleep 0.5
  done
}

if [ "$ACTION" = uninstall ]; then
  if [ "$DRY" = 1 ]; then
    echo "would run: launchctl bootout $DOMAIN/$LABEL"
    echo "would delete: $PLIST"
    exit 0
  fi
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  wait_gone
  rm -f "$PLIST"
  echo "removed $LABEL"
  exit 0
fi

# The job's PATH: where python3, claude and tmux live right now, then the usual places.
DIRS="$(dirname "$PY")"
for tool in claude tmux; do
  p="$(command -v "$tool" || true)"
  if [ -n "$p" ]; then DIRS="$DIRS:$(dirname "$p")"; else echo "warning: $tool not found on PATH; the idle trigger skips every session until it is" >&2; fi
done
JOB_PATH="$DIRS:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
ENV_XML="    <key>PATH</key><string>$(xml "$JOB_PATH")</string>"
if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
  ENV_XML="$ENV_XML
    <key>CLAUDE_CONFIG_DIR</key><string>$(xml "$CLAUDE_CONFIG_DIR")</string>"
fi

PLIST_XML="<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
  <key>Label</key><string>$(xml "$LABEL")</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml "$PY")</string>
    <string>$(xml "$SCRIPT")</string>
  </array>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><false/>
  <key>EnvironmentVariables</key>
  <dict>
$ENV_XML
  </dict>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>$(xml "$ERR_LOG")</string>
</dict>
</plist>"

if [ "$DRY" = 1 ]; then
  echo "would write $PLIST:"
  echo "$PLIST_XML"
  echo "would run: launchctl bootstrap $DOMAIN $PLIST"
  exit 0
fi
mkdir -p "$AGENTS" "$STATE_DIR"
printf '%s\n' "$PLIST_XML" > "$PLIST"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
wait_gone
ok=0
for try in 1 2 3; do
  if launchctl bootstrap "$DOMAIN" "$PLIST"; then ok=1; break; fi
  [ "$try" -lt 3 ] && sleep 1
done
if [ "$ok" = 1 ]; then
  echo "installed $LABEL: runs $SCRIPT every $INTERVAL s"
  echo "Errors from the job: $ERR_LOG"
  echo "Idle limit: idle_minutes / SAVE_CLEAR_IDLE_MINUTES (default 50, 0 = off). Check it with: $SCRIPT --dry-run"
else
  echo "install.sh: launchctl bootstrap failed; the plist is at $PLIST" >&2
  exit 1
fi
