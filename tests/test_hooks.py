#!/usr/bin/env python3
"""Hook and command tests: sample hook JSON on stdin, check the output, exit code and files.

Everything runs in temporary folders (SAVE_CLEAR_STATE_DIR, SAVE_CLEAR_SNAPSHOT_DIR); no tmux,
no real Claude session. Run: python3 tests/test_hooks.py -v
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
SID_A = "aaaaaaaa-0000-0000-0000-00000000000a"
SID_B = "bbbbbbbb-0000-0000-0000-00000000000b"
SID_NEW = "cccccccc-0000-0000-0000-00000000000c"


def iso(offset_s=0):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + offset_s))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = os.path.realpath(self.tmp.name)
        self.state = os.path.join(t, "state")
        self.snaps = os.path.join(t, "snaps")
        self.proj = os.path.join(t, "project")
        os.makedirs(self.proj)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("CLAUDE", "TMUX", "SAVE_CLEAR", "TERM_PROGRAM", "LC_TERMINAL"))}
        self.env.update(SAVE_CLEAR_STATE_DIR=self.state, SAVE_CLEAR_SNAPSHOT_DIR=self.snaps,
                        SAVE_CLEAR_CLAUDE="/nonexistent/claude", SAVE_CLEAR_POINTER=os.path.join(t, "pointer"),
                        SAVE_CLEAR_APP_LOG=os.path.join(t, "app-main.log"))

    def tearDown(self):
        self.tmp.cleanup()

    def run_py(self, rel, args=(), stdin=None, **env):
        e = dict(self.env)
        e.update({k: str(v) for k, v in env.items()})
        r = subprocess.run([PY, os.path.join(ROOT, rel), *args], input=json.dumps(stdin) if stdin is not None else "",
                           capture_output=True, text=True, cwd=self.proj, env=e, timeout=60)
        return r

    def snapshot(self, body="# Session test\n## Last topic\nwe were testing\n"):
        os.makedirs(self.snaps, exist_ok=True)
        p = os.path.join(self.snaps, "SESSION-SUMMARY-20260101-000000-test.md")
        with open(p, "w") as f:
            f.write(f"---\nsession_id: {SID_A}\n---\n" + body)
        return p

    def arm(self, snap, **env):
        base = dict(CLAUDE_CODE_SESSION_ID=SID_A, CLAUDE_PID=4242, CLAUDE_PROJECT_DIR=self.proj)
        base.update(env)
        return self.run_py("bin/save-clear", ["arm-resume", snap], **base)

    def start(self, source="clear", sid=SID_NEW, cwd=None, **env):
        return self.run_py("hooks/session_start.py", stdin={"session_id": sid, "source": source, "cwd": cwd or self.proj},
                           **env)

    def flag_path(self, name):
        return os.path.join(self.state, name)


class Resume(Base):
    def test_arm_rejects_missing_snapshot(self):
        r = self.arm(os.path.join(self.snaps, "nope.md"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("NOT armed", r.stderr)
        self.assertFalse(os.path.exists(self.flag_path("pending_resume.json")))

    def test_clear_in_same_process_gets_summary_once(self):
        snap = self.snapshot()
        r = self.arm(snap)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("auto-resume armed", r.stdout)
        out = self.start(CLAUDE_PID=4242)
        ctx = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("we were testing", ctx)
        self.assertIn(f"Snapshot: `{snap}`", ctx)
        self.assertFalse(os.path.exists(self.flag_path("pending_resume.json")), "entry consumed")
        self.assertEqual(self.start(CLAUDE_PID=4242).stdout.strip(), "", "delivered only once")

    def test_other_process_other_folder_and_compact_do_not_take_it(self):
        self.arm(self.snapshot())
        self.assertEqual(self.start(CLAUDE_PID=9999).stdout.strip(), "")            # another terminal
        self.assertEqual(self.start(source="startup", CLAUDE_PID=4242).stdout.strip(), "")  # new process, not /clear
        other = os.path.join(os.path.dirname(self.proj), "elsewhere")
        os.makedirs(other)
        self.assertEqual(self.start(cwd=other, CLAUDE_PID=4242).stdout.strip(), "")  # another folder
        self.assertEqual(self.start(source="compact", sid=SID_A, CLAUDE_PID=4242).stdout.strip(), "")
        self.assertTrue(os.path.exists(self.flag_path("pending_resume.json")), "flag kept for the right session")
        self.assertIn("we were testing", self.start(CLAUDE_PID=4242).stdout)

    def test_desktop_app_matches_on_host_session(self):
        self.arm(self.snapshot(), CLAUDE_CODE_HOST_SESSION_ID="local_one", CLAUDE_CODE_ENTRYPOINT="claude-desktop")
        self.assertEqual(self.start(source="startup", CLAUDE_CODE_HOST_SESSION_ID="local_two").stdout.strip(), "")
        self.assertIn("we were testing", self.start(source="startup", CLAUDE_CODE_HOST_SESSION_ID="local_one").stdout)

    def test_two_sessions_queue(self):
        self.arm(self.snapshot())
        other = os.path.join(self.snaps, "SESSION-SUMMARY-other.md")
        with open(other, "w") as f:
            f.write("# other session\n")
        self.arm(other, CLAUDE_CODE_SESSION_ID=SID_B, CLAUDE_PID=5555)
        self.assertIn("other session", self.start(CLAUDE_PID=5555).stdout)
        self.assertIn("we were testing", self.start(CLAUDE_PID=4242).stdout)

    def test_summary_url_is_shown_first(self):
        snap = self.snapshot("summary_url: https://example.com/page/1\n# Session\n")
        self.arm(snap)
        ctx = json.loads(self.start(CLAUDE_PID=4242).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn('"Previous conversation: https://example.com/page/1"', ctx)

    def test_plugin_option_reaches_the_command(self):
        custom = os.path.join(os.path.dirname(self.snaps), "custom")
        env = {k: v for k, v in self.env.items() if k != "SAVE_CLEAR_SNAPSHOT_DIR"}
        self.env = env
        self.start(CLAUDE_PLUGIN_OPTION_SNAPSHOT_DIR=custom)  # hook env has the option
        r = self.run_py("bin/save-clear", ["dir"])           # Bash env does not
        self.assertEqual(r.stdout.splitlines()[0], custom)
        self.assertTrue(os.path.isdir(custom))


class Stop(Base):
    def write_flag(self, ts=None, **extra):
        os.makedirs(self.state, exist_ok=True)
        f = {"session_id": SID_A, "mode": "tmux", "target": "%99", "ts": ts or int(time.time() * 1000)}
        f.update(extra)
        with open(self.flag_path("pending_clear.json"), "w") as fh:
            json.dump(f, fh)

    def transcript(self, *entries):
        p = os.path.join(self.proj, "t.jsonl")
        with open(p, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        return p

    def test_no_flag_is_silent(self):
        r = self.run_py("hooks/stop.py", stdin={"session_id": SID_A})
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_old_flag_is_dropped(self):
        self.write_flag(ts=int(time.time() * 1000) - 20 * 60 * 1000)
        r = self.run_py("hooks/stop.py", stdin={"session_id": SID_A}, CLAUDE_CODE_ENTRYPOINT="cli")
        self.assertEqual(r.stdout, "")
        self.assertFalse(os.path.exists(self.flag_path("pending_clear.json")))

    def test_message_after_save_blocks_then_rearm(self):
        self.write_flag(mode="manual-test")  # a mode that skips the terminal check
        tp = self.transcript(
            {"type": "user", "timestamp": iso(-3600), "message": {"role": "user", "content": "old message"}},
            {"type": "user", "timestamp": iso(1), "message": {"role": "user", "content": "one more thing: rename it"}},
            {"type": "user", "timestamp": iso(1), "message": {"role": "user", "content": "<command-name>/x</command-name>"}},
        )
        r = self.run_py("hooks/stop.py", stdin={"session_id": SID_A, "transcript_path": tp})
        out = json.loads(r.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("one more thing: rename it", out["reason"])
        self.assertNotIn("old message", out["reason"])
        self.assertIn("## After the save", out["reason"])
        self.assertTrue(os.path.exists(self.flag_path("pending_clear.json")), "flag kept until the re-arm")
        r = self.run_py("bin/save-clear", ["rearm"])
        self.assertIn("round 1", r.stdout)
        with open(self.flag_path("pending_clear.json")) as f:
            self.assertEqual(json.load(f)["deltas"], 1)


class ArmClear(Base):
    def test_plain_terminal_falls_back_to_manual(self):
        r = self.run_py("bin/save-clear", ["arm-clear"], CLAUDE_CODE_SESSION_ID=SID_A, CLAUDE_CODE_ENTRYPOINT="cli",
                        TERM_PROGRAM="Apple_Terminal", CLAUDE_PID=os.getpid())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("mode=manual", r.stdout)
        self.assertIn("type /clear", r.stdout)
        self.assertFalse(os.path.exists(self.flag_path("pending_clear.json")))

    def test_desktop_app_never_types_into_inherited_pane(self):
        r = self.run_py("bin/save-clear", ["arm-clear"], CLAUDE_CODE_SESSION_ID=SID_A,
                        CLAUDE_CODE_ENTRYPOINT="claude-desktop", TMUX_PANE="%0")
        self.assertIn("mode=app", r.stdout)
        self.assertIn("clear_session", r.stdout)
        self.assertFalse(os.path.exists(self.flag_path("pending_clear.json")))

    def test_tmux_pane_that_cannot_be_checked_is_not_armed(self):
        # SAVE_CLEAR_CLAUDE points nowhere: the target cannot be confirmed, so no flag.
        r = self.run_py("bin/save-clear", ["arm-clear"], CLAUDE_CODE_SESSION_ID=SID_A, CLAUDE_CODE_ENTRYPOINT="cli",
                        TMUX_PANE="%987654")
        self.assertIn("mode=manual", r.stdout)
        self.assertFalse(os.path.exists(self.flag_path("pending_clear.json")))


class Compact(Base):
    def test_pre_and_post(self):
        tp = os.path.join(self.proj, "t.jsonl")
        with open(tp, "w") as f:
            f.write('{"type":"user"}\n')
        r = self.run_py("hooks/compact.py", ["pre"], stdin={"session_id": SID_A, "transcript_path": tp, "trigger": "auto"})
        self.assertIn("emergency snapshot saved", r.stdout)
        r = self.run_py("hooks/compact.py", ["post"], stdin={"session_id": SID_A, "compact_summary": "the summary"})
        self.assertIn("compaction summary kept", r.stdout)
        names = sorted(os.listdir(self.snaps))
        self.assertEqual([n.split("-")[0] for n in names], ["COMPACT", "EMERGENCY", "EMERGENCY"])
        self.assertTrue(any(n.endswith(".jsonl.gz") for n in names))

    def test_never_blocks_on_bad_input(self):
        r = subprocess.run([PY, os.path.join(ROOT, "hooks/compact.py"), "pre"], input="not json",
                           capture_output=True, text=True, env=self.env)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class ClearGuard(Base):
    def test_holds_once_then_allows(self):
        tp = os.path.join(self.proj, "t.jsonl")
        skill = "Base directory for this skill: /x/plugins/save-clear/skills/save-clear\n..."
        with open(tp, "w") as f:
            for e in ({"type": "user", "timestamp": iso(-60), "message": {"role": "user", "content": skill}},
                      {"type": "user", "timestamp": iso(-30), "message": {"role": "user", "content": "wait, also check X"}}):
                f.write(json.dumps(e) + "\n")
        hook = {"tool_input": {"session_id": "self"}, "transcript_path": tp}
        out = json.loads(self.run_py("hooks/clear_guard.py", stdin=hook).stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("wait, also check X", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertEqual(self.run_py("hooks/clear_guard.py", stdin=hook).stdout, "", "second call goes through")

    def test_other_targets_ignored(self):
        r = self.run_py("hooks/clear_guard.py", stdin={"tool_input": {"session_id": "local_x"}})
        self.assertEqual(r.stdout, "")


class StateDir(Base):
    def test_plugin_data_reaches_bash_through_pointer(self):
        data = os.path.join(os.path.dirname(self.state), "plugin-data")
        self.env.pop("SAVE_CLEAR_STATE_DIR")
        self.start(CLAUDE_PLUGIN_DATA=data)                      # hook env has it
        self.arm(self.snapshot())                                # Bash env does not
        self.assertTrue(os.path.exists(os.path.join(data, "pending_resume.json")))


APP = "local_11111111-1111-1111-1111-111111111111"
RELAY_ID = "local_22222222-2222-2222-2222-222222222222"


class Relay(Base):
    def relay(self, *args, **env):
        return self.run_py("bin/save-clear-relay", args, **env)

    def inbox(self):
        d = os.path.join(self.state, "relay-inbox")
        return sorted(f for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []

    def heartbeat(self, age=0):
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, "relay-listener.alive"), "w") as f:
            f.write(f"{os.getpid()} {int(time.time() - age)}\n")

    def app_log(self, *lines):
        with open(self.env["SAVE_CLEAR_APP_LOG"], "w") as f:
            f.write("".join(l + "\n" for l in lines))

    def test_no_relay_falls_back(self):
        r = self.relay("request", CLAUDE_CODE_HOST_SESSION_ID=APP)
        self.assertEqual(r.returncode, 4)
        self.assertIn("next message", r.stderr)
        self.assertEqual(self.inbox(), [])

    def test_app_mode_points_to_relay_and_fallback(self):
        r = self.run_py("bin/save-clear", ["arm-clear"], CLAUDE_CODE_ENTRYPOINT="claude-desktop",
                        CLAUDE_CODE_HOST_SESSION_ID=APP)
        self.assertIn("mode=app", r.stdout)
        self.assertIn("save-clear-relay request", r.stdout)

    def test_request_queues_and_reports_listening(self):
        self.assertEqual(self.relay("setup", RELAY_ID).returncode, 0)
        r = self.relay("request", CLAUDE_CODE_HOST_SESSION_ID=APP)          # no heartbeat
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(f"send_message to {RELAY_ID}", r.stderr)
        self.assertIn("RELAY listen", r.stderr)
        [name] = self.inbox()
        with open(os.path.join(self.state, "relay-inbox", name)) as f:
            job = json.load(f)
        self.assertEqual((job["to"], job["after_clear"]), (APP, APP))
        self.assertTrue(job["message"].startswith("save-clear: resuming"))
        self.heartbeat()
        self.assertEqual(self.relay("request", CLAUDE_CODE_HOST_SESSION_ID=APP).returncode, 0)
        self.heartbeat(age=120)                                              # stale heartbeat
        self.assertEqual(self.relay("request", CLAUDE_CODE_HOST_SESSION_ID=APP).returncode, 2)

    def test_off_file_refuses(self):
        self.relay("setup", RELAY_ID)
        self.relay("off")
        r = self.relay("request", CLAUDE_CODE_HOST_SESSION_ID=APP)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(self.inbox(), [])
        self.relay("on")
        self.assertEqual(self.relay("request", CLAUDE_CODE_HOST_SESSION_ID=APP).returncode, 2)

    def test_bad_ids_and_relay_itself(self):
        self.assertEqual(self.relay("job", "--to", "nope", "--message", "x").returncode, 1)
        self.assertEqual(self.relay("job", "--to", APP).returncode, 1)  # no message
        self.relay("setup", RELAY_ID)
        r = self.relay("request", CLAUDE_CODE_HOST_SESSION_ID=RELAY_ID)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Context full", r.stderr)

    def test_listener_waits_for_the_clear(self):
        self.relay("setup", RELAY_ID)
        self.relay("job", "--to", APP, "--message", "hello", "--after-clear", APP)
        self.app_log("2026-01-01 00:00:00 [info] something else")
        fast = dict(SAVE_CLEAR_RELAY_POLL="0.2", SAVE_CLEAR_RELAY_MAXWAIT="1")
        r = self.relay("listen", **fast)
        self.assertEqual(r.stdout.strip(), "IDLE")                          # no clear yet
        self.assertEqual(len(self.inbox()), 1)
        self.app_log(time.strftime("%Y-%m-%d %H:%M:%S") + f" [info] Clearing session {APP}")
        r = self.relay("listen", **fast)
        self.assertIn("JOB ", r.stdout)
        self.assertIn(f"session_id={APP}", r.stdout)
        self.assertIn("TEXT: hello", r.stdout)
        self.assertEqual(self.inbox(), [])

    def test_listener_without_app_log_uses_grace(self):
        self.relay("job", "--to", APP, "--message", "hi", "--after-clear", APP)
        r = self.relay("listen", SAVE_CLEAR_RELAY_POLL="0.2", SAVE_CLEAR_RELAY_MAXWAIT="2",
                       SAVE_CLEAR_RELAY_GRACE="0", SAVE_CLEAR_APP_LOG=os.path.join(self.proj, "missing.log"))
        self.assertIn("TEXT: hi", r.stdout)

    def test_waker_sees_relay_clear(self):
        self.relay("setup", RELAY_ID)
        self.app_log(f"2026-01-01 00:00:00 [info] Clearing session {RELAY_ID}")
        self.assertEqual(self.relay("clear-count").stdout.strip(), "1")
        self.app_log(*[f"2026-01-01 00:00:0{i} [info] Clearing session {RELAY_ID}" for i in range(2)])
        r = self.relay("wait-clear", "--base", "1", "--timeout", "5", SAVE_CLEAR_RELAY_POLL="0.1")
        self.assertTrue(r.stdout.startswith("CLEARED"), r.stdout)

    def test_rules_have_paths_filled_in(self):
        out = self.relay("rules").stdout
        self.assertIn("save-clear-relay\" listen", out)
        self.assertNotIn("{RELAY}", out)
        self.assertIn("## Context full", out)


if __name__ == "__main__":
    unittest.main()
