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
                        SAVE_CLEAR_APP_LOG=os.path.join(t, "app-main.log"))
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


if __name__ == "__main__":
    unittest.main()
