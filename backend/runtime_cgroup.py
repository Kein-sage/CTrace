"""Read-only cgroup v2 observations, never delegation or limit enforcement."""

import os
from pathlib import Path, PurePosixPath
import re


CONTROL_FILES = ("cgroup.procs", "cgroup.threads", "cgroup.subtree_control")
LIMIT_FILES = ("memory.max", "memory.swap.max", "pids.max")


def read_observation(path):
    try:
        return {"value": path.read_text(encoding="utf-8").strip(), "error": None}
    except (OSError, UnicodeError) as error:
        return {"value": None, "error": type(error).__name__}


def absolute_path(value):
    if not value.startswith("/") or "\x00" in value:
        raise ValueError("InvalidCgroupPath")
    if any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("InvalidCgroupPath")
    return PurePosixPath(value)


def decode_mount_path(value):
    return re.sub(r"\\(040|011|012|134)",
                  lambda match: chr(int(match.group(1), 8)), value)


def locate_cgroup(process_cgroup, mountinfo):
    """Map kernel metadata conservatively; ambiguous views stay unsupported."""
    entries = [line[3:] for line in process_cgroup.splitlines()
               if line.startswith("0::")]
    if len(entries) != 1:
        raise ValueError("CgroupV2PathUnavailable")
    process_path = absolute_path(entries[0])
    matches = []
    for line in mountinfo.splitlines():
        before, separator, after = line.partition(" - ")
        fields, filesystem = before.split(), after.split()
        if not separator or not filesystem or filesystem[0] != "cgroup2":
            continue
        if len(fields) < 6 or len(filesystem) < 3:
            raise ValueError("InvalidMountInfo")
        mount_root = absolute_path(decode_mount_path(fields[3]))
        mount_point = absolute_path(decode_mount_path(fields[4]))
        try:
            relative = process_path.relative_to(mount_root)
        except ValueError:
            continue
        matches.append({
            "mount_point": str(mount_point),
            "path": str(mount_point / relative),
            "mount_read_only": "ro" in fields[5].split(",")
            or "ro" in filesystem[2].split(","),
        })
    if len(matches) != 1:
        raise ValueError("CgroupMountAmbiguousOrUnavailable")
    return matches[0]


def inspect_current_cgroup(process_cgroup):
    if process_cgroup["error"] is not None:
        return {"error": process_cgroup["error"]}
    mountinfo = read_observation(Path("/proc/self/mountinfo"))
    if mountinfo["error"] is not None:
        return {"error": mountinfo["error"]}
    try:
        location = locate_cgroup(process_cgroup["value"], mountinfo["value"])
        mount = Path(location["mount_point"]).resolve(strict=True)
        directory = Path(location["path"]).resolve(strict=True)
        if not directory.is_relative_to(mount):
            raise ValueError("CgroupPathOutsideMount")
        if not directory.is_dir():
            raise ValueError("CgroupDirectoryUnavailable")
    except (OSError, RuntimeError, ValueError) as error:
        return {"error": str(error) if isinstance(error, ValueError)
                else type(error).__name__}

    return {
        **location,
        "error": None,
        "directory_writable": os.access(directory, os.W_OK | os.X_OK,
                                        effective_ids=True),
        "control_writable": {
            name: os.access(directory / name, os.W_OK, effective_ids=True)
            for name in CONTROL_FILES
        },
        "controllers": read_observation(directory / "cgroup.controllers"),
        "subtree_control": read_observation(directory / "cgroup.subtree_control"),
        "limits": {name: read_observation(directory / name) for name in LIMIT_FILES},
    }
