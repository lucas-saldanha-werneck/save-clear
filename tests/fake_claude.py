#!/usr/bin/env python3
# A fake `claude agents --json`: describes who runs in the test pane, per scenario.
import json, os
D = os.environ["FAKE_DIR"]; sc = os.environ["FAKE_SCENARIO"]
A, B, C = os.environ["FAKE_A"], os.environ["FAKE_B"], os.environ["FAKE_C"]
p = os.path.join(D, "typed.txt")
typed = open(p).read().splitlines() if os.path.exists(p) else []
calls_file = os.path.join(D, "calls")
calls = int(open(calls_file).read()) + 1 if os.path.exists(calls_file) else 1
open(calls_file, "w").write(str(calls))
tui = int(open(os.path.join(D, "tui.pid")).read())
child = int(open(os.path.join(D, "child.pid")).read())
pid, sid = tui, A
if sc == "mismatch":
    sid = B
elif sc == "match":
    sid = C if "/clear" in typed else A
elif sc == "otherproc":
    if "/clear" in typed:
        pid, sid = child, C
elif sc == "switch":          # another session takes the pane after the first check
    sid = A if calls == 1 else B
elif sc == "switchlate":      # another session takes the pane while /clear sits in the prompt
    sid = A if calls <= 2 else B
print(json.dumps([{"kind": "interactive", "pid": pid, "sessionId": sid, "status": "idle", "cwd": D, "name": "fake"}]))
