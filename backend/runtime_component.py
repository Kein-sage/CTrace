"""Verify a pinned project-local unprivileged bubblewrap binary."""

import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent


def metadata():
    return json.loads((ROOT / "tools/bubblewrap-lock.json").read_text())


def binary_path():
    return ROOT / ".runtime" / ("bubblewrap-" + metadata()["version"]) / "usr/bin/bwrap"


def verify_binary():
    lock = metadata()
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise RuntimeError("Pinned component supports Linux amd64 only")
    binary = binary_path()
    if not binary.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError("Component path leaves the project")
    info = binary.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & (stat.S_ISUID | stat.S_ISGID):
        raise RuntimeError("Component must be a regular non-setuid executable")
    if not os.access(binary, os.X_OK):
        raise RuntimeError("Component is not executable")
    if "security.capability" in os.listxattr(binary):
        raise RuntimeError("Component file capabilities are forbidden")
    if hashlib.sha256(binary.read_bytes()).hexdigest() != lock["binary_sha256"]:
        raise RuntimeError("Component checksum mismatch")
    return binary


def provision(archive):
    lock = metadata()
    archive = Path(archive)
    if archive.stat().st_size != lock["package_size"]:
        raise RuntimeError("Package size mismatch")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != lock["package_sha256"]:
        raise RuntimeError("Package checksum mismatch")
    destination = ROOT / ".runtime" / ("bubblewrap-" + lock["version"])
    if not destination.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError("Extraction path leaves the project")
    if not destination.exists():
        destination.mkdir(parents=True)
        subprocess.run(["/usr/bin/dpkg-deb", "--extract", str(archive.resolve()), str(destination)], check=True, timeout=10)
    return verify_binary()


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Usage: python3 backend/runtime_component.py PACKAGE.deb")
        print(json.dumps({"binary": str(provision(sys.argv[1])), "system_install": False}))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(2)
