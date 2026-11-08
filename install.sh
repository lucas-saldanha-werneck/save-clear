#!/bin/bash
# Schedule (or remove) the optional idle trigger, bin/save-clear-idle.
#
#   ./install.sh --idle               macOS: write and load a launchd job that runs it every 2 minutes
#                                     Linux: print the cron line to add (nothing is changed)
#   ./install.sh --idle --interval N  run every N seconds instead of 120
#   ./install.sh --uninstall          macOS: unload and delete the launchd job
#   --dry-run                         print what would be done, change nothing
#
# The launchd label is <prefix>.save-clear-idle; the prefix is $SAVE_CLEAR_LAUNCHD_PREFIX,
# default com.claude-tools. The job points at THIS folder: run the script again after moving it
# or after a plugin update changes the folder.
# How long a session must be idle is a setting, not part of the job: idle_minutes /
# $SAVE_CLEAR_IDLE_MINUTES (default 50, 0 = off).
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

xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

if [ "$OS" != "Darwin" ]; then
  if [ "$ACTION" = install ]; then
    MIN=$(( (INTERVAL + 59) / 60 ))
    echo "launchd is macOS only. To run the idle trigger on this system, add this line with 'crontab -e':"
    echo
    echo "*/$MIN * * * * PATH=$(dirname "$PY"):\$PATH:/usr/local/bin:/usr/bin:/bin $PY $SCRIPT >/dev/null 2>&1"
    echo
    echo "It needs tmux and the claude CLI on that PATH. Nothing was changed."
  else
    echo "launchd is macOS only; remove the save-clear-idle line from 'crontab -e' if you added one."
  fi
  exit 0
fi

AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if [ "$ACTION" = uninstall ]; then
  if [ "$DRY" = 1 ]; then
    echo "would run: launchctl bootout $DOMAIN/$LABEL"
    echo "would delete: $PLIST"
    exit 0
  fi
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
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
    <key>PATH</key><string>$(xml "$JOB_PATH")</string>
  </dict>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>/dev/null</string>
</dict>
</plist>"

if [ "$DRY" = 1 ]; then
  echo "would write $PLIST:"
  echo "$PLIST_XML"
  echo "would run: launchctl bootstrap $DOMAIN $PLIST"
  exit 0
fi
mkdir -p "$AGENTS"
printf '%s\n' "$PLIST_XML" > "$PLIST"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if launchctl bootstrap "$DOMAIN" "$PLIST"; then
  echo "installed $LABEL: runs $SCRIPT every $INTERVAL s"
  echo "Idle limit: idle_minutes / SAVE_CLEAR_IDLE_MINUTES (default 50, 0 = off). Check it with: $SCRIPT --dry-run"
else
  echo "install.sh: launchctl bootstrap failed; the plist is at $PLIST" >&2
  exit 1
fi
