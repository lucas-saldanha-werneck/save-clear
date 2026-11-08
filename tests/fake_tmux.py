#!/usr/bin/env python3
# A fake `tmux` for the unit tests. It never talks to a tmux server: it records every call and
# keeps a one-line prompt in a file, so capture-pane shows what was typed since the last Enter.
import json
import os
import sys
import time

D = os.environ.get("FAKE_TMUX_DIR")
if not D:
    sys.exit(1)
args = sys.argv[1:]
calls = os.path.join(D, "tmux-calls.jsonl")
with open(calls, "a") as f:
    f.write(json.dumps(args) + "\n")
draft_file = os.path.join(D, "draft.txt")
draft = open(draft_file).read() if os.path.exists(draft_file) else ""
cmd = args[0] if args else ""
if cmd == "capture-pane":
    sys.stdout.write("\x1b[0m\n❯ " + draft + "\n")
elif cmd == "send-keys":
    rest, literal, i = [], False, 1
    while i < len(args):
        if args[i] == "-t":
            i += 2
            continue
        if args[i] == "-l":
            literal = True
        else:
            rest.append(args[i])
        i += 1
    for k in rest:
        if k == "Enter" and not literal:
            if draft.strip() == "/clear" and os.environ.get("FAKE_TMUX_CLEARS") == "1":
                st = os.environ["FAKE_JOB_STATE"]
                with open(st) as f:
                    v = json.load(f)
                v["resumeSessionId"] = "new-conversation-after-clear"
                with open(st, "w") as f:
                    json.dump(v, f)
            draft = ""
        elif k == "C-z" and not literal:
            pass
        elif k == "BSpace" and not literal:
            draft = draft[:-1]
        else:
            draft += k
            queue = os.environ.get("FAKE_TMUX_QUEUE_ON_CLEAR")
            if queue and literal and draft.strip() == "/clear":  # a remote message lands during the pre-Enter delay
                with open(queue, "a") as f:
                    f.write(json.dumps({"type": "queue-operation", "operation": "enqueue",
                                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 1)),
                                        "content": "wait, do not clear yet"}) + "\n")
    with open(draft_file, "w") as f:
        f.write(draft)
sys.exit(0)
