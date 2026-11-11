#!/usr/bin/env python3
"""Hook and command tests: sample hook JSON on stdin, check the output, exit code and files.

Everything runs in temporary folders (SAVE_CLEAR_STATE_DIR, SAVE_CLEAR_SNAPSHOT_DIR); no tmux,
no real Claude session. Run: python3 tests/test_hooks.py -v
"""
import ast
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
with open(os.path.join(ROOT, "lib", "common.py"), encoding="utf-8") as _f:
    RESUME_MSG = next(ast.literal_eval(n.value) for n in ast.parse(_f.read()).body  # the text save-clear types
                      if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "RESUME_MSG")
SID_A = "aaaaaaaa-0000-0000-0000-00000000000a"
SID_B = "bbbbbbbb-0000-0000-0000-00000000000b"
SID_NEW = "cccccccc-0000-0000-0000-00000000000c"
# The automatic /clear types into a terminal through tmux, which does not run natively on Windows.
# There save-clear falls back to "type /clear yourself" (see ArmClear), so these paths are skipped.
NO_TMUX = sys.platform == "win32"
NO_TMUX_WHY = "automatic /clear needs tmux, which does not run natively on Windows"


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
                        SAVE_CLEAR_APP_LOG=os.path.join(t, "app-main.log"),
                        SAVE_CLEAR_CONFIG=os.path.join(t, "user-config.json"))
        # A fake tmux first on PATH: no test ever reaches a real tmux server.
        self.fakebin = os.path.join(t, "fakebin")
        os.makedirs(self.fakebin)
        with open(os.path.join(self.fakebin, "tmux"), "w") as f:
            f.write(f'#!/bin/sh\nexec "{PY}" "{ROOT}/tests/fake_tmux.py" "$@"\n')
        os.chmod(os.path.join(self.fakebin, "tmux"), 0o755)
        self.env.update(PATH=self.fakebin + os.pathsep + self.env.get("PATH", ""), FAKE_TMUX_DIR=t)
        self.t = t

    def tearDown(self):
        # Windows cannot delete a folder while a process still uses it: a helper the Stop hook
        # started in the background may need a moment to exit.
        for _ in range(50):
            try:
                self.tmp.cleanup()
                return
            except OSError:
                time.sleep(0.2)
        self.tmp.cleanup()

    def run_py(self, rel, args=(), stdin=None, **env):
        e = dict(self.env)
        e.update({k: str(v) for k, v in env.items()})
        r = subprocess.run([PY, os.path.join(ROOT, rel), *args], input=json.dumps(stdin) if stdin is not None else "",
                           capture_output=True, text=True, encoding="utf-8", cwd=self.proj, env=e, timeout=60)
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

    def test_ide_session_needs_the_same_process(self):
        # An IDE session has no app session id: only a /clear in the same process may take the summary.
        self.arm(self.snapshot(), CLAUDE_CODE_ENTRYPOINT="claude-vscode")
        self.assertEqual(self.start(source="startup", sid=SID_B, CLAUDE_CODE_ENTRYPOINT="claude-vscode",
                                    CLAUDE_PID=9999).stdout.strip(), "", "another IDE session in the same folder")
        self.assertEqual(self.start(CLAUDE_CODE_ENTRYPOINT="claude-vscode", CLAUDE_PID=9999).stdout.strip(), "")
        self.assertIn("we were testing", self.start(CLAUDE_CODE_ENTRYPOINT="claude-vscode", CLAUDE_PID=4242).stdout)

    def test_no_stable_identity_is_never_delivered_automatically(self):
        snap = self.snapshot()
        r = self.arm(snap, CLAUDE_PID="", CLAUDE_CODE_SESSION_ID="")
        self.assertIn("NOT delivered automatically", r.stdout)
        self.assertEqual(self.start(CLAUDE_PID=9999).stdout.strip(), "", "another session's /clear in the same folder")
        self.assertTrue(os.path.exists(self.flag_path("pending_resume.json")), "entry kept for its owner")

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


RACE = r"""
import os, sys, time
sys.path.insert(0, os.path.join(sys.argv[1], "lib"))
import resume
real = resume.read_entries
def slow():
    v = real()
    time.sleep(float(os.environ["RACE_SLEEP"]))
    return v
if os.environ.get("RACE_SLOW") == "1":
    resume.read_entries = slow
if sys.argv[2] == "arm":
    ok, msg = resume.arm(sys.argv[3])
    print(msg)
else:
    e, why = resume.take({"session_id": "x", "source": "clear", "cwd": os.environ["CLAUDE_PROJECT_DIR"]},
                         os.environ, resume.now_ms())
    print("took", e and e["snapshot"])
"""


class Concurrency(Base):
    def race(self, what, snap="", slow=False, **env):
        e = dict(self.env, RACE_SLEEP="0.8", RACE_SLOW="1" if slow else "0", CLAUDE_PROJECT_DIR=self.proj)
        e.update({k: str(v) for k, v in env.items()})
        return subprocess.Popen([PY, "-c", RACE, ROOT, what, snap], env=e, cwd=self.proj,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def entries(self):
        if not os.path.exists(self.flag_path("pending_resume.json")):
            return []
        with open(self.flag_path("pending_resume.json")) as f:
            v = json.load(f)
        return v if isinstance(v, list) else [v]

    def test_concurrent_arms_keep_every_entry(self):
        procs = []
        for i in range(5):
            snap = os.path.join(self.snaps, f"S-{i}.md")
            os.makedirs(self.snaps, exist_ok=True)
            with open(snap, "w") as f:
                f.write(f"# session {i}\n")
            procs.append(self.race("arm", snap, slow=True, CLAUDE_CODE_SESSION_ID=f"sess-{i}", CLAUDE_PID=7000 + i))
        for p in procs:
            p.communicate(timeout=60)
        self.assertEqual(len(self.entries()), 5, "every session's resume survives concurrent arms")

    def test_take_does_not_drop_an_entry_armed_meanwhile(self):
        self.arm(self.snapshot())                                   # A, pid 4242
        other = os.path.join(self.snaps, "S-B.md")
        with open(other, "w") as f:
            f.write("# B\n")
        taker = self.race("take", slow=True, CLAUDE_PID=4242)      # A's /clear: reads, then is slow
        time.sleep(0.3)
        armer = self.race("arm", other, CLAUDE_CODE_SESSION_ID=SID_B, CLAUDE_PID=5555)
        out, _ = taker.communicate(timeout=60)
        armer.communicate(timeout=60)
        self.assertIn("SESSION-SUMMARY", out, "A got its summary")
        self.assertEqual([e["snapshot"] for e in self.entries()], [other], "B's entry was not lost")


class Stop(Base):
    def write_flag(self, ts=None, **extra):
        os.makedirs(self.state, exist_ok=True)
        f = {"session_id": SID_A, "mode": "tmux", "target": "%99", "key": SID_A, "ts": ts or int(time.time() * 1000)}
        f.update(extra)
        os.makedirs(self.flag_path("clear"), exist_ok=True)
        with open(self.flag_path(f"clear/{SID_A}.json"), "w") as fh:
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
        self.assertFalse(os.path.exists(self.flag_path(f"clear/{SID_A}.json")))

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
        self.assertTrue(os.path.exists(self.flag_path(f"clear/{SID_A}.json")), "flag kept until the re-arm")
        self.assertIn(f"rearm {SID_A}", out["reason"], "the re-arm command names this session")
        r = self.run_py("bin/save-clear", ["rearm", SID_A])
        self.assertIn("round 1", r.stdout)
        with open(self.flag_path(f"clear/{SID_A}.json")) as f:
            self.assertEqual(json.load(f)["deltas"], 1)


    def test_queued_message_blocks_the_clear(self):
        self.write_flag(mode="manual-test")
        tp = self.transcript({"type": "queue-operation", "operation": "enqueue", "timestamp": iso(1),
                              "content": "Wait: do not clear"})
        r = self.run_py("hooks/stop.py", stdin={"session_id": SID_A, "transcript_path": tp})
        self.assertIn('"decision": "block"', r.stdout, r.stdout)
        self.assertIn("Wait: do not clear", r.stdout)

    def test_cancelled_queued_message_and_notifications_do_not_block(self):
        self.write_flag(mode="manual-test")
        tp = self.transcript(
            {"type": "queue-operation", "operation": "enqueue", "timestamp": iso(1), "content": "<task-notification>x"},
            {"type": "queue-operation", "operation": "enqueue", "timestamp": iso(1), "content": "never mind"},
            {"type": "queue-operation", "operation": "remove", "timestamp": iso(2), "content": "never mind",
             "reason": "cancelled"})
        r = self.run_py("hooks/stop.py", stdin={"session_id": SID_A, "transcript_path": tp})
        self.assertNotIn('"decision": "block"', r.stdout, r.stdout)


@unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
class ClearFlags(Base):
    """Each session has its own automatic-clear request."""
    def arm_bg(self, sid, job):
        return self.run_py("bin/save-clear", ["arm-clear"], CLAUDE_CODE_SESSION_ID=sid,
                           CLAUDE_JOB_DIR=os.path.join(self.t, "jobs", job), CLAUDE_CODE_ENTRYPOINT="cli")

    def flags(self):
        c = json.loads(self.run_py("bin/save-clear", ["status"]).stdout)["clear"]
        return c if isinstance(c, list) else [c] if c else []

    def stop_with_message(self, sid, job):
        tp = os.path.join(self.proj, f"t-{sid[:4]}.jsonl")
        with open(tp, "w") as f:
            f.write(json.dumps({"type": "user", "timestamp": iso(1),
                                "message": {"role": "user", "content": "one more thing"}}) + "\n")
        return self.run_py("hooks/stop.py", stdin={"session_id": sid, "transcript_path": tp},
                           CLAUDE_JOB_DIR=os.path.join(self.t, "jobs", job))

    def test_second_session_does_not_replace_the_first_request(self):
        self.assertIn("mode=bg", self.arm_bg(SID_A, "jobA").stdout)
        self.assertIn("mode=bg", self.arm_bg(SID_B, "jobB").stdout)
        self.assertEqual(sorted(f["session_id"] for f in self.flags()), [SID_A, SID_B])
        r = self.stop_with_message(SID_A, "jobA")
        self.assertIn('"decision": "block"', r.stdout, "A's Stop hook still finds A's request")

    def test_rearm_only_touches_the_callers_request(self):
        self.arm_bg(SID_A, "jobA")
        r = self.run_py("bin/save-clear", ["rearm"], CLAUDE_CODE_SESSION_ID=SID_B)
        self.assertEqual(r.returncode, 1, "B has no request of its own to re-arm")
        [a] = self.flags()
        self.assertEqual((a["session_id"], a.get("deltas", 0)), (SID_A, 0), "A's request untouched")
        r = self.run_py("bin/save-clear", ["rearm", SID_A])
        self.assertIn("round 1", r.stdout)


class ArmClear(Base):
    def test_iterm_without_tmux_falls_back_to_manual(self):
        # The helper cannot see whether an iTerm2 prompt is empty or idle, so it must not type there.
        code = (
            "import importlib.machinery, importlib.util, sys, os\n"
            "ld = importlib.machinery.SourceFileLoader('sc', os.path.join(sys.argv[1], 'bin', 'save-clear'))\n"
            "m = importlib.util.module_from_spec(importlib.util.spec_from_loader('sc', ld))\n"
            "ld.exec_module(m)\n"
            "m.tty_of = lambda pid: 'ttys001'\n"
            "m.is_iterm = lambda: True\n"
            "m.check = lambda *a: (0, 'ok: target runs this session')\n"
            "sys.exit(m.cmd_arm_clear([]))\n")
        e = dict(self.env, CLAUDE_CODE_SESSION_ID=SID_A, CLAUDE_CODE_ENTRYPOINT="cli", TERM_PROGRAM="iTerm.app")
        r = subprocess.run([PY, "-c", code, ROOT], capture_output=True, text=True, encoding="utf-8", env=e, cwd=self.proj, timeout=60)
        self.assertIn("mode=manual", r.stdout, r.stdout + r.stderr)
        self.assertIn("type /clear", r.stdout)
        self.assertEqual(json.loads(self.run_py("bin/save-clear", ["status"]).stdout)["clear"] or [], [])


    def test_plain_terminal_falls_back_to_manual(self):
        r = self.run_py("bin/save-clear", ["arm-clear"], CLAUDE_CODE_SESSION_ID=SID_A, CLAUDE_CODE_ENTRYPOINT="cli",
                        TERM_PROGRAM="Apple_Terminal", CLAUDE_PID=os.getpid())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("mode=manual", r.stdout)
        self.assertIn("type /clear", r.stdout)
        self.assertFalse(os.path.exists(self.flag_path(f"clear/{SID_A}.json")))

    def test_desktop_app_never_types_into_inherited_pane(self):
        r = self.run_py("bin/save-clear", ["arm-clear"], CLAUDE_CODE_SESSION_ID=SID_A,
                        CLAUDE_CODE_ENTRYPOINT="claude-desktop", TMUX_PANE="%0")
        self.assertIn("mode=app", r.stdout)
        self.assertIn("clear_session", r.stdout)
        self.assertFalse(os.path.exists(self.flag_path(f"clear/{SID_A}.json")))

    def test_tmux_pane_that_cannot_be_checked_is_not_armed(self):
        # SAVE_CLEAR_CLAUDE points nowhere: the target cannot be confirmed, so no flag.
        r = self.run_py("bin/save-clear", ["arm-clear"], CLAUDE_CODE_SESSION_ID=SID_A, CLAUDE_CODE_ENTRYPOINT="cli",
                        TMUX_PANE="%987654")
        self.assertIn("mode=manual", r.stdout)
        self.assertFalse(os.path.exists(self.flag_path(f"clear/{SID_A}.json")))


@unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
class BgHelper(Base):
    """`claude --bg` mode of the helper, against a fake tmux and a fake job state file."""
    JOB = "job12345"

    def run_bg(self, state="idle", messages=(), draft="", clears=False, raw=(), **env):
        home = os.path.join(self.t, "home")
        jd = os.path.join(home, ".claude", "jobs", self.JOB)
        os.makedirs(jd, exist_ok=True)
        st = os.path.join(jd, "state.json")
        with open(st, "w") as f:
            json.dump({"state": state, "resumeSessionId": SID_A}, f)
        if draft:
            with open(os.path.join(self.t, "draft.txt"), "w") as f:
                f.write(draft)
        tp = os.path.join(self.proj, "t.jsonl")
        with open(tp, "w") as f:
            for m in messages:
                f.write(json.dumps({"type": "user", "timestamp": iso(1), "message": {"role": "user", "content": m}}) + "\n")
            for e in raw:
                f.write(json.dumps(e) + "\n")
        self.tp = tp
        flag = {"session_id": SID_A, "mode": "bg", "target": self.JOB, "key": "job-" + self.JOB,
                "ts": int(time.time() * 1000)}
        r = self.run_py("bin/save-clear-send", ["bg", self.JOB, SID_A, json.dumps(flag), tp], HOME=home,
                        FAKE_JOB_STATE=st, FAKE_TMUX_CLEARS="1" if clears else "0", SAVE_CLEAR_BG_WAIT="2",
                        **{k: v.replace("{tp}", tp) for k, v in env.items()})
        calls = os.path.join(self.t, "tmux-calls.jsonl")
        keys = []
        if os.path.exists(calls):
            with open(calls) as f:
                keys = [a for a in (json.loads(l) for l in f) if a and a[0] == "send-keys"]
        return r, " ".join(" ".join(k) for k in keys)

    def test_job_that_never_goes_idle_is_not_typed_into(self):
        r, keys = self.run_bg(state="working")
        self.assertNotIn("/clear", keys)

    def test_busy_job_marks_the_rearmed_flag(self):
        # The flag the helper writes back says the session stayed busy, for kill_background_tasks.
        self.run_bg(state="working")
        with open(self.flag_path(f"clear/job-{self.JOB}.json")) as f:
            self.assertTrue(json.load(f).get("busy_rearm"))

    def test_message_rearm_is_not_marked_busy(self):
        self.run_bg(messages=["wait, one more thing"])
        with open(self.flag_path(f"clear/job-{self.JOB}.json")) as f:
            self.assertNotIn("busy_rearm", json.load(f))

    def test_message_after_the_save_stops_the_clear(self):
        r, keys = self.run_bg(messages=["wait, one more thing"])
        self.assertNotIn("/clear", keys)

    def test_draft_in_the_prompt_is_not_submitted(self):
        r, keys = self.run_bg(draft="half typed")
        self.assertNotIn("/clear", keys)

    def test_resume_request_only_after_a_confirmed_clear(self):
        r, keys = self.run_bg(clears=False)
        self.assertIn("/clear", keys)
        self.assertNotIn("save-clear: resuming", keys, "clear not confirmed: no resume request typed")
        os.unlink(os.path.join(self.t, "tmux-calls.jsonl"))
        r, keys = self.run_bg(clears=True)
        self.assertIn("save-clear: resuming", keys)


    def test_queued_message_stops_the_clear(self):
        r, keys = self.run_bg(raw=[{"type": "queue-operation", "operation": "enqueue", "timestamp": iso(1),
                                    "content": "Wait: do not clear"}])
        self.assertNotIn("/clear", keys)

    def test_message_during_the_pre_enter_delay_withholds_enter(self):
        r, keys = self.run_bg(clears=True, FAKE_TMUX_QUEUE_ON_CLEAR="{tp}")
        self.assertIn("/clear", keys, "typed /clear, then the message arrived")
        self.assertNotIn("/clear Enter", keys.replace("-l /clear send-keys", "/clear"))
        with open(os.path.join(self.t, "tmux-calls.jsonl")) as f:
            calls = [json.loads(l) for l in f]
        sends = [c for c in calls if c[0] == "send-keys"]
        i = next(n for n, c in enumerate(sends) if c[-1] == "/clear")
        self.assertNotIn("Enter", [c[-1] for c in sends[i:]], "Enter not pressed after /clear")
        self.assertTrue(os.path.exists(self.flag_path("clear/job-" + self.JOB + ".json")), "request re-armed")

    def test_attach_command_survives_spaces_and_shell_characters(self):
        tools = os.path.join(self.t, "my tools;touch PWNED")
        os.makedirs(tools)
        fake = os.path.join(tools, "claude")
        out = os.path.join(self.t, "attach-args")
        with open(fake, "w") as f:
            f.write(f'#!/bin/sh\necho "$@" > "{out}"\n')
        os.chmod(fake, 0o755)
        self.run_bg(SAVE_CLEAR_CLAUDE=fake)
        with open(os.path.join(self.t, "tmux-calls.jsonl")) as f:
            [new] = [json.loads(l) for l in f if json.loads(l)[0] == "new-session"]
        subprocess.run(["sh", "-c", new[-1]], cwd=self.t, timeout=10)  # what tmux does with the command string
        self.assertFalse(os.path.exists(os.path.join(self.t, "PWNED")), "no shell syntax ran from the path")
        with open(out) as f:
            self.assertEqual(f.read().strip(), "attach " + self.JOB)

    def test_custom_resume_message_and_pre_clear_in_bg_mode(self):
        log = os.path.join(self.t, "hook-log.jsonl")
        script = os.path.join(self.t, "hook.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(HOOK_SCRIPT)
        with open(self.env["SAVE_CLEAR_CONFIG"], "w", encoding="utf-8") as f:
            json.dump({"resume_message": PT_RESUME, "pre_clear_command": f'"{PY}" "{script}"'}, f,
                      ensure_ascii=False)
        r, keys = self.run_bg(clears=True, HOOK_LOG=log)
        self.assertIn(PT_RESUME, keys)
        self.assertNotIn("save-clear: resuming", keys)
        with open(log, encoding="utf-8") as f:
            [call] = [json.loads(l) for l in f]
        self.assertEqual((call["SAVE_CLEAR_EVENT"], call["SAVE_CLEAR_MODE"], call["SAVE_CLEAR_TARGET"]),
                         ("pre_clear", "bg", self.JOB))


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
        self.start(CLAUDE_PLUGIN_DATA=data, CLAUDE_PLUGIN_ROOT=ROOT)  # hook env has it
        self.arm(self.snapshot())                                # Bash env does not
        self.assertTrue(os.path.exists(os.path.join(data, "pending_resume.json")))

    def test_other_plugins_data_folder_in_bash_env_is_ignored(self):
        # Another plugin's SessionStart hook can export its own CLAUDE_PLUGIN_DATA into the session
        # (CLAUDE_ENV_FILE), so every Bash command carries a folder that is not ours.
        data = os.path.join(os.path.dirname(self.state), "plugin-data")
        other = os.path.join(os.path.dirname(self.state), "codex-openai-codex")
        self.env.pop("SAVE_CLEAR_STATE_DIR")
        self.start(CLAUDE_PLUGIN_DATA=data, CLAUDE_PLUGIN_ROOT=ROOT)
        self.arm(self.snapshot(), CLAUDE_PLUGIN_DATA=other)
        self.assertTrue(os.path.exists(os.path.join(data, "pending_resume.json")))
        self.assertFalse(os.path.exists(os.path.join(other, "pending_resume.json")))

    def test_hook_with_another_plugins_data_does_not_move_the_pointer(self):
        data = os.path.join(os.path.dirname(self.state), "plugin-data")
        other = os.path.join(os.path.dirname(self.state), "codex-openai-codex")
        self.env.pop("SAVE_CLEAR_STATE_DIR")
        self.start(CLAUDE_PLUGIN_DATA=data, CLAUDE_PLUGIN_ROOT=ROOT)
        self.start(CLAUDE_PLUGIN_DATA=other)                     # no CLAUDE_PLUGIN_ROOT of ours
        with open(self.env["SAVE_CLEAR_POINTER"], encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), data)


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

    def test_listener_keeps_request_until_ack(self):
        self.relay("setup", RELAY_ID)
        self.relay("job", "--to", APP, "--message", "hello")
        fast = dict(SAVE_CLEAR_RELAY_POLL="0.2", SAVE_CLEAR_RELAY_MAXWAIT="1")
        self.assertIn("TEXT: hello", self.relay("listen", **fast).stdout)
        st = json.loads(self.relay("status").stdout)
        self.assertEqual(st.get("unacked"), 1, "handed out but not acknowledged: still pending")
        # the relay turn was lost: after the claim times out, the same request is handed out again
        r = self.relay("listen", SAVE_CLEAR_RELAY_CLAIM_S="0", **fast)
        self.assertIn("TEXT: hello", r.stdout)
        name = [l.split()[-2] for l in r.stdout.splitlines() if l.strip().startswith("ACK:")][0]
        self.assertEqual(self.relay("ack", name, "ok").returncode, 0)
        st = json.loads(self.relay("status").stdout)
        self.assertEqual((st["queued"], st["unacked"], st["failed"]), (0, 0, 0))

    def test_failed_deliveries_are_retried_then_kept_as_failed(self):
        self.relay("job", "--to", APP, "--message", "hello")
        fast = dict(SAVE_CLEAR_RELAY_POLL="0.2", SAVE_CLEAR_RELAY_MAXWAIT="1")
        for attempt in range(3):
            r = self.relay("listen", **fast)
            self.assertIn("TEXT: hello", r.stdout, f"attempt {attempt + 1}")
            name = [l.split()[-2] for l in r.stdout.splitlines() if l.strip().startswith("ACK:")][0]
            self.relay("ack", name, "failed")
        self.assertEqual(self.relay("listen", **fast).stdout.strip(), "IDLE", "no 4th attempt")
        st = json.loads(self.relay("status").stdout)
        self.assertEqual((st["queued"], st["unacked"], st["failed"]), (0, 0, 1))

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


class RelayAuto(Base):
    """`save-clear-relay ensure`: the relay sets itself up in the desktop app, never in a terminal."""
    def setUp(self):
        super().setUp()
        self.sessions = os.path.join(self.t, "app-sessions")
        os.makedirs(os.path.join(self.sessions, "acct", "org"))
        self.calls = os.path.join(self.t, "claude-calls.txt")
        # fake `claude -p`: records its arguments
        fake = os.path.join(self.t, "fake_claude_p.py")
        with open(fake, "w") as f:
            f.write("import sys\nopen(sys.argv[1], 'a').write(' '.join(sys.argv[2:]) + '\\n')\nprint('relay ready')\n")
        # fake deep-link opener: the app imports the session (log line + session file)
        opener = os.path.join(self.t, "fake_open.py")
        with open(opener, "w") as f:
            f.write("import json, os, sys, time\n"
                    "log, sess, url = sys.argv[1], sys.argv[2], sys.argv[3]\n"
                    "cli = url.split('session=')[1]\n"
                    "open(log, 'a').write(time.strftime('%Y-%m-%d %H:%M:%S') + "
                    "f' [info] Resume deep link: imported {cli} as Desktop session local_{cli}\\n')\n"
                    "json.dump({'sessionId': 'local_' + cli, 'isArchived': False, 'model': 'claude-haiku-x', "
                    "'permissionMode': os.environ.get('FAKE_MODE', 'bypassPermissions'), 'title': 'relay ready'}, "
                    "open(os.path.join(sess, 'local_' + cli + '.json'), 'w'))\n")
        if sys.platform == "win32":
            claude = os.path.join(self.t, "claude.cmd")
            with open(claude, "w") as f:
                f.write(f'@"{PY}" "{fake}" "{self.calls}" %*\n')
        else:
            claude = os.path.join(self.t, "claude")
            with open(claude, "w") as f:
                f.write(f'#!/bin/sh\nexec "{PY}" "{fake}" "{self.calls}" "$@"\n')
            os.chmod(claude, 0o755)
        self.env.update(SAVE_CLEAR_CLAUDE=claude, SAVE_CLEAR_APP_SESSIONS=self.sessions,
                        SAVE_CLEAR_OPEN=f'"{PY}" "{opener}" "{self.env["SAVE_CLEAR_APP_LOG"]}" '
                                        f'"{os.path.join(self.sessions, "acct", "org")}"',
                        SAVE_CLEAR_RELAY_IMPORT_S="5", SAVE_CLEAR_RELAY_POLL="0.1")
        self.app_session(APP, permissionMode="bypassPermissions")

    def app_session(self, sid, **fields):
        v = {"sessionId": sid, "isArchived": False}
        v.update(fields)
        with open(os.path.join(self.sessions, "acct", "org", sid + ".json"), "w") as f:
            json.dump(v, f)

    def ensure(self, **env):
        e = dict(CLAUDE_CODE_ENTRYPOINT="claude-desktop", CLAUDE_CODE_HOST_SESSION_ID=APP)
        e.update(env)
        return self.run_py("bin/save-clear-relay", ["ensure"], **e)

    def created(self):
        try:
            with open(self.calls) as f:
                return f.read().splitlines()
        except OSError:
            return []

    def relay_json(self):
        p = os.path.join(self.state, "relay.json")
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return json.load(f)

    def test_no_relay_created_once(self):
        r = self.ensure()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("relay created: local_", r.stdout)
        [call] = self.created()
        self.assertIn("-p --session-id", call)
        self.assertIn("--model haiku", call)
        self.assertIn("--permission-mode bypassPermissions", call, "same permission mode as the caller")
        rid = self.relay_json()["local_id"]
        self.assertIn(f'set_session_title session_id={rid} title="Don\'t touch it, relay"', r.stdout)
        self.assertIn("request", r.stdout)
        # again: the same relay, nothing created
        r2 = self.ensure()
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn(f"relay ready: {rid}", r2.stdout)
        self.assertEqual(len(self.created()), 1, "never two relays")
        self.assertEqual(self.relay_json()["local_id"], rid)
        # request then queues for it and says how to wake it
        q = self.run_py("bin/save-clear-relay", ["request"], CLAUDE_CODE_HOST_SESSION_ID=APP)
        self.assertEqual(q.returncode, 2, q.stderr)
        self.assertIn(f"send_message to {rid}", q.stderr)

    def test_custom_title_and_model(self):
        r = self.ensure(SAVE_CLEAR_RELAY_TITLE="Relay X", SAVE_CLEAR_RELAY_MODEL="sonnet")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('title="Relay X"', r.stdout)
        self.assertIn("--model sonnet", self.created()[0])
        self.assertIn("set_session_model", r.stdout, "the fake app reports haiku, so it asks to set sonnet")

    def test_plan_mode_caller_gives_default_mode(self):
        self.app_session(APP, permissionMode="plan")
        self.ensure(FAKE_MODE="default")
        self.assertIn("--permission-mode default", self.created()[0])

    def test_archived_relay_reported_and_not_used(self):
        self.app_session(RELAY_ID, isArchived=True)
        self.assertEqual(self.run_py("bin/save-clear-relay", ["setup", RELAY_ID]).returncode, 1,
                         "setup refuses an archived session")
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, "relay.json"), "w") as f:
            json.dump({"local_id": RELAY_ID}, f)
        st = json.loads(self.run_py("bin/save-clear-relay", ["status"]).stdout)
        self.assertIs(st["archived"], True)
        q = self.run_py("bin/save-clear-relay", ["request"], CLAUDE_CODE_HOST_SESSION_ID=APP)
        self.assertEqual(q.returncode, 4)
        self.assertIn("archived", q.stderr)
        self.assertFalse(os.path.isdir(os.path.join(self.state, "relay-inbox")) and
                         [n for n in os.listdir(os.path.join(self.state, "relay-inbox")) if n.endswith(".json")])
        r = self.ensure()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("archived", r.stdout)
        v = self.relay_json()
        self.assertNotEqual(v["local_id"], RELAY_ID)
        self.assertEqual(v["replaced"], RELAY_ID)
        self.assertEqual(len(self.created()), 1)

    def test_terminal_session_never_creates(self):
        for env in (dict(CLAUDE_CODE_ENTRYPOINT="cli"), dict(CLAUDE_CODE_HOST_SESSION_ID=""),
                    dict(CLAUDE_CODE_ENTRYPOINT="", CLAUDE_CODE_HOST_SESSION_ID="")):
            r = self.ensure(**env)
            self.assertEqual(r.returncode, 5, (env, r.stdout, r.stderr))
        self.assertEqual(self.created(), [])
        self.assertIsNone(self.relay_json())
        out = self.run_py("bin/save-clear", ["arm-clear"], CLAUDE_CODE_SESSION_ID=SID_A).stdout
        self.assertNotIn("relay", out)

    def test_off_setting_does_nothing(self):
        r = self.ensure(SAVE_CLEAR_RELAY="off")
        self.assertEqual(r.returncode, 3)
        with open(self.env["SAVE_CLEAR_CONFIG"], "w") as f:
            json.dump({"relay": "off"}, f)
        self.assertEqual(self.ensure().returncode, 3)
        self.assertEqual(self.created(), [])
        self.assertIsNone(self.relay_json())

    def test_existing_relay_is_left_alone(self):
        self.app_session(RELAY_ID, title="my relay", permissionMode="bypassPermissions")
        self.run_py("bin/save-clear-relay", ["setup", RELAY_ID])
        r = self.ensure()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"relay ready: {RELAY_ID}", r.stdout)
        self.assertNotIn("DO ", r.stdout, "a relay the user set up keeps its title")
        self.assertEqual(self.created(), [])

    def test_failed_creation_cools_down(self):
        self.env["SAVE_CLEAR_OPEN"] = f'"{PY}" -c "pass"'          # the app never imports it
        self.env["SAVE_CLEAR_RELAY_IMPORT_S"] = "0.3"
        r = self.ensure()
        self.assertEqual(r.returncode, 6)
        self.assertIn("did not import", r.stderr)
        self.assertIsNone(self.relay_json())
        r = self.ensure()
        self.assertEqual(r.returncode, 6)
        self.assertIn("less than", r.stderr)
        self.assertEqual(len(self.created()), 1, "no retry during the cool-down")

    def test_concurrent_creator_waits(self):
        os.makedirs(self.state, exist_ok=True)
        open(os.path.join(self.state, "relay-create.lock"), "w").close()
        r = self.ensure()
        self.assertEqual(r.returncode, 6)
        self.assertIn("another session", r.stderr)
        self.assertEqual(self.created(), [])


class ContextCap(Base):
    """Token-limit hook: asks for a save-clear at the end of a turn when the context is too big."""
    def transcript(self, tokens, sidechain_tokens=None):
        p = os.path.join(self.proj, "cap.jsonl")
        rows = [{"type": "assistant", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": tokens - 10,
                                                             "cache_creation_input_tokens": 0}}}]
        if sidechain_tokens is not None:
            rows.append({"type": "assistant", "isSidechain": True,
                         "message": {"usage": {"input_tokens": sidechain_tokens}}})
        with open(p, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return p

    def cap(self, tokens, sid=SID_A, extra=None, **env):
        hook = {"session_id": sid, "transcript_path": self.transcript(tokens)}
        hook.update(extra or {})
        env.setdefault("CLAUDE_CODE_ENTRYPOINT", "cli")
        return self.run_py("hooks/context_cap.py", stdin=hook, **env)

    def test_below_default_limit_is_silent(self):
        self.assertEqual(self.cap(299000).stdout, "")

    def test_above_default_limit_blocks_once(self):
        out = json.loads(self.cap(310000).stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("310k", out["reason"])
        self.assertIn("limit 300k", out["reason"])
        self.assertEqual(self.cap(320000).stdout, "", "no second ask until +50k")
        self.assertIn('"block"', self.cap(361000).stdout, "asks again after +50k")

    def test_user_can_change_or_turn_off_the_limit(self):
        self.assertIn("limit 150k", self.cap(160000, SAVE_CLEAR_CAP_TOKENS="150000").stdout)
        self.assertIn("limit 200k", self.cap(250000, sid=SID_B, CLAUDE_PLUGIN_OPTION_CONTEXT_CAP_TOKENS="200000").stdout)
        self.assertEqual(self.cap(900000, sid=SID_NEW, SAVE_CLEAR_CAP_TOKENS="0").stdout, "")

    def test_skips_headless_active_armed_and_relay(self):
        self.assertEqual(self.cap(400000, CLAUDE_CODE_ENTRYPOINT="sdk-cli").stdout, "")
        self.assertEqual(self.cap(400000, extra={"stop_hook_active": True}).stdout, "")
        os.makedirs(self.flag_path("clear"), exist_ok=True)
        with open(self.flag_path(f"clear/{SID_A}.json"), "w") as f:
            json.dump({"session_id": SID_A}, f)
        self.assertEqual(self.cap(400000).stdout, "", "save-clear already armed")
        with open(self.flag_path("relay.json"), "w") as f:
            json.dump({"local_id": "relay-1"}, f)
        self.assertEqual(self.cap(400000, sid=SID_B, CLAUDE_CODE_HOST_SESSION_ID="relay-1").stdout, "")

    def test_no_second_ask_right_after_a_save(self):
        # The save-clear turn just armed the resume; stop.py already consumed the /clear request.
        self.arm(self.snapshot())
        self.assertEqual(self.cap(400000).stdout, "", "this session just saved")

    def test_bg_job_request_counts_as_armed(self):
        os.makedirs(self.flag_path("clear"), exist_ok=True)
        with open(self.flag_path("clear/job-j1.json"), "w") as f:
            json.dump({"session_id": SID_A, "mode": "bg", "target": "j1"}, f)
        self.assertEqual(self.cap(400000, CLAUDE_JOB_DIR=os.path.join(self.t, "jobs", "j1")).stdout, "")

    def test_asks_again_after_the_context_shrinks(self):
        self.assertIn('"block"', self.cap(310000).stdout)
        self.assertEqual(self.cap(100000).stdout, "", "after a compaction")
        self.assertIn('"block"', self.cap(310000).stdout, "past the limit again: ask again")

    def test_zero_usage_row_is_skipped(self):
        p = self.transcript(400000)
        with open(p, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"model": "<synthetic>", "usage": {
                "input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}) + "\n")
        r = self.run_py("hooks/context_cap.py", stdin={"session_id": SID_A, "transcript_path": p},
                        CLAUDE_CODE_ENTRYPOINT="cli")
        self.assertIn('"block"', r.stdout, "a synthetic reply with zero usage is not the context size")

    def test_ignores_subagent_usage_and_bad_input(self):
        p = self.transcript(100000, sidechain_tokens=900000)
        r = self.run_py("hooks/context_cap.py", stdin={"session_id": SID_A, "transcript_path": p},
                        CLAUDE_CODE_ENTRYPOINT="cli")
        self.assertEqual(r.stdout, "")
        r = self.run_py("hooks/context_cap.py", stdin=None, CLAUDE_CODE_ENTRYPOINT="cli")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIn("limit 300k", self.cap(400000, SAVE_CLEAR_CAP_TOKENS="abc").stdout, "bad value = default")


def read_text(path):
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


class IdleTrigger(Base):
    """bin/save-clear-idle: a session idle for N minutes gets a save-clear request typed into its tmux pane.

    A fake `claude agents --json` (a .py file), a fake ~/.claude/projects and the fake tmux. Nothing is
    scheduled: launchd is never touched here (see InstallScript for the installer, with a fake launchctl)."""
    PANE_PID = 4242

    def setUp(self):
        super().setUp()
        self.projects = os.path.join(self.t, "projects")
        os.makedirs(os.path.join(self.projects, "-tmp-project"))
        self.agents_file = os.path.join(self.t, "agents.json")
        fake = os.path.join(self.t, "fake_claude_agents.py")
        with open(fake, "w") as f:
            # Records each call; from the 2nd call on it answers FAKE_AGENTS_LATER if that file exists.
            f.write("import os, sys\n"
                    "c = os.environ['FAKE_AGENTS'] + '.calls'\n"
                    "n = (len(open(c).read()) if os.path.exists(c) else 0) + 1\n"
                    "open(c, 'a').write('x')\n"
                    "later = os.environ['FAKE_AGENTS'] + '.later'\n"
                    "src = later if n > 1 and os.path.exists(later) else os.environ['FAKE_AGENTS']\n"
                    "sys.stdout.write(open(src).read())\n")
        self.env.update(SAVE_CLEAR_CLAUDE=fake, FAKE_AGENTS=self.agents_file, SAVE_CLEAR_PROJECTS_DIR=self.projects,
                        SAVE_CLEAR_IDLE_TYPE_DELAY="0", FAKE_TMUX_PANES=f"{self.PANE_PID} %7")
        self.set_agents()

    def set_agents(self, status="idle", kind="interactive", sid=SID_A, later=False):
        with open(self.agents_file + (".later" if later else ""), "w") as f:
            json.dump([{"kind": kind, "pid": self.PANE_PID, "sessionId": sid, "status": status,
                        "cwd": self.proj, "name": "test"}], f)

    def session(self, tokens=120000, idle_min=55, sid=SID_A, rows=()):
        p = os.path.join(self.projects, "-tmp-project", sid + ".jsonl")
        with open(p, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"usage": {
                "input_tokens": 10, "cache_read_input_tokens": tokens - 10, "cache_creation_input_tokens": 0}}}) + "\n")
        t = time.time() - idle_min * 60
        os.utime(p, (t, t))
        return p

    def idle(self, *args, **env):
        return self.run_py("bin/save-clear-idle", args, **env)

    def agents_calls(self):
        return len(read_text(self.agents_file + ".calls"))

    def mark(self, sid=SID_A):
        return json.loads(read_text(os.path.join(self.state, "idle", sid + ".done")))

    def tmux_calls(self):
        p = os.path.join(self.t, "tmux-calls.jsonl")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def typed(self):
        return [c for c in self.tmux_calls() if c[:1] == ["send-keys"]]

    def log_text(self):
        p = os.path.join(self.state, "save-clear.log")
        return read_text(p)

    def test_zero_turns_it_off(self):
        self.session()
        r = self.idle("--dry-run", SAVE_CLEAR_IDLE_MINUTES="0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("off", r.stdout)
        self.assertEqual(self.typed(), [])
        r = self.idle(CLAUDE_PLUGIN_OPTION_IDLE_MINUTES="0")
        self.assertEqual((r.returncode, self.typed()), (0, []))

    def test_too_early_or_too_late_does_nothing(self):
        self.session(idle_min=20)
        self.idle()
        self.session(idle_min=120)
        self.idle()
        self.assertEqual(self.typed(), [])
        self.assertNotIn("sent", self.log_text())

    def test_small_context_is_skipped_once(self):
        self.session(tokens=30000)
        r = self.idle("--dry-run")
        self.assertIn("context below 80000", r.stdout)
        self.assertEqual(self.typed(), [])
        self.idle()  # a real run records the decision for this idle stretch
        self.assertEqual(self.idle("--dry-run").stdout, "", "decided once per idle stretch")
        self.session(tokens=30000)  # a new idle stretch, with a lower limit
        self.assertIn("would-type" if not NO_TMUX else "not in a tmux pane",
                      self.idle("--dry-run", "--min-tokens", "20000").stdout)

    def test_busy_or_non_interactive_sessions_are_ignored(self):
        self.session()
        self.set_agents(status="busy")
        self.idle()
        self.set_agents(kind="background")
        self.idle()
        self.assertEqual(self.typed(), [])

    def test_armed_clear_or_recent_resume_is_skipped(self):
        self.session()
        os.makedirs(self.flag_path("clear"), exist_ok=True)
        with open(self.flag_path(f"clear/{SID_A}.json"), "w") as f:
            json.dump({"session_id": SID_A}, f)
        self.assertIn("already armed its /clear", self.idle("--dry-run").stdout)
        os.unlink(self.flag_path(f"clear/{SID_A}.json"))
        self.arm(self.snapshot())
        self.assertIn("armed a resume in the last 15 minutes", self.idle("--dry-run").stdout)

    def test_no_loop_right_after_an_automatic_resume(self):
        resumed = {"type": "user", "message": {"content": RESUME_MSG}}
        first = {"type": "assistant", "message": {"usage": {"input_tokens": 100000}}}
        self.session(tokens=110000, rows=[resumed, first])
        self.assertIn("little new since the automatic resume", self.idle("--dry-run").stdout)

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_types_again_once_the_session_grew_after_a_resume(self):
        resumed = {"type": "user", "message": {"content": RESUME_MSG}}
        first = {"type": "assistant", "message": {"usage": {"input_tokens": 100000}}}
        self.session(tokens=150000, rows=[resumed, first])
        self.assertIn("would-type", self.idle("--dry-run").stdout, "grew 50k since the resume")

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_resume_text_inside_a_tool_result_is_not_a_resume(self):
        # A tool result (e.g. reading the save-clear source or log) quotes the resume request: that is
        # not an automatic resume, so the small growth after it must not block the request.
        quoted = {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "RESUME_MSG = (\"" + RESUME_MSG + "\")"}]}}
        later = {"type": "user", "message": {"content": "about the line: " + RESUME_MSG}}
        side = {"type": "user", "isSidechain": True, "message": {"content": RESUME_MSG}}
        first = {"type": "assistant", "message": {"usage": {"input_tokens": 100000}}}
        self.session(tokens=110000, rows=[quoted, first, later, first, side, first])
        r = self.idle("--dry-run")
        self.assertNotIn("little new since the automatic resume", r.stdout)
        self.assertIn("would-type", r.stdout)

    def test_session_outside_tmux_is_skipped(self):
        self.session()
        r = self.idle("--dry-run", FAKE_TMUX_PANES="")
        self.assertIn("not in a tmux pane", r.stdout)
        self.assertEqual(self.typed(), [])

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_dry_run_types_nothing(self):
        self.session()
        r = self.idle("--dry-run")
        self.assertIn('"would-type"', r.stdout)
        self.assertIn('"target": "%7"', r.stdout)
        self.assertEqual(self.typed(), [])

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_idle_session_gets_the_request_once(self):
        self.session(tokens=120000, idle_min=52)
        r = self.idle()
        self.assertEqual(r.returncode, 0, r.stderr)
        typed = self.typed()
        self.assertEqual(len(typed), 2, typed)
        self.assertEqual(typed[0][:4], ["send-keys", "-t", "%7", "-l"])
        self.assertIn("save-clear (automatic: session idle for 52 min, context 120k", typed[0][4])
        self.assertEqual(typed[1], ["send-keys", "-t", "%7", "Enter"])
        self.assertIn('"event": "sent"', self.log_text())
        self.idle()
        self.assertEqual(len(self.typed()), 2, "no second request in the same idle stretch")

    # ----- a later idle stretch: ask again only after the user did something -----
    @staticmethod
    def at(minutes_ago):
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - minutes_ago * 60))

    def asked(self, minutes_ago=54):
        """The transcript record of the request typed by the first send."""
        return {"type": "user", "timestamp": self.at(minutes_ago), "message": {"content": self.typed()[0][4]}}

    def said(self, content="go on with the tests", minutes_ago=53, **extra):
        return dict({"type": "user", "timestamp": self.at(minutes_ago), "message": {"content": content}}, **extra)

    def first_send(self):
        self.session(idle_min=55)
        self.idle()
        self.assertEqual(len(self.typed()), 2)

    def notify_file(self):
        out = os.path.join(self.t, "notified.txt")
        script = os.path.join(self.t, "notify.py")
        with open(script, "w") as f:
            f.write("import sys\nopen(sys.argv[1], 'a', encoding='utf-8').write(sys.argv[2] + '\\n')\n")
        with open(os.path.join(self.t, "user-config.json"), "w") as f:
            json.dump({"notify_command": f"{PY} {script} {out}"}, f)
        return out

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_not_asked_again_while_the_user_did_nothing(self):
        self.first_send()
        m = self.mark()
        self.assertIn("sent_at", m)
        self.assertEqual((m["asked_pid"], m["pending"]), (self.PANE_PID, True))
        # The request landed (transcript changed), the session did not clear (say, a usage limit) and
        # it sat idle again: the same context is not saved twice. None of these rows is the user.
        limit = {"type": "assistant", "timestamp": self.at(53.9), "message": {
            "content": [{"type": "text", "text": "usage limit reached"}], "usage": {"input_tokens": 0}}}
        rows = [self.asked(), limit,
                self.said("Stop hook feedback: check it", isMeta=True),
                self.said([{"type": "tool_result", "tool_use_id": "t", "content": "<command-name>x \"image\""}]),
                self.said("This session is being continued from a previous conversation that ran out of context.",
                          isCompactSummary=True),
                self.said("in the subagent", isSidechain=True),
                self.said("<command-name>/stack</command-name>\n<command-message>stack</command-message>",
                          origin={"kind": "plugin", "name": "some-plugin"}),
                {"type": "user", "timestamp": self.at(53), "promptSource": "system",
                 "message": {"content": "<task-notification>\n<task-id>b1</task-id>"}},
                {"type": "queue-operation", "operation": "enqueue", "timestamp": self.at(53), "content": "never mind"},
                {"type": "queue-operation", "operation": "remove", "timestamp": self.at(52.9), "content": "never mind",
                 "reason": "user_cancelled"}]
        self.session(idle_min=51, rows=rows)
        self.idle()
        self.assertEqual(len(self.typed()), 2, "no new request while the user did nothing")
        self.assertIn("already asked to save and the user did nothing since", self.log_text())
        m = self.mark()
        self.assertEqual((m["fails"], "pending" in m, "gave_up" in m), (1, False, False))
        self.session(idle_min=51, rows=rows)  # one more hour like that: still nothing, and no second failure
        self.idle()
        self.assertEqual((len(self.typed()), self.mark()["fails"]), (2, 1))

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_asks_again_after_the_user_did_something(self):
        image = [{"type": "image", "source": {"type": "base64", "data": "AAAA"}}]
        for name, row in (("message", self.said()),
                          ("queued message", {"type": "queue-operation", "operation": "enqueue",
                                              "timestamp": self.at(53), "content": "one more thing"}),
                          ("image", self.said(image)),
                          ("slash command", self.said("<command-message>x</command-message>\n<command-name>/x</command-name>")),
                          ("! command", self.said("<bash-input>ls</bash-input>")),
                          ("interrupt", self.said([{"type": "text", "text": "[Request interrupted by user]"}]))):
            with self.subTest(name):
                for f in ("tmux-calls.jsonl", os.path.join("state", "idle", SID_A + ".done")):
                    if os.path.exists(os.path.join(self.t, f)):
                        os.unlink(os.path.join(self.t, f))
                self.first_send()
                self.session(idle_min=51, rows=[self.asked(), row])
                self.idle()
                self.assertEqual(len(self.typed()), 4, "a new idle stretch after the user gets a new request")
                self.assertNotIn("fails", self.mark(), "a request the user answered is not a failure")

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_gives_up_and_notifies_once_after_two_requests_that_did_not_clear(self):
        out = self.notify_file()
        self.first_send()
        asked1, words, asked2 = self.asked(54), self.said(minutes_ago=53), self.asked(52)
        self.session(idle_min=51, rows=[asked1])
        self.idle()  # failure 1: landed, nobody wrote, same process still on the same id
        self.session(idle_min=51, rows=[asked1, words])
        self.idle()
        self.assertEqual(len(self.typed()), 4, "the user wrote: second request")
        self.session(idle_min=51, rows=[asked1, words, asked2])
        self.idle()  # failure 2
        self.assertEqual(self.mark()["fails"], 2)
        self.assertTrue(self.mark()["gave_up"])
        for _ in range(2):  # the user writes again and leaves, twice: this session is left alone
            self.session(idle_min=51, rows=[asked1, words, asked2, self.said("and now?", minutes_ago=51.5)])
            r = self.idle("--only", SID_A)
            self.assertIn("gave up on this session", r.stdout)
        self.assertEqual(len(self.typed()), 4, "no third request")
        self.assertEqual(self.log_text().count('"gave-up"'), 1)
        self.assertEqual(len(read_text(out).splitlines()), 1, "notified once")
        self.assertIn("stayed as it was", read_text(out))

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_requests_the_user_answered_never_add_up_to_giving_up(self):
        out = self.notify_file()
        self.first_send()
        rows = []
        for n in range(1, 4):  # three more hours: each time the user wrote after the request
            rows += [self.asked(60 - 2 * n), self.said(f"reply {n}", minutes_ago=59 - 2 * n)]
            self.session(idle_min=51, rows=rows)
            self.idle()
            self.assertEqual(len(self.typed()), 2 + 2 * n)
        self.assertNotIn("gave-up", self.log_text())
        self.assertNotIn("fails", self.mark())
        self.assertFalse(os.path.exists(out))

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_same_session_open_in_another_pane_is_not_asked_and_is_not_a_failure(self):
        # Pane %7 (pid 4242) was asked, saved and cleared: it now has a new id. Pane %8 (pid 4343) still
        # runs the old id and shares its transcript, where the request and the save now are.
        self.first_send()
        other = 4343
        with open(self.agents_file, "w") as f:
            json.dump([{"kind": "interactive", "pid": other, "sessionId": SID_A, "status": "idle", "name": "copy"}], f)
        self.session(idle_min=51, rows=[self.asked()])
        r = self.idle("--only", SID_A, FAKE_TMUX_PANES=f"{other} %8")
        self.assertIn("already asked in another process", r.stdout)
        self.assertEqual(len(self.typed()), 2, "the copy is not asked to save the same context")
        m = self.mark()
        self.assertEqual(("fails" in m, "gave_up" in m, m["asked_pid"]), (False, False, self.PANE_PID))
        # The user works in the copy, then leaves: now it is asked.
        self.session(idle_min=51, rows=[self.asked(), self.said()])
        self.idle(FAKE_TMUX_PANES=f"{other} %8")
        typed = self.typed()
        self.assertEqual((len(typed), typed[2][:3]), (4, ["send-keys", "-t", "%8"]))
        self.assertEqual(self.mark()["asked_pid"], other)

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_request_that_never_landed_is_not_a_failure(self):
        out = self.notify_file()
        for n in range(1, 4):  # Enter was pressed, but the request never reached the transcript
            self.session(idle_min=55 - n)
            self.idle()
            self.assertEqual(len(self.typed()), 2 * n, "asked again in the next idle stretch")
        self.assertNotIn("fails", self.mark())
        self.assertNotIn("gave-up", self.log_text())
        self.assertFalse(os.path.exists(out))

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_no_response_is_logged_once_and_not_retyped(self):
        self.session(idle_min=52)
        self.idle()
        m = self.mark()
        m["sent_at"] -= 300  # five minutes ago, and the transcript still did not change
        with open(os.path.join(self.state, "idle", SID_A + ".done"), "w") as f:
            json.dump(m, f)
        self.idle()
        self.idle()
        self.assertEqual(len(self.typed()), 2, "never a second copy in the same stretch")
        self.assertEqual(self.log_text().count('"no-response"'), 1)

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_failed_check_before_enter_erases_the_typed_text(self):
        self.session()
        self.set_agents(status="busy", later=True)  # goes busy between typing and Enter
        r = self.idle("--only", SID_A)
        self.assertIn("the typed text was erased", r.stdout)
        typed = self.typed()
        self.assertEqual(typed[0][3], "-l")
        self.assertNotIn(["send-keys", "-t", "%7", "Enter"], typed)
        self.assertEqual(typed[1][:3], ["send-keys", "-t", "%7"])
        self.assertEqual(set(typed[1][3:]), {"BSpace"})
        self.assertEqual(read_text(os.path.join(self.t, "draft.txt")), "", "prompt left empty")

    def test_agents_not_asked_when_nothing_is_in_the_window(self):
        self.session(idle_min=20)
        self.idle()
        self.session(idle_min=200)
        self.idle()
        self.assertEqual(self.agents_calls(), 0)

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_agents_asked_twice_per_send(self):
        self.session()
        self.idle()
        self.assertEqual(len(self.typed()), 2)
        self.assertEqual(self.agents_calls(), 2, "once to list, once right before Enter")

    def test_window_never_reaches_60_minutes(self):
        self.session(idle_min=60.5)
        r = self.idle("--only", SID_A, SAVE_CLEAR_IDLE_MINUTES="55")  # 55 + 8 would reach 63
        self.assertIn("outside 55-59", r.stdout)
        self.assertEqual(self.typed(), [])
        r = self.idle("--only", SID_A, SAVE_CLEAR_IDLE_MINUTES="59")
        self.assertIn("leaves no window", r.stdout)
        self.assertEqual((r.returncode, self.typed()), (0, []))
        self.assertIn("leaves no window", self.idle("--dry-run", "--idle-min", "50", "--max-min", "50").stdout)

    def test_missing_transcripts_are_logged_once(self):
        r = self.idle()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.idle()
        self.assertEqual(self.log_text().count("no transcripts under"), 1)
        self.assertEqual(self.agents_calls(), 0)

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_custom_idle_minutes(self):
        self.session(idle_min=12)
        self.idle(SAVE_CLEAR_IDLE_MINUTES="10")
        self.assertEqual(len(self.typed()), 2)

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_draft_in_the_prompt_is_never_touched(self):
        self.session()
        with open(os.path.join(self.t, "draft.txt"), "w") as f:
            f.write("half-written idea")
        r = self.idle("--only", SID_A)
        self.assertIn("the prompt has a draft", r.stdout)
        self.assertEqual(self.typed(), [])

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_pane_running_another_session_is_not_typed_into(self):
        self.session(sid=SID_A)
        # the pane's process now reports session B, but A's transcript is the idle one
        with open(self.agents_file, "w") as f:
            json.dump([{"kind": "interactive", "pid": self.PANE_PID, "sessionId": SID_A, "status": "idle"},
                       {"kind": "interactive", "pid": self.PANE_PID, "sessionId": SID_B, "status": "idle"}], f)
        r = self.idle("--only", SID_A)
        self.assertIn("no single Claude in the pane", r.stdout)
        self.assertEqual(self.typed(), [])


@unittest.skipIf(sys.platform == "win32", "install.sh is a bash script for macOS (launchd) and Linux (cron line)")
class InstallScript(Base):
    """install.sh with a fake HOME and a fake launchctl: no real launchd job is ever loaded."""
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.t, "home")
        os.makedirs(self.home)
        self.calls = os.path.join(self.t, "launchctl-calls")
        # `print` says "not loaded" unless FAKE_LOADED_PRINTS more answers say "loaded";
        # `bootstrap` fails FAKE_BOOTSTRAP_FAILS times first.
        with open(os.path.join(self.fakebin, "launchctl"), "w") as f:
            f.write(f'''#!/bin/sh
echo "$@" >> "{self.calls}"
count() {{ n=$(cat "{self.t}/lc-$1" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "{self.t}/lc-$1"; echo $n; }}
case "$1" in
  print) [ "$(count print)" -le "${{FAKE_LOADED_PRINTS:-0}}" ] && exit 0; exit 1 ;;
  bootstrap) [ "$(count bootstrap)" -le "${{FAKE_BOOTSTRAP_FAILS:-0}}" ] && exit 5; exit 0 ;;
esac
exit 0
''')
        os.chmod(os.path.join(self.fakebin, "launchctl"), 0o755)

    def install(self, *args, os_name="Darwin", **env):
        e = dict(self.env, HOME=self.home, SAVE_CLEAR_INSTALL_OS=os_name)
        e.update(env)
        return subprocess.run(["bash", os.path.join(ROOT, "install.sh"), *args], capture_output=True, text=True,
                              env=e, timeout=60)

    def launchctl(self):
        return read_text(self.calls)

    def test_dry_run_changes_nothing(self):
        r = self.install("--idle", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("<string>com.claude-tools.save-clear-idle</string>", r.stdout)
        self.assertIn(os.path.join(ROOT, "bin", "save-clear-idle"), r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.home, "Library")))
        self.assertEqual(self.launchctl(), "")

    def test_install_and_uninstall_with_custom_prefix(self):
        r = self.install("--idle", "--interval", "300", SAVE_CLEAR_LAUNCHD_PREFIX="org.example")
        self.assertEqual(r.returncode, 0, r.stderr)
        plist = os.path.join(self.home, "Library", "LaunchAgents", "org.example.save-clear-idle.plist")
        body = read_text(plist)
        self.assertIn("<integer>300</integer>", body)
        self.assertIn("bootstrap gui/", self.launchctl())
        if sys.platform == "darwin":
            self.assertEqual(subprocess.run(["plutil", "-lint", plist], capture_output=True).returncode, 0)
        r = self.install("--uninstall", SAVE_CLEAR_LAUNCHD_PREFIX="org.example")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(plist))
        self.assertIn("bootout gui/", self.launchctl())

    def test_waits_for_bootout_and_retries_bootstrap(self):
        r = self.install("--idle", FAKE_LOADED_PRINTS="2", FAKE_BOOTSTRAP_FAILS="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.launchctl().splitlines()
        self.assertEqual([c.split()[0] for c in calls],
                         ["bootout", "print", "print", "print", "bootstrap", "bootstrap"])
        r = self.install("--idle", FAKE_BOOTSTRAP_FAILS="9")
        self.assertEqual(r.returncode, 1)
        self.assertIn("bootstrap failed", r.stderr)

    def test_job_gets_config_dir_and_an_error_log(self):
        cfg = os.path.join(self.t, "alt claude")
        r = self.install("--idle", "--dry-run", CLAUDE_CONFIG_DIR=cfg)
        self.assertIn(f"<key>CLAUDE_CONFIG_DIR</key><string>{cfg}</string>", r.stdout)
        self.assertIn(f"<key>StandardErrorPath</key><string>{os.path.join(self.state, 'idle-job.log')}</string>",
                      r.stdout)
        self.assertNotIn("CLAUDE_CONFIG_DIR", self.install("--idle", "--dry-run").stdout)
        r = self.install("--idle", os_name="Linux", CLAUDE_CONFIG_DIR=cfg)
        self.assertIn(f"CLAUDE_CONFIG_DIR={cfg} ", r.stdout)
        self.assertIn("2>>" + os.path.join(self.state, "idle-job.log"), r.stdout)

    def test_linux_rejects_intervals_cron_cannot_express(self):
        for bad in ("90", "3600", "7200"):
            r = self.install("--idle", "--interval", bad, os_name="Linux")
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("multiple of 60", r.stderr)
        self.assertIn("*/5 * * * *", self.install("--idle", "--interval", "300", os_name="Linux").stdout)

    def test_linux_only_prints_a_cron_line(self):
        r = self.install("--idle", os_name="Linux")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("crontab -e", r.stdout)
        self.assertIn("*/2 * * * *", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.home, "Library")))
        self.assertEqual(self.launchctl(), "")

    def test_bad_arguments(self):
        self.assertEqual(self.install().returncode, 2)
        self.assertEqual(self.install("--idle", "--interval", "5").returncode, 2)
        self.assertEqual(self.install("--bogus").returncode, 2)


PT_RESUME = "salvalimpa: retomando. Comece pelo ÚLTIMO assunto (onde paramos e o que está em aberto)."
# A command a user could plug in: logs the save-clear variables it got, prints HOOK_PRINT, exits HOOK_EXIT.
HOOK_SCRIPT = r"""
import json, os, sys
NAMES = ("SAVE_CLEAR_EVENT", "SAVE_CLEAR_SNAPSHOT", "SAVE_CLEAR_SESSION_ID", "SAVE_CLEAR_PREVIOUS_SESSION_ID",
         "SAVE_CLEAR_MODE", "SAVE_CLEAR_TARGET", "SAVE_CLEAR_PROJECT_DIR", "CLAUDE_PID")
with open(os.environ["HOOK_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({k: os.environ[k] for k in NAMES if k in os.environ}) + "\n")
sys.stdout.write(os.environ.get("HOOK_PRINT", "").replace("|", "\n"))
sys.exit(int(os.environ.get("HOOK_EXIT", "0")))
"""


class UserConfig(Base):
    """Options in ~/.config/save-clear/config.json (SAVE_CLEAR_CONFIG) and the commands users plug in."""

    def setUp(self):
        super().setUp()
        self.hook_log = os.path.join(self.t, "hook-log.jsonl")
        script = os.path.join(self.t, "hook script.py")  # a space in the path on purpose
        with open(script, "w", encoding="utf-8") as f:
            f.write(HOOK_SCRIPT)
        self.cmd = f'"{PY}" "{script}"'
        self.env["HOOK_LOG"] = self.hook_log

    def config(self, **values):
        with open(self.env["SAVE_CLEAR_CONFIG"], "w", encoding="utf-8") as f:
            json.dump(values, f, ensure_ascii=False)

    def hook_calls(self):
        if not os.path.exists(self.hook_log):
            return []
        with open(self.hook_log, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_config_file_is_read_and_env_wins(self):
        custom = os.path.join(self.t, "from-file")
        self.config(snapshot_dir=custom, language="Portuguese", extra_steps="Call plan_steps before clear_session.")
        r = self.run_py("bin/save-clear", ["dir"])
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], self.snaps, "the env var beats the config file")
        self.assertIn("language: Portuguese", lines)
        self.assertIn("Call plan_steps before clear_session.", r.stdout)
        self.env.pop("SAVE_CLEAR_SNAPSHOT_DIR")
        self.assertEqual(self.run_py("bin/save-clear", ["dir"]).stdout.splitlines()[0], custom)
        r = self.run_py("bin/save-clear", ["dir"], CLAUDE_PLUGIN_OPTION_LANGUAGE="German")
        self.assertIn("language: German", r.stdout, "a plugin option beats the config file")

    def py(self, code, *args):
        """Run `code` with lib/ and bin/save-clear-idle importable, in this test's environment.
        `args` reach the code as ARGS (a JSON list): the idle script parses sys.argv when imported."""
        pre = ("import importlib.machinery, importlib.util, json, os, sys\n"
               "ARGS = json.loads(os.environ['TEST_ARGS'])\n"
               f"sys.path.insert(0, {os.path.join(ROOT, 'lib')!r})\n"
               f"ld = importlib.machinery.SourceFileLoader('idle', {os.path.join(ROOT, 'bin', 'save-clear-idle')!r})\n"
               "idle = importlib.util.module_from_spec(importlib.util.spec_from_loader('idle', ld))\n"
               "ld.exec_module(idle)\n"
               "import common\n")
        r = subprocess.run([PY, "-c", pre + code], capture_output=True, text=True, encoding="utf-8",
                           cwd=self.proj, env=dict(self.env, TEST_ARGS=json.dumps(args)), timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_number_zero_and_false_in_the_file_are_values(self):
        self.config(context_cap_tokens=0, idle_minutes=0, publish_link=False)
        out = self.py("print(common.setting('context_cap_tokens', '300000'), common.setting('idle_minutes', '50'),"
                      " common.setting('publish_link', 'x'))")
        self.assertEqual(out, "0 0 false", "0 = off must not fall back to the default")

    def test_short_custom_resume_message_does_not_hide_the_users_own_words(self):
        self.config(resume_message="go on")
        tp = os.path.join(self.t, "t.jsonl")
        with open(tp, "w", encoding="utf-8") as f:
            for txt in ("go on", "go on with the refactor, please"):
                f.write(json.dumps({"type": "user", "timestamp": iso(5), "message": {"role": "user", "content": txt}}) + "\n")
        out = self.py("print(json.dumps(common.user_messages_since(ARGS[0], 0)))", tp)
        self.assertEqual(json.loads(out), ["go on with the refactor, please"])
        out = self.py("print(common.is_resume_text('go on'), common.is_resume_text('  go   on '),"
                      " common.is_resume_text(ARGS[0][:30] + ' extra'))", RESUME_MSG)
        self.assertEqual(out, "True True True", "the exact custom text and the default's start still count")

    def test_idle_finds_a_custom_resume_message_with_quotes(self):
        custom = 'Say "resumed" then go on'
        self.config(resume_message=custom)
        lines = [json.dumps({"type": "user", "message": {"role": "user", "content": custom}}),
                 json.dumps({"type": "user", "message": {"role": "user", "content": custom + " and more"}})]
        out = self.py("c = common.resume_msg(); print([idle.is_resume_request(l, c) for l in ARGS])", *lines)
        self.assertEqual(out, "[True, False]")

    def test_extra_steps_from_a_file_and_bad_config(self):
        steps = os.path.join(self.t, "steps.md")
        with open(steps, "w", encoding="utf-8") as f:
            f.write("1. Passo próprio do usuário\n2. Outro passo\n")
        self.config(extra_steps=steps)
        r = self.run_py("bin/save-clear", ["dir"])
        self.assertIn("extra steps", r.stdout)
        self.assertIn("2. Outro passo", r.stdout)
        with open(self.env["SAVE_CLEAR_CONFIG"], "w") as f:
            f.write("{not json")
        r = self.run_py("bin/save-clear", ["dir"])
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("extra steps", r.stdout)

    def test_on_save_output_is_appended_once(self):
        self.config(on_save_command=self.cmd)
        snap = self.snapshot()
        r = self.arm(snap, HOOK_PRINT="## Open pages (restart first)|- /tmp/page.html")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("on_save_command: added 2 line(s)", r.stdout)
        self.assertIn("auto-resume armed", r.stdout)
        with open(snap, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("we were testing\n\n## Open pages (restart first)\n- /tmp/page.html\n", body)
        [call] = self.hook_calls()
        self.assertEqual((call["SAVE_CLEAR_EVENT"], call["SAVE_CLEAR_SNAPSHOT"], call["SAVE_CLEAR_SESSION_ID"]),
                         ("save", snap, SID_A))
        self.assertEqual(call["CLAUDE_PID"], "4242", "the script can find the session's own processes")
        self.arm(snap, HOOK_PRINT="## Open pages (restart first)|- /tmp/page.html")
        with open(snap, encoding="utf-8") as f:
            self.assertEqual(f.read().count("## Open pages"), 1, "not appended twice")

    def test_failing_on_save_never_blocks_the_arm(self):
        self.config(on_save_command=self.cmd)
        snap = self.snapshot()
        r = self.arm(snap, HOOK_PRINT="half output", HOOK_EXIT="3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("exited 3", r.stdout)
        with open(snap, encoding="utf-8") as f:
            self.assertNotIn("half output", f.read())
        self.config(on_save_command=os.path.join(self.t, "missing-program"))
        r = self.arm(snap)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("failed to run", r.stdout)
        self.assertIn("auto-resume armed", r.stdout)

    def test_on_resume_output_and_language_reach_the_new_session(self):
        self.config(on_resume_command=self.cmd, language="Portuguese")
        snap = self.snapshot()
        self.arm(snap)
        out = self.start(CLAUDE_PID=4242, HOOK_PRINT="Run the page poll again in the background.")
        ctx = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Write your replies in Portuguese.", ctx)
        self.assertIn("Run the page poll again in the background.", ctx)
        self.assertLess(ctx.index("page poll again"), ctx.index("we were testing"), "before the summary")
        [call] = self.hook_calls()
        self.assertEqual((call["SAVE_CLEAR_EVENT"], call["SAVE_CLEAR_SNAPSHOT"], call["SAVE_CLEAR_SESSION_ID"],
                          call["SAVE_CLEAR_PREVIOUS_SESSION_ID"]), ("resume", snap, SID_NEW, SID_A))

    def test_failing_on_resume_still_delivers_the_summary(self):
        self.config(on_resume_command=self.cmd)
        self.arm(self.snapshot())
        out = self.start(CLAUDE_PID=4242, HOOK_PRINT="should not show", HOOK_EXIT="1")
        ctx = json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("we were testing", ctx)
        self.assertNotIn("should not show", ctx)

    def test_custom_resume_message_is_not_a_user_message(self):
        self.config(resume_message=PT_RESUME)
        os.makedirs(self.flag_path("clear"), exist_ok=True)
        with open(self.flag_path(f"clear/{SID_A}.json"), "w") as f:
            json.dump({"session_id": SID_A, "mode": "manual-test", "target": "", "key": SID_A,
                       "ts": int(time.time() * 1000)}, f)
        tp = os.path.join(self.proj, "t.jsonl")
        with open(tp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "timestamp": iso(1),
                                "message": {"role": "user", "content": PT_RESUME}}) + "\n")
        r = self.run_py("hooks/stop.py", stdin={"session_id": SID_A, "transcript_path": tp})
        self.assertNotIn('"block"', r.stdout, "the resume request does not hold the clear")

    def test_relay_sends_the_custom_resume_message(self):
        self.config(resume_message=PT_RESUME)
        self.run_py("bin/save-clear-relay", ["setup", RELAY_ID])
        self.run_py("bin/save-clear-relay", ["request"], CLAUDE_CODE_HOST_SESSION_ID=APP)
        d = os.path.join(self.state, "relay-inbox")
        [name] = [n for n in os.listdir(d) if n.endswith(".json")]
        with open(os.path.join(d, name), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["message"], PT_RESUME)

    def test_pre_clear_runs_before_clear_session_goes_through(self):
        self.config(pre_clear_command=self.cmd)
        hook = {"tool_input": {"session_id": "self"}, "session_id": SID_A,
                "transcript_path": os.path.join(self.proj, "none.jsonl")}
        r = self.run_py("hooks/clear_guard.py", stdin=hook, CLAUDE_CODE_HOST_SESSION_ID=APP, HOOK_PRINT="noise")
        self.assertEqual(r.stdout, "", "the command's output never reaches the hook output")
        [call] = self.hook_calls()
        self.assertEqual((call["SAVE_CLEAR_EVENT"], call["SAVE_CLEAR_MODE"], call["SAVE_CLEAR_TARGET"],
                          call["SAVE_CLEAR_SESSION_ID"]), ("pre_clear", "app", APP, SID_A))

    def test_pre_clear_not_run_when_the_clear_is_held(self):
        self.config(pre_clear_command=self.cmd)
        tp = os.path.join(self.proj, "t.jsonl")
        skill = "Base directory for this skill: /x/plugins/save-clear/skills/save-clear\n..."
        with open(tp, "w") as f:
            for e in ({"type": "user", "timestamp": iso(-60), "message": {"role": "user", "content": skill}},
                      {"type": "user", "timestamp": iso(-30), "message": {"role": "user", "content": "wait, X"}}):
                f.write(json.dumps(e) + "\n")
        r = self.run_py("hooks/clear_guard.py", stdin={"tool_input": {"session_id": "self"}, "transcript_path": tp})
        self.assertIn("deny", r.stdout)
        self.assertEqual(self.hook_calls(), [])


class NotifySuccess(Base):
    """notify_success: on (default) sends success and failure notices; off sends only failures."""

    def notices(self, value):
        out = os.path.join(self.t, "notices.txt")
        if os.path.exists(out):
            os.remove(out)
        script = os.path.join(self.t, "notify.py")
        with open(script, "w") as f:
            f.write(f"import sys\nopen({out!r}, 'a').write(sys.argv[1] + '\\n')\n")
        with open(self.env["SAVE_CLEAR_CONFIG"], "w") as f:
            json.dump({"notify_command": f"{PY} {script}", "notify_success": value}, f)
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from common import notify; "
                "notify('ok', success=True); notify('failed')")
        e = dict(self.env)
        subprocess.run([PY, "-c", code, os.path.join(ROOT, "lib")], env=e, cwd=self.proj, timeout=60, check=True)
        return open(out).read().split() if os.path.exists(out) else []

    def test_default_sends_both(self):
        for value in (None, "", "on", "typo"):
            self.assertEqual(self.notices(value), ["ok", "failed"], value)

    def test_off_sends_only_failures(self):
        for value in ("off", "false", "no", "0", "OFF"):
            self.assertEqual(self.notices(value), ["failed"], value)


class KillBackgroundTasks(Base):
    """kill_background_tasks: off (default) changes nothing; on asks Claude to stop this session's tasks."""
    LINE = "kill_background_tasks: on"

    def setUp(self):
        super().setUp()
        home = os.path.join(self.t, "home")  # nothing here may reach the real home folder
        os.makedirs(home)
        self.env.update(HOME=home, USERPROFILE=home)

    def write_cfg(self, value):
        with open(self.env["SAVE_CLEAR_CONFIG"], "w") as f:
            json.dump({"kill_background_tasks": value}, f)

    def write_flag(self, **extra):
        os.makedirs(self.flag_path("clear"), exist_ok=True)
        f = {"session_id": SID_A, "mode": "manual-test", "target": "", "key": SID_A, "ts": int(time.time() * 1000)}
        f.update(extra)
        with open(self.flag_path(f"clear/{SID_A}.json"), "w") as fh:
            json.dump(f, fh)

    def stop(self, **env):
        return self.run_py("hooks/stop.py", stdin={"session_id": SID_A}, **env)

    def test_dir_prints_nothing_new_when_absent_null_or_off(self):
        base = self.run_py("bin/save-clear", ["dir"])
        self.assertEqual(base.returncode, 0, base.stderr)
        self.assertNotIn("kill_background_tasks", base.stdout)
        for value in (None, "", "off", False):
            self.write_cfg(value)
            r = self.run_py("bin/save-clear", ["dir"])
            self.assertEqual((r.returncode, r.stdout), (0, base.stdout), value)

    def test_dir_prints_the_instruction_when_on(self):
        for env, cfg in (({"SAVE_CLEAR_KILL_BACKGROUND_TASKS": "on"}, None),
                         ({"CLAUDE_PLUGIN_OPTION_KILL_BACKGROUND_TASKS": "true"}, None), ({}, "on"), ({}, True)):
            if cfg is not None:
                self.write_cfg(cfg)
            r = self.run_py("bin/save-clear", ["dir"], **env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(self.LINE, r.stdout, (env, cfg))
            self.assertIn("TaskStop", r.stdout)
            self.assertIn("## Stopped background tasks", r.stdout)
            self.assertIn("Never stop anything this session did not start", r.stdout)

    def test_bad_values_are_off_and_never_crash(self):
        for value in ("maybe", 5, ["on"], {"x": 1}):
            self.write_cfg(value)
            r = self.run_py("bin/save-clear", ["dir"])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn(self.LINE, r.stdout, value)
            self.write_flag(busy_rearm=True)
            r = self.stop()
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn('"decision": "block"', r.stdout, value)
        with open(self.env["SAVE_CLEAR_CONFIG"], "w") as f:
            f.write("{not json")
        self.assertEqual(self.run_py("bin/save-clear", ["dir"]).returncode, 0)

    def test_app_mode_reminder_only_when_on(self):
        env = dict(CLAUDE_CODE_SESSION_ID=SID_A, CLAUDE_CODE_ENTRYPOINT="claude-desktop")
        self.assertNotIn(self.LINE, self.run_py("bin/save-clear", ["arm-clear"], **env).stdout)
        r = self.run_py("bin/save-clear", ["arm-clear"], SAVE_CLEAR_KILL_BACKGROUND_TASKS="on", **env)
        self.assertIn("mode=app", r.stdout)
        self.assertIn(self.LINE, r.stdout)
        self.assertIn("before calling clear_session", r.stdout)

    def test_manual_mode_has_no_reminder(self):
        # The user types /clear; nothing waits for the session to be idle, so nothing to stop.
        r = self.run_py("bin/save-clear", ["arm-clear"], CLAUDE_CODE_SESSION_ID=SID_A, CLAUDE_CODE_ENTRYPOINT="cli",
                        TERM_PROGRAM="Apple_Terminal", CLAUDE_PID=os.getpid(), SAVE_CLEAR_KILL_BACKGROUND_TASKS="on")
        self.assertIn("mode=manual", r.stdout)
        self.assertNotIn(self.LINE, r.stdout)

    @unittest.skipIf(NO_TMUX, NO_TMUX_WHY)
    def test_armed_terminal_clear_reminds_before_ending_the_turn(self):
        env = dict(CLAUDE_CODE_SESSION_ID=SID_A, CLAUDE_JOB_DIR=os.path.join(self.t, "jobs", "j1"),
                   CLAUDE_CODE_ENTRYPOINT="cli")
        off = self.run_py("bin/save-clear", ["arm-clear"], **env)
        self.assertIn("mode=bg", off.stdout)
        self.assertNotIn(self.LINE, off.stdout)
        on = self.run_py("bin/save-clear", ["arm-clear"], SAVE_CLEAR_KILL_BACKGROUND_TASKS="on", **env).stdout
        self.assertIn(self.LINE, on)
        self.assertLess(on.index(self.LINE), on.index("end the turn now"))

    def test_stop_hook_unchanged_when_off(self):
        self.write_flag(busy_rearm=True)
        r = self.stop()
        self.assertNotIn('"decision": "block"', r.stdout)
        self.assertFalse(os.path.exists(self.flag_path(f"clear/{SID_A}.json")), "consumed as today")

    def test_stop_hook_asks_once_after_a_busy_rearm(self):
        self.write_flag(busy_rearm=True)
        r = self.stop(SAVE_CLEAR_KILL_BACKGROUND_TASKS="on")
        out = json.loads(r.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("TaskStop", out["reason"])
        self.assertIn("## Stopped background tasks", out["reason"])
        with open(self.flag_path(f"clear/{SID_A}.json")) as f:
            self.assertTrue(json.load(f)["bg_asked"], "flag kept, marked as asked")
        r = self.stop(SAVE_CLEAR_KILL_BACKGROUND_TASKS="on")
        self.assertNotIn('"decision": "block"', r.stdout, "asked only once")
        self.assertFalse(os.path.exists(self.flag_path(f"clear/{SID_A}.json")))

    def test_stop_hook_does_not_ask_without_a_busy_rearm(self):
        self.write_flag()
        r = self.stop(SAVE_CLEAR_KILL_BACKGROUND_TASKS="on")
        self.assertNotIn('"decision": "block"', r.stdout)

    def test_user_message_still_comes_first(self):
        self.write_flag(busy_rearm=True)
        tp = os.path.join(self.proj, "t.jsonl")
        with open(tp, "w") as f:
            f.write(json.dumps({"type": "user", "timestamp": iso(1),
                                "message": {"role": "user", "content": "one more thing"}}) + "\n")
        r = self.run_py("hooks/stop.py", stdin={"session_id": SID_A, "transcript_path": tp},
                        SAVE_CLEAR_KILL_BACKGROUND_TASKS="on")
        reason = json.loads(r.stdout)["reason"]
        self.assertIn("one more thing", reason)
        self.assertNotIn("TaskStop", reason)

    def test_message_rearm_drops_the_busy_marker(self):
        # The helper saw "busy" because the user typed after the save; Claude answers and re-arms.
        self.write_flag(busy_rearm=True)
        self.assertEqual(self.run_py("bin/save-clear", ["rearm", SID_A]).returncode, 0)
        with open(self.flag_path(f"clear/{SID_A}.json")) as f:
            self.assertNotIn("busy_rearm", json.load(f))
        r = self.stop(SAVE_CLEAR_KILL_BACKGROUND_TASKS="on")
        self.assertNotIn('"decision": "block"', r.stdout, "no request to stop tasks after a message round")

    def test_stop_hook_names_this_sessions_snapshot(self):
        snap = self.snapshot()
        self.arm(snap)
        self.write_flag(busy_rearm=True)
        reason = json.loads(self.stop(SAVE_CLEAR_KILL_BACKGROUND_TASKS="on").stdout)["reason"]
        self.assertIn(f"Then add to the snapshot {snap} a section", reason)

    def test_resume_ignores_a_none_section_and_accepts_a_suffix(self):
        cases = {"## Stopped background tasks\nnone\n": False,
                 "## Stopped background tasks\n- None.\n\n## Next\nx\n": False,
                 "## Stopped background tasks (none)\n": False,
                 "## Stopped background tasks\n": False,
                 "## Stopped background tasks (2)\n- b1x2: tail -f app.log\n": True,
                 "## Stopped background tasks:\n- b1x2: tail -f app.log (restart)\n## Next\n": True}
        for body, named in cases.items():
            self.arm(self.snapshot("# Session\n## Last topic\nx\n" + body))
            ctx = json.loads(self.start(CLAUDE_PID=4242).stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertEqual("stopped background tasks before the /clear" in ctx, named, body)

    def test_resume_names_the_stopped_tasks_only_if_listed(self):
        plain = self.snapshot()
        self.arm(plain)
        ctx = json.loads(self.start(CLAUDE_PID=4242).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("stopped background tasks", ctx)
        snap = self.snapshot("# Session\n## Last topic\nx\n## Stopped background tasks\n- b1x2: tail -f app.log (restart)\n")
        self.arm(snap)
        ctx = json.loads(self.start(CLAUDE_PID=4242).stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("stopped background tasks before the /clear", ctx)
        self.assertIn("tail -f app.log", ctx)


EXAMPLE = os.path.join(ROOT, "config.example.json")
SETTINGS_DUMP = """
import json, sys
sys.path.insert(0, {lib!r})
import common as c
print(json.dumps({{"settings": {{n: c.setting(n) for n in c.OPTIONS}}, "snapshot_dir": c.snapshot_dir(),
    "publish_link": c.publish_link(), "language": c.language(), "extra_steps": c.extra_steps(),
    "resume_msg": c.resume_msg()}}, sort_keys=True))
"""


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


class SeedConfig(Base):
    """The plugin creates ~/.config/save-clear/config.json by itself on first run (install runs no hook)."""

    def setUp(self):
        super().setUp()
        self.cfg = self.env["SAVE_CLEAR_CONFIG"]
        self.home = os.path.join(self.t, "home")
        os.makedirs(self.home)

    def dump(self, **env):
        r = subprocess.run([PY, "-c", SETTINGS_DUMP.format(lib=os.path.join(ROOT, "lib"))], capture_output=True,
                           text=True, encoding="utf-8", env=dict(self.env, **env), timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_example_lists_every_option_at_the_plugin_default(self):
        with open(EXAMPLE, encoding="utf-8") as f:
            example = json.load(f)
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            user_config = json.load(f)["userConfig"]
        keys = {k for k in example if not k.startswith("_")}
        self.assertEqual(keys, set(user_config), "one key per plugin option")
        self.assertEqual(set(example["_doc"]) - {"about"}, keys, "a short doc line per option")
        self.assertEqual({example[k] for k in keys}, {None}, "every option null: the seed pins no default")
        for k, v in user_config.items():  # each note names the default the plugin falls back to
            if v.get("default"):
                self.assertIn(str(v["default"]), example["_doc"][k], k)

    def test_seeded_file_follows_a_changed_built_in_default(self):
        """Whatever default the code passes wins over a seeded file, so a later default change reaches it."""
        self.env.pop("SAVE_CLEAR_SNAPSHOT_DIR")  # only the seeded file and the code's default are left
        self.start(source="startup")
        self.assertEqual(read_bytes(self.cfg), read_bytes(EXAMPLE))
        code = (f"import json, sys; sys.path.insert(0, {os.path.join(ROOT, 'lib')!r}); import common as c; "
                "print(json.dumps({n: c.setting(n, 'NEW-DEFAULT-' + n) for n in c.OPTIONS}))")
        r = subprocess.run([PY, "-c", code], capture_output=True, text=True, encoding="utf-8", env=self.env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got, {n: "NEW-DEFAULT-" + n for n in got})

    def test_missing_file_is_created_by_session_start_with_defaults(self):
        nested = os.path.join(self.t, "new", "dir", "config.json")
        self.env["SAVE_CLEAR_CONFIG"] = nested
        r = self.start(source="startup")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), "silent")
        self.assertEqual(read_bytes(nested), read_bytes(EXAMPLE))

    def test_missing_file_is_created_by_the_command_too(self):
        r = self.run_py("bin/save-clear", ["status"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(read_bytes(self.cfg), read_bytes(EXAMPLE))

    def test_existing_file_is_never_touched(self):
        for body in (b'{"language": "Portuguese"}', b"{not json", b""):
            with open(self.cfg, "wb") as f:
                f.write(body)
            before = os.stat(self.cfg).st_mtime_ns
            self.start(source="startup")
            self.run_py("bin/save-clear", ["dir"])
            self.assertEqual(read_bytes(self.cfg), body)
            self.assertEqual(os.stat(self.cfg).st_mtime_ns, before)

    def test_xdg_and_home_are_respected(self):
        self.env.pop("SAVE_CLEAR_CONFIG")
        self.env.pop("XDG_CONFIG_HOME", None)  # some CI runners set it
        xdg = os.path.join(self.t, "xdg")
        self.start(source="startup", HOME=self.home, USERPROFILE=self.home, XDG_CONFIG_HOME=xdg)
        self.assertEqual(read_bytes(os.path.join(xdg, "save-clear", "config.json")), read_bytes(EXAMPLE))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config")), "XDG_CONFIG_HOME wins over ~/.config")
        self.start(source="startup", HOME=self.home, USERPROFILE=self.home)
        self.assertEqual(read_bytes(os.path.join(self.home, ".config", "save-clear", "config.json")),
                         read_bytes(EXAMPLE))

    def test_relative_path_is_not_seeded_into_the_project(self):
        r = self.start(source="startup", SAVE_CLEAR_CONFIG="rel-config.json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(os.listdir(self.proj), [])

    def test_unwritable_place_never_breaks_the_session(self):
        blocker = os.path.join(self.t, "a-file")
        with open(blocker, "w") as f:
            f.write("x")
        self.env["SAVE_CLEAR_CONFIG"] = os.path.join(blocker, "sub", "config.json")  # parent is a file
        self.arm(self.snapshot())
        r = self.start(CLAUDE_PID=4242)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("we were testing", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.run_py("bin/save-clear", ["dir"]).returncode, 0)
        if os.name != "nt" and os.geteuid() != 0:  # a read-only folder (root and Windows ignore the mode)
            ro = os.path.join(self.t, "ro")
            os.makedirs(ro)
            os.chmod(ro, 0o500)
            try:
                r = self.start(source="startup", SAVE_CLEAR_CONFIG=os.path.join(ro, "config.json"))
                self.assertEqual((r.returncode, r.stderr), (0, ""))
                self.assertEqual(os.listdir(ro), [])
            finally:
                os.chmod(ro, 0o700)

    def test_seeded_file_behaves_like_no_file(self):
        env = dict(HOME=self.home, USERPROFILE=self.home)
        self.env.pop("SAVE_CLEAR_SNAPSHOT_DIR")
        without = self.dump(**env)
        self.assertFalse(os.path.exists(self.cfg))
        self.start(source="startup", **env)
        self.assertEqual(read_bytes(self.cfg), read_bytes(EXAMPLE))
        seeded = self.dump(**env)
        self.assertEqual(seeded, without)
        # plugin options (/config) and env vars still win over the seeded defaults
        on = self.dump(CLAUDE_PLUGIN_OPTION_PUBLISH_LINK="on", CLAUDE_PLUGIN_OPTION_LANGUAGE="German", **env)
        self.assertEqual((on["publish_link"], on["language"]), (True, "German"))
        self.assertTrue(self.dump(SAVE_CLEAR_PUBLISH_LINK="on", **env)["publish_link"])
        # the hooks that read numeric options: same result with and without the file
        for script in ("hooks/context_cap.py",):
            hook = {"session_id": SID_A, "transcript_path": os.path.join(self.proj, "none.jsonl")}
            a = self.run_py(script, stdin=hook, **env)
            os.remove(self.cfg)
            b = self.run_py(script, stdin=hook, SAVE_CLEAR_CONFIG="/nonexistent/x.json", **env)
            self.assertEqual((a.returncode, a.stdout), (b.returncode, b.stdout))


class RcIdleOff(Base):
    """The relay turns Remote Control off for sessions idle past `rc_idle_off_hours`."""

    def relay(self, *args, **env):
        return self.run_py("bin/save-clear-relay", args, **env)
    IDLE = "local_33333333-3333-3333-3333-333333333333"      # RC on, last message 30 h ago
    NEVER = "local_44444444-4444-4444-4444-444444444444"     # RC on, no message, created 48 h ago
    RECENT = "local_55555555-5555-5555-5555-555555555555"    # RC on, last message 1 h ago
    RC_OFF = "local_66666666-6666-6666-6666-666666666666"    # RC off, idle
    KEPT = "local_77777777-7777-7777-7777-777777777777"      # RC on, idle, in the keep-on file
    GONE = "local_88888888-8888-8888-8888-888888888888"      # RC on, idle, archived

    def setUp(self):
        super().setUp()
        t = os.path.dirname(self.state)
        self.sessions = os.path.join(t, "app-sessions", "acct", "org")
        os.makedirs(self.sessions)
        now_ms = time.time() * 1000
        h = 3600 * 1000
        rows = [(self.IDLE, True, now_ms - 30 * h, now_ms - 60 * h, False),
                (self.NEVER, True, None, now_ms - 48 * h, False),
                (self.RECENT, True, now_ms - 1 * h, now_ms - 60 * h, False),
                (self.RC_OFF, False, now_ms - 30 * h, now_ms - 60 * h, False),
                (self.KEPT, True, now_ms - 30 * h, now_ms - 60 * h, False),
                (self.GONE, True, now_ms - 30 * h, now_ms - 60 * h, True),
                (RELAY_ID, True, now_ms - 30 * h, now_ms - 60 * h, False)]
        for sid, rc, last, created, gone in rows:
            row = {"sessionId": sid, "title": sid[-4:], "remoteControlUserEnabled": rc,
                   "createdAt": created, "isArchived": gone}
            if last is not None:
                row["latestUserFrameAt"] = last
            with open(os.path.join(self.sessions, sid + ".json"), "w") as f:
                json.dump(row, f)
        keep = os.path.join(t, "keep-on.json")
        with open(keep, "w") as f:
            json.dump({"session_ids": [self.KEPT]}, f)
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, "relay.json"), "w") as f:
            json.dump({"local_id": RELAY_ID}, f)
        self.env.update(SAVE_CLEAR_APP_SESSIONS=os.path.dirname(os.path.dirname(self.sessions)),
                        SAVE_CLEAR_RC_KEEP_ON=keep, SAVE_CLEAR_RC_IDLE_OFF_HOURS="24")

    def test_dry_run_lists_only_idle_sessions_with_rc_on(self):
        r = self.relay("rc-sweep", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got["rc_idle_off_hours"], 24.0)
        self.assertEqual(sorted(c["id"] for c in got["candidates"]), sorted([self.IDLE, self.NEVER]))
        self.assertFalse(os.path.exists(os.path.join(self.state, "rc-off.json")))

    def test_sweep_hands_out_once_until_acked(self):
        first = self.relay("rc-sweep")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout.count("RCOFF "), 2)
        self.assertIn(f"session_id={self.IDLE} enabled=false", first.stdout)
        self.assertIn(f"rc-ack {self.NEVER} ok", first.stdout)
        again = self.relay("rc-sweep")
        self.assertEqual(again.stdout.strip(), "NONE")           # handed out, not acked yet
        self.assertEqual(self.relay("rc-ack", self.IDLE, "ok").returncode, 0)
        third = self.relay("rc-sweep")
        self.assertEqual(third.stdout.count("RCOFF "), 1)         # acked, still on: handed again
        self.assertIn(self.IDLE, third.stdout)

    def test_off_when_the_setting_is_empty(self):
        self.env["SAVE_CLEAR_RC_IDLE_OFF_HOURS"] = ""
        r = self.relay("rc-sweep")
        self.assertTrue(r.stdout.startswith("OFF"), r.stdout)
        self.assertEqual(json.loads(self.relay("rc-sweep", "--dry-run").stdout)["candidates"], [])

    def test_rc_ack_needs_an_id_and_a_result(self):
        self.assertEqual(self.relay("rc-ack", "nope", "ok").returncode, 1)
        self.assertEqual(self.relay("rc-ack", self.IDLE, "maybe").returncode, 1)


class RcClear(Base):
    """A session with Remote Control on: the app will not clear it, so it turns its own Remote Control
    off right before clear_session and the relay turns it back on after the resume."""

    def relay(self, *args, **env):
        return self.run_py("bin/save-clear-relay", args, **env)

    def setUp(self):
        super().setUp()
        t = os.path.dirname(self.state)
        self.sessions = os.path.join(t, "app-sessions", "acct", "org")
        os.makedirs(self.sessions)
        self.env.update(SAVE_CLEAR_APP_SESSIONS=os.path.dirname(os.path.dirname(self.sessions)),
                        SAVE_CLEAR_APP_LOG=os.path.join(t, "main.log"))
        self.relay("setup", RELAY_ID)

    def app_row(self, **fields):
        row = {"sessionId": APP, "title": "app", "isArchived": False}
        row.update(fields)
        with open(os.path.join(self.sessions, APP + ".json"), "w") as f:
            json.dump(row, f)

    def heartbeat(self):
        with open(os.path.join(self.state, "relay-listener.alive"), "w") as f:
            f.write(f"{os.getpid()} {int(time.time())}\n")

    def job(self):
        d = os.path.join(self.state, "relay-inbox")
        [name] = [f for f in os.listdir(d) if f.endswith(".json")]
        with open(os.path.join(d, name)) as f:
            return json.load(f)

    def test_rc_on_and_listening_turns_itself_off(self):
        self.app_row(remoteControlUserEnabled=True)
        self.heartbeat()
        r = self.relay("request", CLAUDE_CODE_HOST_SESSION_ID=APP)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("RC-OFF-SELF", r.stdout)
        self.assertIn("session_id=self enabled=false", r.stdout)
        self.assertTrue(self.job().get("rc_on"))

    def test_rc_on_but_relay_not_listening_keeps_it_on(self):
        self.app_row(remoteControlUserEnabled=True)
        r = self.relay("request", CLAUDE_CODE_HOST_SESSION_ID=APP)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("RC-KEEP", r.stdout)
        self.assertNotIn("RC-OFF-SELF", r.stdout)
        self.assertFalse(self.job().get("rc_on"))

    def test_started_from_remote_control_is_locked(self):
        self.app_row(remoteControlUserEnabled=None, remoteControlSpawn={"ccrSessionId": "cse_x"})
        self.heartbeat()
        r = self.relay("request", CLAUDE_CODE_HOST_SESSION_ID=APP)
        self.assertIn("RC-LOCKED", r.stdout)
        self.assertNotIn("RC-OFF-SELF", r.stdout)
        self.assertFalse(self.job().get("rc_on"))

    def test_rc_off_prints_nothing_about_it(self):
        self.app_row(remoteControlUserEnabled=False)
        self.heartbeat()
        r = self.relay("request", CLAUDE_CODE_HOST_SESSION_ID=APP)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("RC-", r.stdout)

    def test_listener_turns_it_back_on_after_the_resume(self):
        self.relay("job", "--to", APP, "--message", "hello", "--after-clear", APP, "--rc-on")
        with open(self.env["SAVE_CLEAR_APP_LOG"], "w") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + f" [info] Clearing session {APP}\n")
        r = self.relay("listen", SAVE_CLEAR_RELAY_POLL="0.2", SAVE_CLEAR_RELAY_MAXWAIT="2")
        self.assertIn("TEXT: hello", r.stdout)
        self.assertIn(f"RCON {APP}: after the send_message", r.stdout)
        self.assertIn(f"session_id={APP} enabled=true", r.stdout)
        self.assertLess(r.stdout.index("TEXT: hello"), r.stdout.index("RCON "))

    def test_no_clear_turns_it_back_on_without_a_resume(self):
        self.relay("job", "--to", APP, "--message", "hello", "--after-clear", APP, "--rc-on")
        with open(self.env["SAVE_CLEAR_APP_LOG"], "w") as f:
            f.write("2026-01-01 00:00:00 [info] nothing\n")
        r = self.relay("listen", SAVE_CLEAR_RELAY_POLL="0.2", SAVE_CLEAR_RELAY_MAXWAIT="2",
                       SAVE_CLEAR_RELAY_RC_MAX="0")
        self.assertIn(f"RCON {APP}: call", r.stdout)
        self.assertNotIn("TEXT: hello", r.stdout)
        self.assertNotIn("send_message session_id", r.stdout)

    def test_job_without_rc_on_has_no_rcon(self):
        self.relay("job", "--to", APP, "--message", "hello")
        r = self.relay("listen", SAVE_CLEAR_RELAY_POLL="0.2", SAVE_CLEAR_RELAY_MAXWAIT="2")
        self.assertIn("TEXT: hello", r.stdout)
        self.assertNotIn("RCON", r.stdout)

    def test_rc_on_ack(self):
        self.assertEqual(self.relay("rc-on-ack", "nope", "ok").returncode, 1)
        self.assertEqual(self.relay("rc-on-ack", APP, "maybe").returncode, 1)
        self.assertEqual(self.relay("rc-on-ack", APP, "ok").returncode, 0)
        with open(os.path.join(self.state, "relay.log")) as f:
            self.assertIn(f"rc on ok: {APP}", f.read())

    def test_rules_allow_turning_on_only_the_rcon_session(self):
        out = self.relay("rules").stdout
        self.assertIn("RCON <id>", out)
        self.assertIn("rc-on-ack", out)


if __name__ == "__main__":
    unittest.main()
