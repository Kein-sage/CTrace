"""Read-only inventory for the proposed runtime backend; never runs C code."""

import json
import os
from pathlib import Path
import platform
import shutil
import sys

from runtime_cgroup import inspect_current_cgroup, read_observation


TOOLS = ("clang", "bwrap", "unshare", "prlimit", "timeout")
CGROUP_ROOT = Path("/sys/fs/cgroup")


def inspect_environment():
    """Observe prerequisites only. Presence and access checks are not validation."""
    system = platform.system()
    linux = system == "Linux"
    process_cgroup = read_observation(Path("/proc/self/cgroup")) if linux else {
        "value": None, "error": "UnsupportedPlatform",
    }
    return {
        "system": system,
        "effective_uid": os.geteuid() if linux else None,
        "tools": {name: shutil.which(name) for name in TOOLS},
        "cgroup_v2_controllers": read_observation(
            CGROUP_ROOT / "cgroup.controllers"
        ) if linux else {"value": None, "error": "UnsupportedPlatform"},
        "process_cgroup": process_cgroup,
        "current_cgroup": inspect_current_cgroup(process_cgroup)
        if linux else {"error": "UnsupportedPlatform"},
    }


def build_report(observations):
    blockers = []

    def block(code, message):
        blockers.append({"code": code, "message": message})

    if observations["system"] != "Linux":
        block("SANDBOX_UNAVAILABLE", "当前候选隔离方案仅面向 Linux。")
    elif observations["effective_uid"] == 0:
        block("SANDBOX_UNAVAILABLE", "运行时检查要求非 root 执行环境。")

    if not observations["tools"]["bwrap"]:
        block("SANDBOX_UNAVAILABLE", "未找到 bwrap；不会尝试安装或直接执行 C 程序。")
    if not observations["tools"]["clang"]:
        block("COMPILER_NOT_FOUND", "未找到 clang；尚未验证 UBSan 工具链。")

    current = observations["current_cgroup"]
    if current["error"] is not None:
        block("LIMITS_UNAVAILABLE", "无法定位或读取当前 cgroup v2，资源控制能力未知。")
    else:
        controllers = current["controllers"]["value"]
        if controllers is None or not {"memory", "pids"}.issubset(controllers.split()):
            block("LIMITS_UNAVAILABLE", "当前 cgroup 未观察到 memory 和 pids 控制器。")
        if (current["mount_read_only"] or not current["directory_writable"]
                or not all(current["control_writable"].values())):
            block("LIMITS_UNAVAILABLE", "当前 cgroup 的挂载或权限观察不满足子树管理前提。")

    # Inventory must never become permission to execute, even on a capable host.
    block("ISOLATION_NOT_VERIFIED", "尚无通过验收的隔离后端，禁止编译和运行受检源码。")
    block("LIMITS_NOT_VERIFIED", "整任务资源限额、工作区配额和后代进程清理尚未验收。")
    return {
        "schema_version": 1,
        "kind": "runtime_environment_inventory",
        "ready": False,
        "observations": observations,
        "blockers": blockers,
    }


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(json.dumps({"error": {
            "code": "INVALID_ARGUMENTS",
            "message": "用法：python3 backend/runtime_preflight.py（不接受源码或其他参数）",
        }}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(build_report(inspect_environment()), ensure_ascii=False, indent=2))
    return 2


if __name__ == "__main__":
    sys.exit(main())
