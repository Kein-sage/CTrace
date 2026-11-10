from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime_cgroup import inspect_current_cgroup, locate_cgroup


def mount_line(root="/", target="/sys/fs/cgroup", options="rw", super_options="rw"):
    return f"25 24 0:25 {root} {target} {options} shared:4 - cgroup2 cgroup2 {super_options}"


class CgroupLocationTests(unittest.TestCase):
    def test_standard_and_namespaced_mounts(self):
        for root, process, expected in (
            ("/", "/non-systemd", "/sys/fs/cgroup/non-systemd"),
            ("/delegated", "/delegated/worker", "/sys/fs/cgroup/worker"),
            ("/", "/", "/sys/fs/cgroup"),
        ):
            with self.subTest(root=root, process=process):
                location = locate_cgroup("0::" + process, mount_line(root=root))
                self.assertEqual(location["path"], expected)
                self.assertFalse(location["mount_read_only"])

    def test_mount_escape_is_decoded_once(self):
        location = locate_cgroup("0::/worker", mount_line(target=r"/cgroup\040space\134040"))
        self.assertEqual(location["path"], "/cgroup space" + chr(92) + "040/worker")

    def test_both_mount_and_superblock_read_only_flags(self):
        for options, super_options in (("ro", "rw"), ("rw", "ro")):
            with self.subTest(options=options):
                self.assertTrue(locate_cgroup("0::/worker", mount_line(
                    options=options, super_options=super_options
                ))["mount_read_only"])

    def test_invalid_cgroup_paths_are_rejected(self):
        for process in ("relative", "/a/../b", "/./b", "/a/../../b", "/a\x00b"):
            with self.subTest(process=process), self.assertRaises(ValueError):
                locate_cgroup("0::" + process, mount_line())

    def test_missing_duplicate_or_non_v2_entries_are_rejected(self):
        for process in ("", "1:memory:/worker", "0::/one\n0::/two"):
            with self.subTest(process=process), self.assertRaises(ValueError):
                locate_cgroup(process, mount_line())

    def test_unmapped_ambiguous_and_malformed_mounts_are_rejected(self):
        for mounts in (
            "", mount_line(root="/other"),
            mount_line() + "\n" + mount_line(target="/another"),
            "25 24 - cgroup2 cgroup2 rw",
            mount_line().replace("cgroup2", "tmpfs"),
        ):
            with self.subTest(mounts=mounts), self.assertRaises(ValueError):
                locate_cgroup("0::/worker", mounts)


class CgroupInventoryTests(unittest.TestCase):
    def test_current_subtree_metadata_and_limits_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            mount = Path(temporary)
            directory = mount / "worker"
            directory.mkdir()
            for name, value in {
                "cgroup.controllers": "cpu memory pids",
                "cgroup.subtree_control": "",
                "memory.max": "max", "memory.swap.max": "0", "pids.max": "16",
            }.items():
                (directory / name).write_text(value)
            from runtime_cgroup import read_observation
            def read(path):
                if str(path) == "/proc/self/mountinfo":
                    return {"value": mount_line(target=str(mount)), "error": None}
                return read_observation(path)
            with (
                patch("runtime_cgroup.read_observation", side_effect=read),
                patch("runtime_cgroup.os.access", return_value=False),
                patch("subprocess.Popen", side_effect=AssertionError("process launched")),
                patch.object(Path, "write_text", side_effect=AssertionError("file written")),
                patch.object(Path, "mkdir", side_effect=AssertionError("directory created")),
            ):
                report = inspect_current_cgroup({"value": "0::/worker", "error": None})
            self.assertIsNone(report["error"])
            self.assertEqual(report["path"], str(directory))
            self.assertFalse(report["directory_writable"])
            self.assertFalse(any(report["control_writable"].values()))
            self.assertEqual(report["subtree_control"]["value"], "")
            self.assertEqual(report["limits"]["memory.max"]["value"], "max")
            self.assertEqual(report["limits"]["pids.max"]["value"], "16")

    def test_unreadable_kernel_metadata_stays_unknown(self):
        with patch("runtime_cgroup.read_observation") as read:
            self.assertEqual(inspect_current_cgroup({
                "value": None, "error": "PermissionError",
            }), {"error": "PermissionError"})
            read.assert_not_called()
            read.return_value = {"value": None, "error": "FileNotFoundError"}
            self.assertEqual(inspect_current_cgroup({
                "value": "0::/worker", "error": None,
            }), {"error": "FileNotFoundError"})

    def test_symlink_escape_does_not_read_outside_mount(self):
        with (
            patch("runtime_cgroup.read_observation", return_value={
                "value": mount_line(), "error": None,
            }) as read,
            patch.object(Path, "resolve", side_effect=[Path("/cgroup"), Path("/outside")]),
        ):
            report = inspect_current_cgroup({"value": "0::/worker", "error": None})
        self.assertEqual(report["error"], "CgroupPathOutsideMount")
        self.assertEqual(read.call_count, 1)

    def test_missing_directory_is_an_observation_error(self):
        with (
            patch("runtime_cgroup.read_observation", return_value={
                "value": mount_line(), "error": None,
            }),
            patch.object(Path, "resolve", side_effect=FileNotFoundError),
        ):
            report = inspect_current_cgroup({"value": "0::/worker", "error": None})
        self.assertEqual(report, {"error": "FileNotFoundError"})

    def test_missing_limit_files_are_not_reported_as_unlimited(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch("runtime_cgroup.read_observation") as read:
                read.side_effect = [
                    {"value": mount_line(target=temporary), "error": None},
                    *[{"value": None, "error": "FileNotFoundError"}] * 5,
                ]
                report = inspect_current_cgroup({"value": "0::/", "error": None})
            self.assertIsNone(report["error"])
            self.assertEqual(report["limits"]["memory.max"], {
                "value": None, "error": "FileNotFoundError",
            })


if __name__ == "__main__":
    unittest.main()
