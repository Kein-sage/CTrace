"""Fixed isolation acceptance probes, not a general C execution entry point."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

# The supervisor is invoked with Python -I; imports are from this trusted project directory.
BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
from compiler_runner import CompilerOutputLimitExceeded, run_compiler
from runtime_component import ROOT, verify_binary
from runtime_resource_probe import LIMITS, manager_command, unit_state


CASES = ("files", "network", "environment", "namespaces", "space", "inodes",
         "pids", "output", "timeout", "memory", "descendants")
ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}

REQUIRED_CHECKS = {
    "files": {"namespace_and_privilege_baseline", "host_paths_hidden", "readonly_marker", "root_readonly", "toolchain_readonly", "clang_and_python_visible", "host_process_hidden", "private_work_writable"},
    "network": {"namespace_and_privilege_baseline", "only_loopback", "no_ipv4_route", "ipv4_unreachable", "ipv6_unreachable", "host_abstract_socket_hidden", "host_service_sockets_hidden"},
    "environment": {"namespace_and_privilege_baseline", "environment_allowlist", "only_standard_fds", "stdin_eof"},
    "namespaces": {"namespace_and_privilege_baseline", "nested_userns_denied", "mount_namespace_denied"},
    "space": {"namespace_and_privilege_baseline", "space_enforced"},
    "inodes": {"namespace_and_privilege_baseline", "inodes_enforced"},
    "pids": {"namespace_and_privilege_baseline", "thread_creation_bounded"},
    "descendants": {"namespace_and_privilege_baseline", "child_started"},
}



def emit(frame):
    print(json.dumps(frame), flush=True)


def check_job(job, unit):
    expected = ROOT / "tmp" / unit
    if (job != expected or not unit.startswith("ctrace-isolation-")
            or len(unit) != len("ctrace-isolation-") + 32 + len(".service")
            or not unit.endswith(".service")):
        raise ValueError("Invalid private job path")
    token = unit[len("ctrace-isolation-"):-len(".service")]
    if any(character not in "0123456789abcdef" for character in token):
        raise ValueError("Invalid unit token")
    if not job.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Job path leaves the project")
    if any(character in str(job) for character in "%: \t\n"):
        raise ValueError("Unsupported path for fixed systemd policy")


def sandbox_command(case, job):
    if case not in CASES:
        raise ValueError("Unknown fixed isolation case")
    command = [str(verify_binary()), "--unshare-all", "--unshare-user", "--disable-userns",
               "--assert-userns-disabled", "--die-with-parent", "--new-session",
               "--cap-drop", "ALL", "--hostname", "ctrace-probe", "--clearenv"]
    for key, value in {**ENVIRONMENT, "HOME": "/nonexistent", "TMPDIR": "/work", "PWD": "/work"}.items():
        command.extend(["--setenv", key, value])
    mounts = [
        (Path("/usr/bin/python3").resolve(), "/usr/bin/python3"),
        (Path("/usr/bin/clang").resolve(), "/usr/bin/clang"),
        (Path("/usr/bin/ld").resolve(), "/usr/bin/ld"),
        (Path("/usr/lib/python3.12"), "/usr/lib/python3.12"),
        (Path("/usr/lib/x86_64-linux-gnu"), "/usr/lib/x86_64-linux-gnu"),
        (Path("/usr/lib/llvm-18/lib"), "/usr/lib/llvm-18/lib"),
        (Path("/usr/lib/gcc/x86_64-linux-gnu/13"), "/usr/lib/gcc/x86_64-linux-gnu/13"),
        (Path("/usr/include"), "/usr/include"),
        (BACKEND / "runtime_isolation_worker.py", "/probe.py"),
        (job / "config.json", "/config.json"),
        (job / "outside-marker", "/readonly-marker"),
    ]
    for source, target in mounts:
        command.extend(["--ro-bind", str(source), target])
    command.extend([
        "--symlink", "usr/lib", "/lib", "--dir", "/usr/lib64",
        "--symlink", "../lib/x86_64-linux-gnu/ld-linux-x86-64.so.2", "/usr/lib64/ld-linux-x86-64.so.2",
        "--symlink", "usr/lib64", "/lib64", "--symlink", "usr/bin", "/bin",
        "--proc", "/proc", "--remount-ro", "/proc", "--dir", "/dev",
        "--dev-bind", "/dev/null", "/dev/null", "--bind", str(job / "work"), "/work",
        "--symlink", "/work", "/tmp", "--remount-ro", "/", "--chdir", "/work",
        "--", "/usr/bin/python3", "-I", "-B", "/probe.py", case,
    ])
    return command


def service_command(case, job, unit):
    check_job(job, unit)
    if case not in CASES:
        raise ValueError("Unknown fixed isolation case")
    return [
        "/usr/bin/systemd-run", "--user", "--wait", "--pipe", "--quiet",
        "--service-type=exec", "--unit=" + unit,
        "--property=MemoryMax=67108864", "--property=MemorySwapMax=0",
        "--property=TasksMax=16", "--property=RuntimeMaxSec=5s",
        "--property=TimeoutStopSec=2s", "--property=KillMode=control-group",
        "--property=OOMPolicy=kill", "--property=SendSIGKILL=yes",
        "--property=CollectMode=inactive", "--property=PrivateUsers=yes",
        "--property=NoNewPrivileges=yes", "--property=LimitCORE=0",
        "--property=TemporaryFileSystem=" + str(job / "work")
        + f":rw,size=8388608,nr_inodes=128,mode=0700,uid={os.geteuid()},gid={os.getegid()},nosuid,nodev",
        "--property=WorkingDirectory=" + str(ROOT),
        "--", str(Path(sys.executable).resolve()), "-I", "-B", str(Path(__file__).resolve()),
        "--supervisor", case, unit,
    ]


def supervisor(case, unit):
    job = ROOT / "tmp" / unit
    check_job(job, unit)
    if os.geteuid() == 0:
        raise ValueError("Non-root supervisor required")
    entries = [line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::")]
    if len(entries) != 1 or not entries[0].startswith("/") or ".." in entries[0].split("/"):
        raise ValueError("Cannot locate service cgroup")
    cgroup = Path("/sys/fs/cgroup") / entries[0].lstrip("/")
    if cgroup.name != unit:
        raise ValueError("Wrong service cgroup")
    limits = {name: (cgroup / name).read_text().strip() for name in LIMITS}
    quota = os.statvfs(job / "work")
    if limits != LIMITS or quota.f_blocks * quota.f_frsize != 8388608 or quota.f_files != 128:
        raise ValueError("Required limits unavailable; refusing sandbox launch")
    emit({"event": "limits", "limits": limits, "cgroup": str(cgroup), "quota_bytes": 8388608, "quota_inodes": 128})
    config = json.loads((job / "config.json").read_text())
    config.update(host_pid=os.getpid(), namespaces={name: os.readlink("/proc/self/ns/" + name) for name in ("mnt", "pid", "net", "user", "ipc", "uts", "cgroup")})
    (job / "config.json").write_text(json.dumps(config))
    before = int(dict(line.split() for line in (cgroup / "pids.events").read_text().splitlines())["max"])
    descriptor = os.open(job / "outside-marker", os.O_RDONLY)
    os.set_inheritable(descriptor, True)
    abstract = socket.socket(socket.AF_UNIX)
    abstract.bind(chr(0) + config["abstract_socket"])
    abstract.listen(1)
    environment = {**ENVIRONMENT, "CTRACE_TEST_SECRET": config["marker"]}
    try:
        try:
            result = run_compiler(sandbox_command(case, job), timeout=7,
                                  max_output_bytes=65536, stdin=subprocess.DEVNULL,
                                  env=environment, cwd=ROOT,
                                  on_output=lambda stream, chunk: emit({"event": "output", "stream": stream, "data": chunk.decode("utf-8", errors="replace")}))
            after = int(dict(line.split() for line in (cgroup / "pids.events").read_text().splitlines())["max"])
            emit({"event": "finished", "returncode": result.returncode, "pids_events_delta": after - before})
            return result.returncode
        except CompilerOutputLimitExceeded:
            emit({"event": "output_limit"})
            return 0 if case == "output" else 2
    finally:
        os.close(descriptor)
        abstract.close()


def evaluate(case, client_code, events, state, cleanup):
    if not cleanup or client_code is None or not events:
        return False
    first = events[0]
    if first.get("event") != "limits" or first.get("limits") != LIMITS or first.get("quota_bytes") != 8388608 or first.get("quota_inodes") != 128:
        return False
    output = "".join(item.get("data", "") for item in events if item.get("event") == "output" and item.get("stream") == "stdout")
    lines = output.splitlines()
    try:
        started = json.loads(lines[0])
    except (ValueError, IndexError):
        return False
    if started != {"event": "sandbox_started", "baseline": True, "case": case}:
        return False
    if case == "memory":
        return client_code != 0 and state.get("Result") == "oom-kill"
    if case == "timeout":
        return client_code != 0 and state.get("Result") == "timeout"
    if case == "output":
        return client_code == 0 and any(item.get("event") == "output_limit" for item in events)
    try:
        result = json.loads(lines[1])
    except (ValueError, IndexError):
        return False
    if (result.get("case") != case or result.get("passed") is not True
            or set(result.get("checks", {})) != REQUIRED_CHECKS.get(case)
            or not all(value is True for value in result["checks"].values())):
        return False
    if case == "descendants":
        return result["checks"].get("child_started") is True and state.get("Result") in {"timeout", "success"}
    if case == "pids" and not any(item.get("pids_events_delta", 0) > 0 for item in events):
        return False
    return client_code == 0 and any(item.get("event") == "finished" and item.get("returncode") == 0 for item in events)


def run_probe(case):
    verify_binary()  # Fail before creating a task if the component is missing or changed.
    unit = "ctrace-isolation-" + uuid.uuid4().hex + ".service"
    job = ROOT / "tmp" / unit
    command = service_command(case, job, unit)
    job.mkdir(parents=True)
    (job / "work").mkdir()
    marker = "test-marker-" + uuid.uuid4().hex
    (job / "outside-marker").write_text(marker)
    (job / "config.json").write_text(json.dumps({"marker": marker, "outside_marker": str(job / "outside-marker"), "abstract_socket": unit}))
    events, state, errors = [], {}, []
    client_code, cleanup = None, False
    try:
        result = run_compiler(command, timeout=12, max_output_bytes=131072,
                              stdin=subprocess.DEVNULL, env={**ENVIRONMENT,
                                  "XDG_RUNTIME_DIR": f"/run/user/{os.geteuid()}",
                                  "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.geteuid()}/bus"}, cwd=ROOT)
        client_code = result.returncode
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        state = unit_state(unit)
        if result.stderr:
            errors.append("client: " + result.stderr[:2048])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, CompilerOutputLimitExceeded) as error:
        errors.append(type(error).__name__ + ": " + str(error))
    finally:
        for operation in ("stop", "reset-failed"):
            try:
                manager_command(operation, unit)
            except (OSError, subprocess.SubprocessError) as error:
                errors.append(operation + ": " + type(error).__name__)
        try:
            deadline = time.monotonic() + 3
            while True:
                final = unit_state(unit)
                absent = final.get("LoadState") == "not-found" and final.get("ActiveState") == "inactive"
                cgroup_gone = bool(events and events[0].get("event") == "limits"
                                   and Path(events[0].get("cgroup", "")).name == unit
                                   and not Path(events[0]["cgroup"]).exists())
                if absent and cgroup_gone:
                    cleanup = True
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            errors.append("cleanup: " + str(error))
        host_unchanged = (job / "outside-marker").read_text() == marker and not list((job / "work").iterdir())
        cleanup = cleanup and host_unchanged
        if host_unchanged:
            for name in ("outside-marker", "config.json"):
                (job / name).unlink()
            (job / "work").rmdir()
            job.rmdir()
    passed = not errors and evaluate(case, client_code, events, state, cleanup)
    return {"kind": "trusted_isolation_probe", "case": case, "passed": passed,
            "runtime_ready": False, "unit": unit, "client_exit_code": client_code,
            "unit_result": state, "cleanup_confirmed": cleanup, "errors": errors,
            "events": events}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) == 3 and args[0] == "--supervisor":
            return supervisor(args[1], args[2])
        if not args:
            args = ["--plan", "files"]
        if len(args) != 2 or args[0] not in {"--plan", "--run"} or args[1] not in CASES:
            raise ValueError("Use --plan CASE or --run CASE; fixed cases: " + ", ".join(CASES))
        if args[0] == "--plan":
            unit = "ctrace-isolation-" + uuid.uuid4().hex + ".service"
            emit({"kind": "isolation_probe_plan", "executes": False,
                  "command": service_command(args[1], ROOT / "tmp" / unit, unit)})
            return 0
        report = run_probe(args[1])
        emit(report)
        return 0 if report["passed"] else 2
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        emit({"kind": "trusted_isolation_probe", "passed": False, "runtime_ready": False, "error": str(error)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
