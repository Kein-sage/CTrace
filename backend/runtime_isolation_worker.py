"""Fixed trusted boundary tests, executed only inside the probe sandbox."""

import ctypes
import errno
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import threading


def deny_write(path):
    try:
        with open(path, "ab") as stream:
            stream.write(b"forbidden")
    except OSError as error:
        return error.errno in {errno.EROFS, errno.EACCES, errno.ENOENT}
    return False


def main():
    case = sys.argv[1]
    config = json.loads(Path("/config.json").read_text())
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    space = os.statvfs("/work")
    namespaces = {name: os.readlink("/proc/self/ns/" + name) for name in config["namespaces"]}
    baseline = (os.geteuid() != 0 and os.getpid() <= 3
                and all(namespaces[name] != value for name, value in config["namespaces"].items())
                and status["NoNewPrivs"].strip() == "1"
                and all(int(status[name].strip(), 16) == 0 for name in ("CapEff", "CapPrm", "CapAmb"))
                and bool(os.statvfs("/").f_flag & os.ST_RDONLY)
                and space.f_blocks * space.f_frsize == 8388608 and space.f_files == 128)
    if not baseline:
        print(json.dumps({"baseline": False, "uid": os.geteuid(), "pid": os.getpid(), "namespaces": namespaces, "host_namespaces": config["namespaces"], "caps": {name: status[name].strip() for name in ("CapEff", "CapPrm", "CapAmb", "NoNewPrivs")}, "root_readonly": bool(os.statvfs("/").f_flag & os.ST_RDONLY), "bytes": space.f_blocks * space.f_frsize, "inodes": space.f_files}), flush=True)
        return 2
    print(json.dumps({"event": "sandbox_started", "baseline": True, "case": case}), flush=True)
    checks = {"namespace_and_privilege_baseline": True}
    if case == "files":
        hidden = ["/home", "/root", "/mnt", "/run", "/sys", config["outside_marker"]]
        checks["host_paths_hidden"] = all(not Path(path).exists() for path in hidden)
        checks["readonly_marker"] = Path("/readonly-marker").read_text() == config["marker"] and deny_write("/readonly-marker")
        checks["root_readonly"] = deny_write("/outside-work")
        checks["toolchain_readonly"] = all(bool(os.statvfs(path).f_flag & os.ST_RDONLY) for path in ["/usr/bin/clang", "/usr/include", "/usr/lib/python3.12"])
        checks["clang_and_python_visible"] = all(Path(path).read_bytes()[:4] == b"\x7fELF" for path in ["/usr/bin/clang", "/usr/bin/python3"])
        checks["host_process_hidden"] = not Path(f"/proc/{config['host_pid']}").exists()
        Path("/work/allowed").write_text("ok")
        checks["private_work_writable"] = Path("/work/allowed").read_text() == "ok"
    elif case == "network":
        checks["only_loopback"] = {name for _, name in socket.if_nameindex()} == {"lo"}
        checks["no_ipv4_route"] = len(Path("/proc/net/route").read_text().splitlines()) <= 1
        for family, address, key in [
            (socket.AF_INET, ("192.0.2.1", 9), "ipv4_unreachable"),
            (socket.AF_INET6, ("2001:db8::1", 9), "ipv6_unreachable"),
        ]:
            try:
                with socket.socket(family, socket.SOCK_STREAM) as connection:
                    connection.settimeout(0.2)
                    code = connection.connect_ex(address)
                checks[key] = code in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EAFNOSUPPORT}
            except OSError as error:
                checks[key] = error.errno in {errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT}
        try:
            with socket.socket(socket.AF_UNIX) as connection:
                connection.connect(chr(0) + config["abstract_socket"])
            checks["host_abstract_socket_hidden"] = False
        except OSError as error:
            checks["host_abstract_socket_hidden"] = error.errno in {errno.ECONNREFUSED, errno.ENOENT}
        checks["host_service_sockets_hidden"] = all(not Path(path).exists() for path in ["/run/user/1000/bus", "/var/run/docker.sock"])
    elif case == "environment":
        expected = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/nonexistent", "TMPDIR": "/work", "PWD": "/work"}
        checks["environment_allowlist"] = dict(os.environ) == expected
        descriptors = []
        for path in Path("/proc/self/fd").iterdir():
            try:
                os.readlink(path)
            except FileNotFoundError:
                continue
            descriptors.append(int(path.name))
        checks["only_standard_fds"] = set(descriptors) == {0, 1, 2}
        checks["stdin_eof"] = sys.stdin.buffer.read(1) == b""
    elif case == "namespaces":
        libc = ctypes.CDLL(None, use_errno=True)
        for flag, key in [(0x10000000, "nested_userns_denied"), (0x00020000, "mount_namespace_denied")]:
            ctypes.set_errno(0)
            result = libc.unshare(flag)
            checks[key] = result == -1 and ctypes.get_errno() in {errno.EPERM, errno.ENOSPC}
    elif case == "space":
        written = 0
        hit = False
        try:
            with open("/work/space", "wb", buffering=0) as stream:
                for _ in range(9):
                    written += stream.write(b"x" * (1024 * 1024))
        except OSError as error:
            hit = error.errno == errno.ENOSPC
        checks["space_enforced"] = hit and written <= 8388608 and os.statvfs("/work").f_bavail == 0
    elif case == "inodes":
        hit = False
        count = 0
        try:
            for index in range(140):
                Path(f"/work/file-{index}").touch(exist_ok=False)
                count += 1
        except OSError as error:
            hit = error.errno == errno.ENOSPC
        checks["inodes_enforced"] = hit and count <= 127 and os.statvfs("/work").f_favail == 0
    elif case == "pids":
        release = threading.Event()
        threads = []
        hit = False
        threading.stack_size(64 * 1024)
        try:
            for _ in range(20):
                thread = threading.Thread(target=release.wait, args=(3,))
                try:
                    thread.start()
                except RuntimeError:
                    hit = True
                    break
                threads.append(thread)
        finally:
            release.set()
            for thread in threads:
                thread.join(timeout=1)
        checks["thread_creation_bounded"] = hit and len(threads) < 16
    elif case == "output":
        os.write(1, b"x" * 131072)
        return 3
    elif case == "timeout":
        time.sleep(6)
        return 3
    elif case == "memory":
        time.sleep(0.05)  # Let the trusted controller record sandbox-start evidence.
        allocation = bytearray(80 * 1024 * 1024)
        return 3 if allocation else 3
    elif case == "descendants":
        child = subprocess.Popen(["/usr/bin/python3", "-I", "-B", "-c",
            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(6)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        checks["child_started"] = child.stdout.readline().strip() == "ready"
        child.stdout.close()
    else:
        raise ValueError("Unknown fixed case")
    print(json.dumps({"case": case, "checks": checks, "passed": bool(checks) and all(checks.values())}), flush=True)
    return 0 if all(checks.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
