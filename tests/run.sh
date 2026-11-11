#!/bin/bash
# tmux integration tests for the automatic /clear (about 1 minute). Nothing is typed into a real
# Claude session:
# - a test pane on a separate tmux server (own TMUX_TMPDIR) running a fake TUI;
# - a fake `claude agents --json` (SAVE_CLEAR_CLAUDE) that describes who runs in the pane;
# - flag and log in a temporary folder (SAVE_CLEAR_STATE_DIR).
# Skips (exit 0) when tmux is not installed.
T="$(cd "$(dirname "$0")" && pwd)"
R="$(dirname "$T")"
PY="${PYTHON:-python3}"
TM="$(command -v tmux || true)"
[ -n "$TM" ] || { echo "SKIP: tmux not installed"; exit 0; }
W="$(mktemp -d "${TMPDIR:-/tmp}/save-clear-test.XXXXXX")"
PASS=0; FAIL=0
ok()  { echo "  PASS: $*"; PASS=$((PASS+1)); }
bad() { echo "  FAIL: $*"; FAIL=$((FAIL+1)); }
A=aaaaaaaa-0000-0000-0000-00000000000a
B=bbbbbbbb-0000-0000-0000-00000000000b

# Own tmux server: the helper runs plain `tmux`, so point TMUX_TMPDIR at our folder.
export TMUX_TMPDIR="$(mktemp -d /tmp/sct.XXXX)"   # short path: sockets are limited to ~104 chars
unset TMUX TMUX_PANE
TMS="$TMUX_TMPDIR/tmux-$(id -u)/default"   # the test server's socket: kill-server only ever targets it
export SAVE_CLEAR_START_DELAY=0.5 CLAUDE_CODE_ENTRYPOINT=cli SAVE_CLEAR_CONFIG=/nonexistent/save-clear.json
unset CLAUDE_JOB_DIR CLAUDE_CODE_HOST_SESSION_ID
cleanup() { "$TM" -S "$TMS" kill-server 2>/dev/null; rm -rf "$W" "$TMUX_TMPDIR"; }
trap cleanup EXIT

new_case() {  # $1 = scenario
  D="$W/case-$1-$RANDOM"; mkdir -p "$D/state"
  export SAVE_CLEAR_STATE_DIR="$D/state" SAVE_CLEAR_CLAUDE="$T/fake_claude.py" FAKE_DIR="$D" FAKE_SCENARIO="$1"
  export FAKE_A=$A FAKE_B=$B FAKE_C=cccccccc-0000-0000-0000-00000000000c
  "$TM" -S "$TMS" kill-server 2>/dev/null
  # wait for the old server to go: on Linux a new client can reach the dying one ("server exited unexpectedly")
  for _ in $(seq 1 25); do [ -S "$TMS" ] || break; sleep 0.2; done
  "$TM" new-session -d -s sctest -x 160 -y 40 "bash $T/fake_tui.sh $D"
  sleep 1
  # guard: the server the helper sees must be ONLY the test one
  [ "$("$TM" list-panes -a -F '#{session_name}' | sort -u)" = "sctest" ] || { echo "ABORT: test tmux server not isolated"; exit 1; }
  PANE=$("$TM" list-panes -a -F '#{pane_id}' | head -1)
  rm -f "$D/calls"
}
typed() { cat "$D/typed.txt" 2>/dev/null; }
logf() { cat "$D/state/save-clear.log" 2>/dev/null; }
FL() { echo "$D/state/clear/$A.json"; }   # session A's own /clear request
flag_now() { mkdir -p "$D/state/clear"; printf '{"session_id":"%s","mode":"tmux","target":"%s","key":"%s","ts":%s}' "$A" "$PANE" "$A" "$(($(date +%s)*1000))" > "$(FL)"; }

echo "== T1: the pane runs ANOTHER session from the start -> refuse, nothing typed"
new_case mismatch
"$PY" "$R/bin/save-clear-send" tmux "$PANE" "$A" "{\"session_id\":\"$A\",\"ts\":1}" ""; rc=$?
[ $rc -eq 3 ] && ok "exit 3 (refused)" || bad "rc=$rc"
[ -z "$(typed)" ] && ok "no key reached the pane" || bad "typed: $(typed)"
logf | grep -q "REFUSED: the pane runs session bbbbbbbb" && ok "log has the refusal" || bad "no REFUSED line: $(logf)"
[ -f "$(FL)" ] && bad "re-armed the flag" || ok "flag not re-armed"

echo "== T2: right session -> /clear + resume request; confirmed with the SAME pid"
new_case match
"$PY" "$R/bin/save-clear-send" tmux "$PANE" "$A" "{\"session_id\":\"$A\",\"ts\":1}" ""; rc=$?
[ $rc -eq 0 ] && ok "exit 0" || bad "rc=$rc"
[ "$(typed | sed -n 1p)" = "/clear" ] && typed | sed -n 2p | grep -q '^save-clear: resuming' && ok "pane got /clear, then the resume request" || bad "typed: $(typed | tr '\n' '|')"
logf | grep -q "/clear confirmed (sessionId aaaaaaaa -> cccccccc, same process pid" && ok "log: confirmed in the same process" || bad "log: $(logf)"

echo "== T3: the new sessionId shows up in ANOTHER process in the pane -> not confirmed, refused"
new_case otherproc
"$PY" "$R/bin/save-clear-send" tmux "$PANE" "$A" "{\"session_id\":\"$A\",\"ts\":1}" ""; rc=$?
logf | grep -q "confirmed" && bad "confirmed with another process" || ok "not confirmed"
[ $rc -eq 3 ] && ok "exit 3" || bad "rc=$rc; log: $(logf)"
[ "$(typed | grep -c '^/clear$')" = 1 ] && ok "only 1 /clear typed" || bad "typed: $(typed | tr '\n' '|')"

echo "== T4: the pane switches session after the first check -> refuse before typing /clear"
new_case switch
"$PY" "$R/bin/save-clear-send" tmux "$PANE" "$A" "{\"session_id\":\"$A\",\"ts\":1}" ""; rc=$?
[ $rc -eq 3 ] && ok "exit 3" || bad "rc=$rc"
typed | grep -q '^/clear' && bad "typed /clear" || ok "/clear NOT typed"

echo "== T5: Stop hook in a desktop app session whose stale pane runs another session -> refuse and say so"
new_case mismatch
flag_now
OUT=$(echo "{\"session_id\":\"$A\",\"transcript_path\":\"\"}" | CLAUDE_CODE_ENTRYPOINT=claude-desktop "$PY" "$R/hooks/stop.py")
echo "$OUT" | grep -q 'did NOT clear' && ok "message: $OUT" || bad "output: $OUT"
[ -f "$(FL)" ] && bad "flag left behind" || ok "flag consumed"
sleep 2; [ -z "$(typed)" ] && ok "nothing typed" || bad "typed: $(typed)"

echo "== T6: flag of session A, but session B stops in the same pane -> ignored"
new_case match
flag_now
OUT=$(echo "{\"session_id\":\"$B\",\"transcript_path\":\"\"}" | TMUX_PANE="$PANE" "$PY" "$R/hooks/stop.py")
[ -z "$OUT" ] && ok "hook stayed quiet" || bad "output: $OUT"
[ -f "$(FL)" ] && ok "flag untouched (not this session's)" || bad "consumed another session's flag"
sleep 2; [ -z "$(typed)" ] && ok "nothing typed" || bad "typed: $(typed)"

echo "== T7: terminal Stop hook, but the pane runs another session -> --check refuses"
new_case mismatch
flag_now
OUT=$(echo "{\"session_id\":\"$A\",\"transcript_path\":\"\"}" | "$PY" "$R/hooks/stop.py")
echo "$OUT" | grep -q 'did NOT clear - refused: target' && ok "message: $OUT" || bad "output: $OUT"
sleep 2; [ -z "$(typed)" ] && ok "nothing typed" || bad "typed: $(typed)"

echo "== T8: terminal Stop hook, right session -> starts the helper, which clears"
new_case match
flag_now
OUT=$(echo "{\"session_id\":\"$A\",\"transcript_path\":\"\"}" | "$PY" "$R/hooks/stop.py")
echo "$OUT" | grep -q 'automatic /clear' && ok "hook started the helper" || bad "output: $OUT"
for _ in $(seq 1 30); do logf | grep -q 'sent /clear (confirmed)' && break; sleep 1; done
logf | grep -q "/clear confirmed (sessionId aaaaaaaa -> cccccccc, same process" && ok "helper confirmed in the same process" || bad "log: $(logf)"

echo "== T9: iterm mode (removed: the prompt cannot be checked there) -> refuse without osascript"
new_case match
"$PY" "$R/bin/save-clear-send" iterm "/dev/ttys001" "$A" "{}" ""; rc=$?
[ $rc -eq 3 ] && logf | grep -q "REFUSED: the iTerm2 mode was removed" && ok "refused" || bad "rc=$rc; log: $(logf)"

echo "== T10: arm-clear inside the test pane with the right session -> mode=tmux, flag written"
new_case match
OUT=$(TMUX_PANE="$PANE" CLAUDE_CODE_SESSION_ID=$A CLAUDE_PID=$$ "$PY" "$R/bin/save-clear" arm-clear)
echo "$OUT" | grep -q "mode=tmux target=$PANE" && [ -f "$(FL)" ] && ok "$(echo "$OUT" | head -1)" || bad "output: $OUT"

echo "== T10b: arm-clear with an inherited app entrypoint, in a pane that runs this session -> mode=tmux"
new_case match
OUT=$(TMUX_PANE="$PANE" CLAUDE_CODE_ENTRYPOINT=claude-desktop CLAUDE_CODE_SESSION_ID=$A CLAUDE_PID=$$ "$PY" "$R/bin/save-clear" arm-clear)
echo "$OUT" | grep -q "mode=tmux target=$PANE" && [ -f "$(FL)" ] && ok "$(echo "$OUT" | head -1)" || bad "output: $OUT"

echo "== T10c: inherited app entrypoint, pane runs another session -> mode=app, no flag"
new_case mismatch
OUT=$(TMUX_PANE="$PANE" CLAUDE_CODE_ENTRYPOINT=claude-desktop CLAUDE_CODE_SESSION_ID=$A CLAUDE_PID=$$ "$PY" "$R/bin/save-clear" arm-clear)
echo "$OUT" | grep -q "mode=app" && [ ! -f "$(FL)" ] && ok "$(echo "$OUT" | head -1)" || bad "output: $OUT"

echo "== T11: arm-clear in the test pane, but it runs another session -> falls back to manual, no flag"
new_case mismatch
OUT=$(TMUX_PANE="$PANE" CLAUDE_CODE_SESSION_ID=$A CLAUDE_PID=$$ "$PY" "$R/bin/save-clear" arm-clear)
echo "$OUT" | grep -q "mode=manual" && [ ! -f "$(FL)" ] && ok "$(echo "$OUT" | head -1)" || bad "output: $OUT"

echo "== T12: the pane switches session after /clear was typed, before Enter -> no Enter"
new_case switchlate
"$PY" "$R/bin/save-clear-send" tmux "$PANE" "$A" "{\"session_id\":\"$A\",\"ts\":1}" ""; rc=$?
[ $rc -eq 3 ] && ok "exit 3" || bad "rc=$rc; log: $(logf)"
typed | grep -q 'clear' && bad "Enter sent: $(typed | tr '\n' '|')" || ok "Enter NOT sent"

echo "== T13: the user types into the prompt after /clear was typed, before Enter -> no Enter"
new_case match
"$PY" "$R/bin/save-clear-send" tmux "$PANE" "$A" "{\"session_id\":\"$A\",\"ts\":1}" "" & HP=$!
for _ in $(seq 1 100); do "$TM" capture-pane -p -t "$PANE" | grep -q '❯ /clear' && break; sleep 0.05; done
"$TM" send-keys -t "$PANE" -l " and also"
wait $HP; rc=$?
typed | grep -q 'clear' && bad "submitted the edited draft: $(typed | tr '\n' '|')" || ok "edited draft NOT submitted"
logf | grep -q "Enter not sent" && ok "log says Enter not sent" || bad "log: $(logf)"

echo "== T14: a message is queued while /clear sits in the prompt, before Enter -> no Enter, re-armed"
new_case match
TP="$D/transcript.jsonl"; : > "$TP"
NOW=$(($(date +%s)*1000))
"$PY" "$R/bin/save-clear-send" tmux "$PANE" "$A" "{\"session_id\":\"$A\",\"mode\":\"tmux\",\"target\":\"$PANE\",\"key\":\"$A\",\"ts\":$NOW}" "$TP" & HP=$!
for _ in $(seq 1 100); do "$TM" capture-pane -p -t "$PANE" | grep -q '❯ /clear' && break; sleep 0.05; done
printf '{"type":"queue-operation","operation":"enqueue","timestamp":"%s","content":"wait, do not clear"}\n' "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)" >> "$TP"
wait $HP; rc=$?
typed | grep -q 'clear' && bad "Enter sent: $(typed | tr '\n' '|')" || ok "Enter NOT sent"
[ -f "$(FL)" ] && ok "request re-armed" || bad "not re-armed; log: $(logf)"
"$TM" capture-pane -p -t "$PANE" | grep '❯' | tail -1 | grep -q '/clear' && bad "/clear left in the prompt" || ok "typed /clear erased from the prompt"

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ]
