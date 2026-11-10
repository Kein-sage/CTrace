import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import runtime_preflight


class RuntimePreflightTests(unittest.TestCase):
    def observations(self):
        return {
            "system": "Linux",
            "effective_uid": 1000,
            "tools": {name: "/usr/bin/" + name for name in runtime_preflight.TOOLS},
            "cgroup_v2_controllers": {"value": "cpu memory pids", "error": None},
            "process_cgroup": {"value": "0::/example", "error": None},
            "current_cgroup": {
                "error": None,
                "controllers": {"value": "cpu memory pids", "error": None},
                "mount_read_only": False,
                "directory_writable": True,
                "control_writable": {"cgroup.procs": True},
            },
        }

    def codes(self, report):
        return {item["code"] for item in report["blockers"]}

    def test_all_prerequisites_present_still_refuses_execution(self):
        observations = self.observations()
        original = copy.deepcopy(observations)
        report = runtime_preflight.build_report(observations)
        self.assertFalse(report["ready"])
        self.assertEqual(report["kind"], "runtime_environment_inventory")
        self.assertEqual(self.codes(report), {
            "ISOLATION_NOT_VERIFIED", "LIMITS_NOT_VERIFIED",
        })
        self.assertEqual(observations, original)

    def test_missing_tools_report_actionable_blockers(self):
        observations = self.observations()
        observations["tools"]["bwrap"] = None
        observations["tools"]["clang"] = None
        report = runtime_preflight.build_report(observations)
        self.assertFalse(report["ready"])
        self.assertTrue({"SANDBOX_UNAVAILABLE", "COMPILER_NOT_FOUND"}.issubset(
            self.codes(report)
        ))

    def test_unsupported_platform_and_root_are_blocked(self):
        for system, uid in (("Windows", None), ("Linux", 0)):
            with self.subTest(system=system, uid=uid):
                observations = self.observations()
                observations.update(system=system, effective_uid=uid)
                self.assertIn("SANDBOX_UNAVAILABLE", self.codes(
                    runtime_preflight.build_report(observations)
                ))

    def test_missing_unreadable_or_incomplete_controllers(self):
        for value in (None, "", "memory", "pids", "notmemory pids"):
            with self.subTest(value=value):
                observations = self.observations()
                observations["current_cgroup"]["controllers"]["value"] = value
                self.assertIn("LIMITS_UNAVAILABLE", self.codes(
                    runtime_preflight.build_report(observations)
                ))

    def test_root_controllers_do_not_override_unavailable_current_cgroup(self):
        for current in (
            {"error": "PermissionError"},
            {"error": "CgroupMountAmbiguousOrUnavailable"},
        ):
            with self.subTest(current=current):
                observations = self.observations()
                observations["current_cgroup"] = current
                self.assertIn("LIMITS_UNAVAILABLE", self.codes(
                    runtime_preflight.build_report(observations)
                ))

    def test_read_only_mount_or_missing_write_access_blocks_limits(self):
        for key, value in (("mount_read_only", True), ("directory_writable", False),
                           ("control_writable", {"cgroup.procs": False})):
            with self.subTest(key=key):
                observations = self.observations()
                observations["current_cgroup"][key] = value
                self.assertIn("LIMITS_UNAVAILABLE", self.codes(
                    runtime_preflight.build_report(observations)
                ))

    def test_observation_records_read_failure_without_claiming_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controllers"
            self.assertEqual(runtime_preflight.read_observation(path), {
                "value": None, "error": "FileNotFoundError",
            })
            path.write_bytes(b"\xff")
            self.assertEqual(runtime_preflight.read_observation(path), {
                "value": None, "error": "UnicodeDecodeError",
            })
            with patch.object(Path, "read_text", side_effect=PermissionError):
                self.assertEqual(runtime_preflight.read_observation(path)["error"],
                                 "PermissionError")

    def test_inventory_does_not_launch_tools_or_write_files(self):
        with (
            patch("subprocess.Popen", side_effect=AssertionError("process launched")),
            patch.object(Path, "write_text", side_effect=AssertionError("file written")),
            patch.object(runtime_preflight.platform, "system", return_value="Linux"),
            patch.object(runtime_preflight.os, "geteuid", return_value=1000, create=True),
            patch.object(runtime_preflight.shutil, "which", return_value="/candidate/tool"),
            patch.object(Path, "read_text", return_value="cpu memory pids"),
        ):
            report = runtime_preflight.build_report(runtime_preflight.inspect_environment())
        self.assertFalse(report["ready"])

    def test_windows_does_not_read_linux_pseudo_files(self):
        with (
            patch.object(runtime_preflight.platform, "system", return_value="Windows"),
            patch.object(runtime_preflight.shutil, "which", return_value=None),
            patch.object(Path, "read_text", side_effect=AssertionError("Linux file read")),
        ):
            observations = runtime_preflight.inspect_environment()
        self.assertIsNone(observations["effective_uid"])
        self.assertEqual(observations["process_cgroup"]["error"], "UnsupportedPlatform")

    def test_cli_inventory_is_json_and_exits_two(self):
        with (
            patch.object(runtime_preflight, "inspect_environment", return_value=self.observations()),
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            result = runtime_preflight.main([])
        self.assertEqual(result, 2)
        self.assertFalse(json.loads(output.getvalue())["ready"])

    def test_cli_rejects_source_and_options_before_inventory(self):
        for args in (["source.c"], ["--run"], ["--help"]):
            with (
                self.subTest(args=args),
                patch.object(runtime_preflight, "inspect_environment") as inspect,
                patch("sys.stdout", new_callable=io.StringIO) as output,
            ):
                self.assertEqual(runtime_preflight.main(args), 2)
                self.assertEqual(json.loads(output.getvalue())["error"]["code"],
                                 "INVALID_ARGUMENTS")
                inspect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
