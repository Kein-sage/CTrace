"""Fixed, trusted systemd resource probes. This is not a C execution sandbox."""

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid


CASES = ("snapshot", "pids", "memory", "timeout", "descendants")
LIMITS = {"memory.max": "67108864", "memory.swap.max": "0", "pids.max": "16"}
UNIT_PATTERN = r"ctrace-resource-probe-[0-9a-f]{32}\.service"


def build_command(case, unit):
    if case not in CASES or not re.fullmatch(UNIT_PATTERN, unit):
        raise ValueError("Invalid fixed probe")
    return [
        "/usr/bin/systemd-run", "--user", "--wait", "--pipe", "--quiet",
        "--service-type=exec", "--unit=" + unit,
        "--property=MemoryMax=67108864", "--property=MemorySwapMax=0",
        "--property=TasksMax=16", "--property=RuntimeMaxSec=5s",
        "--property=TimeoutStopSec=2s", "--property=KillMode=control-group",
        "--property=SendSIGKILL=yes", "--property=OOMPolicy=kill",
        "--property=CollectMode=inactive",
        "--property=WorkingDirectory=" + str(Path(__file__).resolve().parent.parent),
        "--", str(Path(sys.executable).resolve()), "-I", "-B",
        str(Path(__file__).resolve()), "--worker", case, unit,
    ]


def worker_snapshot(unit):
    if not re.fullmatch(UNIT_PATTERN, unit) or os.geteuid() == 0:
        raise ValueError("Probe requires a non-root transient user unit")
    entries = [line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines()
               if line.startswith("0::")]
    if len(entries) != 1 or not entries[0].startswith("/"):
        raise ValueError("Cannot locate probe cgroup")
    if any(part in {".", ".."} for part in entries[0].split("/")):
        raise ValueError("Invalid probe cgroup path")
    directory = Path("/sys/fs/cgroup") / entries[0].lstrip("/")
    if directory.name != unit:
        raise ValueError("Not in the expected transient unit")
    limits = {name: (directory / name).read_text().strip() for name in LIMITS}
    if limits != LIMITS:
        raise ValueError("Probe limits do not match; refusing workload")
    return {"uid": os.geteuid(), "cgroup": str(directory), "limits": limits}


def emit(frame):
    print(json.dumps(frame), flush=True)


def process_start(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def worker(case, unit):
    if case not in CASES:
        raise ValueError("Unknown probe case")
    snapshot = worker_snapshot(unit)
    emit(snapshot)
    directory = Path(snapshot["cgroup"])
    if case == "pids":
        def max_events():
            return dict(line.split() for line in
                        (directory / "pids.events").read_text().splitlines())["max"]
        before = int(max_events())
        release = threading.Event()
        threads = []
        hit = False
        threading.stack_size(64 * 1024)
        try:
            for _ in range(20):
                thread = threading.Thread(target=release.wait, args=(4,))
                try:
                    thread.start()
                except RuntimeError:
                    hit = True
                    break
                threads.append(thread)
            current = int((directory / "pids.current").read_text())
            delta = int(max_events()) - before
        finally:
            release.set()
            for thread in threads:
                thread.join(timeout=1)
        emit({"pids_limit_hit": hit, "pids_events_delta": delta,
              "pids_current": current, "threads_started": len(threads)})
    elif case == "memory":
        # One bounded allocation exceeds this unit's verified 64 MiB limit.
        allocation = bytearray(80 * 1024 * 1024)
        emit({"unexpected_allocation_success": len(allocation)})
    elif case == "timeout":
        time.sleep(6)
        emit({"unexpected_timeout_survival": True})
    elif case == "descendants":
        child = subprocess.Popen([
            str(Path(sys.executable).resolve()), "-I", "-B", "-c",
            "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "print('ready',flush=True); time.sleep(6)",
        ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True)
        if child.stdout.readline().strip() != "ready":
            raise RuntimeError("Descendant failed to start")
        child.stdout.close()
        emit({"child_pid": child.pid, "child_start": process_start(child.pid)})
    return 0


def manager_command(*args):
    return subprocess.run(
        ["/usr/bin/systemctl", "--user", *args], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=5,
    )


def unit_state(unit):
    result = manager_command("show", unit, "--property=LoadState",
                             "--property=ActiveState", "--property=Result",
                             "--property=ExecMainStatus", "--property=ControlGroup")
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if not values:
        raise RuntimeError("Cannot read transient unit state")
    return values


def evaluate(case, returncode, frames, state, cleanup):
    if (returncode is None or not cleanup or not frames
            or frames[0].get("limits") != LIMITS):
        return False
    if not isinstance(frames[0].get("uid"), int) or frames[0]["uid"] <= 0:
        return False
    if case == "snapshot":
        return returncode == 0 and len(frames) == 1
    if case == "pids":
        return (returncode == 0 and len(frames) == 2
                and frames[1].get("pids_limit_hit") is True
                and frames[1].get("pids_events_delta", 0) > 0
                and 0 < frames[1].get("pids_current", 0) <= 16)
    if case == "memory":
        return returncode != 0 and len(frames) == 1 and state.get("Result") == "oom-kill"
    if case == "timeout":
        return returncode != 0 and len(frames) == 1 and state.get("Result") == "timeout"
    if case == "descendants":
        return (len(frames) == 2 and isinstance(frames[1].get("child_pid"), int)
                and frames[1]["child_pid"] > 1 and frames[1].get("child_start") is not None
                and process_start(frames[1]["child_pid"]) != frames[1]["child_start"])
    return False


def run_probe(case):
    unit = "ctrace-resource-probe-" + uuid.uuid4().hex + ".service"
    command = build_command(case, unit)
    process = None
    frames, state, errors = [], {}, []
    output = error_output = ""
    returncode = None
    cleanup = False
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        # Only fixed trusted workers write here; this is not a general output collector.
        output, error_output = process.communicate(timeout=12)
        returncode = process.returncode
        frames = [json.loads(line) for line in output.splitlines() if line.strip()]
        state = unit_state(unit)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        errors.append(type(error).__name__ + ": " + str(error))
    finally:
        try:
            manager_command("stop", unit)
        except (OSError, subprocess.TimeoutExpired) as error:
            errors.append("stop: " + type(error).__name__)
        finally:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    errors.append("client cleanup timeout")
        try:
            manager_command("reset-failed", unit)
            deadline = time.monotonic() + 3
            while True:
                final = unit_state(unit)
                unit_gone = (final.get("LoadState") == "not-found"
                             and final.get("ActiveState") == "inactive")
                cgroup_gone = False
                if frames:
                    directory = Path(frames[0].get("cgroup", ""))
                    cgroup_gone = (directory.name == unit
                                   and directory.is_relative_to("/sys/fs/cgroup")
                                   and not directory.exists())
                if unit_gone and cgroup_gone:
                    cleanup = True
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            errors.append("cleanup: " + type(error).__name__)
    passed = not errors and evaluate(case, returncode, frames, state, cleanup)
    return {"schema_version": 1, "kind": "trusted_resource_probe", "case": case,
            "passed": passed, "runtime_ready": False, "unit": unit,
            "client_exit_code": returncode, "frames": frames, "unit_result": state,
            "cleanup_confirmed": cleanup, "errors": errors,
            "stdout": output, "stderr": error_output}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 3 and args[0] == "--worker":
        try:
            return worker(args[1], args[2])
        except (OSError, ValueError, RuntimeError) as error:
            emit({"worker_error": str(error)})
            return 2
    if not args:
        args = ["--plan", "snapshot"]
    if len(args) != 2 or args[0] not in {"--plan", "--run"} or args[1] not in CASES:
        emit({"error": "Use --plan CASE or --run CASE; cases: " + ", ".join(CASES)})
        return 2
    if args[0] == "--plan":
        unit = "ctrace-resource-probe-" + uuid.uuid4().hex + ".service"
        emit({"kind": "trusted_resource_probe_plan", "executes": False,
              "requires_runtime_state_permission": True,
              "command": build_command(args[1], unit)})
        return 0
    if sys.platform != "linux" or os.geteuid() == 0:
        emit({"error": "Probes require non-root Linux with a systemd user manager"})
        return 2
    report = run_probe(args[1])
    emit(report)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
