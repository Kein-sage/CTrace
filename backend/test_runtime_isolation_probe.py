import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from compiler_runner import CompilerOutputLimitExceeded, run_compiler
import runtime_component as component
import runtime_isolation_probe as probe


UNIT = "ctrace-isolation-" + "a" * 32 + ".service"


class IsolationProbeTests(unittest.TestCase):
    def events(self, case, checks=None):
        frames = [{"event": "limits", "limits": dict(probe.LIMITS),
                   "quota_bytes": 8388608, "quota_inodes": 128}]
        output = json.dumps({"event": "sandbox_started", "baseline": True, "case": case}) + "\n"
        if checks is not None:
            output += json.dumps({"case": case, "passed": True, "checks": checks}) + "\n"
        frames.append({"event": "output", "stream": "stdout", "data": output})
        return frames

    def test_plan_is_read_only_and_contains_fixed_resource_policy(self):
        with (
            patch("subprocess.Popen", side_effect=AssertionError("process started")),
            patch.object(Path, "mkdir", side_effect=AssertionError("directory created")),
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            self.assertEqual(probe.main([]), 0)
        result = json.loads(output.getvalue())
        self.assertFalse(result["executes"])
        command = result["command"]
        self.assertIn("--property=PrivateUsers=yes", command)
        self.assertIn("--property=MemoryMax=67108864", command)
        self.assertIn("--property=TasksMax=16", command)
        self.assertTrue(any("size=8388608,nr_inodes=128" in argument for argument in command))

    def test_arbitrary_source_or_command_is_rejected_without_launch(self):
        for args in (["source.c"], ["--run", "bash"], ["--run", "files", "extra"]):
            with (
                self.subTest(args=args), patch.object(probe, "run_probe") as run,
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                self.assertEqual(probe.main(args), 2)
                run.assert_not_called()

    def test_job_escape_and_unit_injection_are_rejected(self):
        for job, unit in ((Path("/tmp/other"), UNIT), (probe.ROOT / "tmp" / UNIT, UNIT + "; command")):
            with self.subTest(job=job), self.assertRaises(ValueError):
                probe.service_command("files", job, unit)

    def test_missing_component_is_rejected_before_task_or_workspace(self):
        with (
            patch.object(probe, "verify_binary", side_effect=RuntimeError("missing")),
            patch.object(Path, "mkdir", side_effect=AssertionError("workspace created")),
            patch("subprocess.Popen", side_effect=AssertionError("process started")),
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            self.assertEqual(probe.main(["--run", "files"]), 2)
        self.assertFalse(json.loads(output.getvalue())["runtime_ready"])

    def test_sandbox_policy_has_no_host_directory_or_service_socket_bind(self):
        job = probe.ROOT / "tmp" / UNIT
        with patch.object(probe, "verify_binary", return_value=Path("/trusted/bwrap")):
            command = probe.sandbox_command("files", job)
        for flag in ("--unshare-all", "--unshare-user", "--disable-userns", "--assert-userns-disabled", "--clearenv", "--new-session"):
            self.assertIn(flag, command)
        writable = [command[index + 1:index + 3] for index, argument in enumerate(command) if argument == "--bind"]
        self.assertEqual(writable, [[str(job / "work"), "/work"]])
        for index, argument in enumerate(command):
            if argument == "--ro-bind":
                self.assertNotIn(command[index + 1], ["/", "/home", "/run", "/mnt", str(probe.ROOT)])
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", command)

    def test_namespace_started_evidence_is_mandatory_even_for_oom(self):
        events = self.events("memory")
        self.assertTrue(probe.evaluate("memory", 1, events, {"Result": "oom-kill"}, True))
        self.assertFalse(probe.evaluate("memory", 1, events[:1], {"Result": "oom-kill"}, True))
        self.assertFalse(probe.evaluate("memory", None, events, {"Result": "oom-kill"}, True))
        self.assertFalse(probe.evaluate("memory", 1, events, {"Result": "exit-code"}, True))

    def test_limits_quota_and_cleanup_cannot_be_skipped(self):
        for key in ("limits", "quota_bytes", "quota_inodes"):
            with self.subTest(key=key):
                events = self.events("timeout")
                events[0][key] = None
                self.assertFalse(probe.evaluate("timeout", 1, events, {"Result": "timeout"}, True))
        self.assertFalse(probe.evaluate("timeout", 1, self.events("timeout"), {"Result": "timeout"}, False))

    def test_partial_or_false_boundary_checks_do_not_pass(self):
        events = self.events("environment", {"namespace_and_privilege_baseline": True})
        events.append({"event": "finished", "returncode": 0})
        self.assertFalse(probe.evaluate("environment", 0, events, {}, True))
        checks = {key: True for key in probe.REQUIRED_CHECKS["environment"]}
        checks["only_standard_fds"] = False
        self.assertFalse(probe.evaluate("environment", 0, self.events("environment", checks), {}, True))

    def test_pids_requires_actual_kernel_event(self):
        checks = {key: True for key in probe.REQUIRED_CHECKS["pids"]}
        events = self.events("pids", checks)
        events.append({"event": "finished", "returncode": 0, "pids_events_delta": 0})
        self.assertFalse(probe.evaluate("pids", 0, events, {}, True))
        events[-1]["pids_events_delta"] = 1
        self.assertTrue(probe.evaluate("pids", 0, events, {}, True))

    def test_output_limit_needs_both_baseline_and_collector_event(self):
        events = self.events("output")
        self.assertFalse(probe.evaluate("output", 0, events, {}, True))
        events.append({"event": "output_limit"})
        self.assertTrue(probe.evaluate("output", 0, events, {}, True))
        self.assertFalse(probe.evaluate("output", 1, events, {}, True))

    def test_failed_stop_still_resets_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            def manage(operation, unit):
                if operation == "stop":
                    raise subprocess.TimeoutExpired("stop", 5)
            with (
                patch.object(probe, "ROOT", Path(temporary)),
                patch.object(probe, "verify_binary", return_value=Path("/trusted/bwrap")),
                patch.object(probe, "run_compiler", side_effect=OSError("failed to launch")),
                patch.object(probe, "manager_command", side_effect=manage) as manager,
                patch.object(probe, "unit_state", return_value={"LoadState": "not-found", "ActiveState": "inactive"}),
                patch.object(probe.time, "monotonic", side_effect=[0, 4]),
            ):
                report = probe.run_probe("files")
            self.assertFalse(report["passed"])
            self.assertEqual([call.args[0] for call in manager.call_args_list], ["stop", "reset-failed"])
            self.assertFalse(list((Path(temporary) / "tmp").iterdir()))


class ComponentAndCollectorTests(unittest.TestCase):
    def test_unverified_archive_is_never_extracted(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "package.deb"
            archive.write_bytes(b"untrusted")
            with patch.object(component.subprocess, "run", side_effect=AssertionError("extracted")):
                with self.assertRaises(RuntimeError):
                    component.provision(archive)

    def test_bad_component_checksum_is_never_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "bwrap"
            binary.write_bytes(b"not the pinned executable")
            binary.chmod(0o755)
            with (
                patch.object(component, "ROOT", Path(temporary)),
                patch.object(component, "metadata", return_value={"binary_sha256": "0" * 64}),
                patch.object(component, "binary_path", return_value=binary),
                patch.object(component.sys, "platform", "linux"),
                patch.object(component.platform, "machine", return_value="x86_64"),
            ):
                with self.assertRaises(RuntimeError):
                    component.verify_binary()

    def test_collector_uses_explicit_environment_cwd_and_empty_stdin(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = run_compiler([sys.executable, "-c", "import os,sys; print(os.environ['CTRACE_TEST']); print(len(sys.stdin.buffer.read())); print(os.getcwd())"],
                                  stdin=subprocess.DEVNULL, env={"CTRACE_TEST": "fixed"}, cwd=temporary)
            self.assertEqual(result.stdout.splitlines(), ["fixed", "0", str(Path(temporary).resolve())])

    def test_callback_never_receives_bytes_beyond_budget(self):
        chunks = []
        with self.assertRaises(CompilerOutputLimitExceeded):
            run_compiler([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x'*8193)"],
                         max_output_bytes=4096, on_output=lambda stream, chunk: chunks.append(chunk))
        self.assertLessEqual(sum(map(len, chunks)), 4096)

    def test_callback_error_still_terminates_the_process(self):
        def fail(stream, chunk):
            raise RuntimeError("callback failed")
        with self.assertRaisesRegex(RuntimeError, "callback failed"):
            run_compiler([sys.executable, "-c", "import time; print('ready',flush=True); time.sleep(30)"], on_output=fail)


if __name__ == "__main__":
    unittest.main()
