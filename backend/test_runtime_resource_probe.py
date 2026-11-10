import io
import json
import subprocess
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import patch

import runtime_resource_probe as probe


UNIT = "ctrace-resource-probe-" + "a" * 32 + ".service"


class ResourceProbeTests(unittest.TestCase):
    def snapshot(self):
        return {"uid": 1000, "limits": dict(probe.LIMITS),
                "cgroup": "/sys/fs/cgroup/test/" + UNIT}

    def test_plan_does_not_launch_processes(self):
        for args in ([], ["--plan", "memory"], ["--plan", "descendants"]):
            with (
                self.subTest(args=args),
                patch("subprocess.Popen", side_effect=AssertionError("launched")),
                patch("sys.stdout", new_callable=io.StringIO) as output,
            ):
                self.assertEqual(probe.main(args), 0)
                report = json.loads(output.getvalue())
                self.assertFalse(report["executes"])
                self.assertTrue(report["requires_runtime_state_permission"])

    def test_command_cannot_accept_arbitrary_workloads_or_unit_names(self):
        for case, unit in (("source.c", UNIT), ("snapshot", "other.service"),
                           ("snapshot", UNIT + "; echo injected")):
            with self.subTest(case=case, unit=unit), self.assertRaises(ValueError):
                probe.build_command(case, unit)
        command = probe.build_command("snapshot", UNIT)
        self.assertEqual(command[0], "/usr/bin/systemd-run")
        self.assertIn("--user", command)
        self.assertIn("--property=MemoryMax=67108864", command)
        self.assertIn("--property=MemorySwapMax=0", command)
        self.assertIn("--property=TasksMax=16", command)
        self.assertIn("--property=RuntimeMaxSec=5s", command)
        self.assertIn("--property=KillMode=control-group", command)
        self.assertEqual(command[-3:], ["--worker", "snapshot", UNIT])

    def test_invalid_arguments_do_not_run_probe(self):
        for args in (["source.c"], ["--run", "source.c"], ["--run", "snapshot", "extra"]):
            with (
                self.subTest(args=args), patch.object(probe, "run_probe") as run,
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                self.assertEqual(probe.main(args), 2)
                run.assert_not_called()

    def test_worker_refuses_wrong_cgroup_before_workload(self):
        with (
            patch.object(probe.os, "geteuid", return_value=1000, create=True),
            patch.object(Path, "read_text", return_value="0::/non-systemd") as read,
            patch.object(probe.threading.Thread, "start", side_effect=AssertionError("thread")),
        ):
            with self.assertRaises(ValueError):
                probe.worker("pids", UNIT)
        self.assertEqual(read.call_count, 1)

    def test_worker_refuses_wrong_limits_before_workload(self):
        with (
            patch.object(probe.os, "geteuid", return_value=1000, create=True),
            patch.object(Path, "read_text", side_effect=["0::/test/" + UNIT, "max", "0", "16"]),
        ):
            with self.assertRaises(ValueError):
                probe.worker_snapshot(UNIT)

    def test_snapshot_success_requires_limits_and_cleanup(self):
        frame = self.snapshot()
        self.assertTrue(probe.evaluate("snapshot", 0, [frame], {}, True))
        self.assertFalse(probe.evaluate("snapshot", 0, [frame], {}, False))
        self.assertFalse(probe.evaluate("snapshot", 1, [frame], {}, True))
        frame["limits"]["memory.max"] = "max"
        self.assertFalse(probe.evaluate("snapshot", 0, [frame], {}, True))

    def test_signal_or_nonzero_exit_alone_is_not_memory_limit_evidence(self):
        frames = [self.snapshot()]
        for result in ({}, {"Result": "exit-code"}, {"Result": "signal"}):
            with self.subTest(result=result):
                self.assertFalse(probe.evaluate("memory", 137, frames, result, True))
        self.assertTrue(probe.evaluate("memory", 137, frames, {"Result": "oom-kill"}, True))
        self.assertFalse(probe.evaluate("memory", 0, frames, {"Result": "oom-kill"}, True))

    def test_controller_timeout_is_not_runtime_limit_evidence(self):
        frames = [self.snapshot()]
        self.assertFalse(probe.evaluate("timeout", None, frames, {}, True))
        self.assertFalse(probe.evaluate("timeout", None, frames, {"Result": "timeout"}, True))
        self.assertFalse(probe.evaluate("timeout", 1, frames, {"Result": "signal"}, True))
        self.assertTrue(probe.evaluate("timeout", 1, frames, {"Result": "timeout"}, True))

    def test_pids_error_requires_cgroup_event_and_bounded_count(self):
        evidence = {"pids_limit_hit": True, "pids_events_delta": 1, "pids_current": 16}
        frames = [self.snapshot(), evidence]
        self.assertTrue(probe.evaluate("pids", 0, frames, {}, True))
        evidence["pids_events_delta"] = 0
        self.assertFalse(probe.evaluate("pids", 0, frames, {}, True))
        evidence.update(pids_events_delta=1, pids_current=17)
        self.assertFalse(probe.evaluate("pids", 0, frames, {}, True))

    def test_descendant_identity_must_be_gone(self):
        frames = [self.snapshot(), {"child_pid": 1234, "child_start": "456"}]
        with patch.object(probe, "process_start", return_value="456"):
            self.assertFalse(probe.evaluate("descendants", 0, frames, {}, True))
        for current in (None, "789"):
            with patch.object(probe, "process_start", return_value=current):
                self.assertTrue(probe.evaluate("descendants", 0, frames, {}, True))
        self.assertFalse(probe.evaluate("descendants", 0, frames, {}, False))

    def test_client_start_failure_still_cleans_only_its_own_unit(self):
        with (
            patch.object(probe.subprocess, "Popen", side_effect=OSError("failed")),
            patch.object(probe, "manager_command") as manager,
            patch.object(probe, "unit_state", return_value={
                "LoadState": "not-found", "ActiveState": "inactive",
            }),
            patch.object(probe.time, "monotonic", side_effect=[0, 4]),
        ):
            report = probe.run_probe("snapshot")
        self.assertFalse(report["passed"])
        self.assertFalse(report["cleanup_confirmed"])
        self.assertEqual(manager.call_args_list[0].args, ("stop", report["unit"]))
        self.assertEqual(manager.call_args_list[1].args, ("reset-failed", report["unit"]))

    def test_stop_timeout_still_terminates_own_client(self):
        client = SimpleNamespace(pid=12345, poll=lambda: None)
        responses = iter([subprocess.TimeoutExpired("client", 12), ("", "")])
        def communicate(**kwargs):
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response
        client.communicate = communicate
        def manage(*args):
            if args[0] == "stop":
                raise subprocess.TimeoutExpired("stop", 5)
        with (
            patch.object(probe.subprocess, "Popen", return_value=client),
            patch.object(probe, "manager_command", side_effect=manage),
            patch.object(probe, "unit_state", return_value={
                "LoadState": "not-found", "ActiveState": "inactive",
            }),
            patch.object(probe.time, "monotonic", side_effect=[0, 4]),
            patch.object(probe.os, "killpg") as kill,
        ):
            report = probe.run_probe("timeout")
        self.assertFalse(report["passed"])
        self.assertTrue(report["errors"])
        kill.assert_called_once_with(12345, probe.signal.SIGKILL)

    def test_cli_cannot_advertise_runtime_ready(self):
        report = {"passed": True, "runtime_ready": False}
        with (
            patch.object(probe.sys, "platform", "linux"),
            patch.object(probe.os, "geteuid", return_value=1000, create=True),
            patch.object(probe, "run_probe", return_value=report),
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            self.assertEqual(probe.main(["--run", "snapshot"]), 0)
        self.assertFalse(json.loads(output.getvalue())["runtime_ready"])


if __name__ == "__main__":
    unittest.main()
