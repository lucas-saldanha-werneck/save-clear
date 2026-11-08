#!/usr/bin/env python3
"""Safety nets that run without the ritual. Never block a compaction (always exit 0).

  compact.py pre    PreCompact: gzip the full transcript + an EMERGENCY-COMPACT-<ts>-<sid>.md marker
  compact.py post   PostCompact: keep the summary Claude Code wrote as COMPACT-SUMMARY-<ts>-<sid>.md
"""
import datetime
import gzip
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from common import log, read_hook_input, snapshot_dir  # noqa: E402


def pre(hook, snap, ts, sid8, iso):
    tp = hook.get("transcript_path")
    if not tp or not os.path.isfile(tp):
        return None
    base = f"EMERGENCY-COMPACT-{ts}-{sid8}"
    with open(tp, "rb") as src, gzip.open(os.path.join(snap, base + ".jsonl.gz"), "wb") as dst:
        shutil.copyfileobj(src, dst)
    with open(os.path.join(snap, base + ".md"), "w", encoding="utf8") as f:
        f.write("\n".join([
            "---", "type: emergency-compact", f"session_id: {hook.get('session_id') or 'unknown'}",
            f"trigger: {hook.get('trigger') or 'unknown'}", f"original_transcript: {tp}", f"ts: {iso}", "---", "",
            "EMERGENCY snapshot from the PreCompact hook: the session compacted BEFORE a save-clear.",
            f"Automatic, NOT curated or verified. Full transcript: {base}.jsonl.gz (gunzip -k to open).", ""]))
    return f"save-clear: emergency snapshot saved ({base})"


def post(hook, snap, ts, sid8, iso):
    summary = str(hook.get("compact_summary") or hook.get("summary") or "")
    if not summary.strip():
        return None
    name = f"COMPACT-SUMMARY-{ts}-{sid8}.md"
    with open(os.path.join(snap, name), "w", encoding="utf8") as f:
        f.write("\n".join(["---", "type: compact-summary", f"session_id: {hook.get('session_id') or 'unknown'}",
                           f"trigger: {hook.get('trigger') or 'unknown'}", f"ts: {iso}", "---", "", summary, ""]))
    return f"save-clear: compaction summary kept ({name})"


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    hook = read_hook_input()
    snap = snapshot_dir()
    os.makedirs(snap, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    sid8 = str(hook.get("session_id") or "no-id")[:8]
    iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    msg = (pre if which == "pre" else post if which == "post" else (lambda *a: None))(hook, snap, ts, sid8, iso)
    if msg:
        print(json.dumps({"suppressOutput": True, "systemMessage": msg}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("compact", f"error: {e!r}")
    sys.exit(0)
