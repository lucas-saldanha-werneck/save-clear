#!/bin/bash
# A fake TUI: shows the ❯ prompt and records each line it receives (at most 200 lines).
D="$1"; export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
echo $$ > "$D/tui.pid"
sleep 600 & CH=$!; echo $CH > "$D/child.pid"; trap 'kill $CH 2>/dev/null' EXIT
for _ in $(seq 1 200); do printf '❯ '; IFS= read -r line || exit 0; printf '%s\n' "$line" >> "$D/typed.txt"; done
