import re
import json
import subprocess
import sys
from pathlib import Path

from compiler_runner import CompilerOutputLimitExceeded, run_compiler

MAX_SOURCE_BYTES = 64 * 1024
MAX_COMPILER_OUTPUT_BYTES = 1024 * 1024

EXPLANATIONS = {
    "expected ';' after return statement":
        "return 语句末尾缺少分号，请在 return 表达式后添加英文分号 ;。",
    "expected '}'":
        "缺少右花括号 }，请检查代码块是否完整闭合。",
    "to match this '{'":
        "此处的左花括号需要与右花括号配对。",
    "missing terminating '\"' character":
        "字符串缺少结束的双引号，请检查英文双引号是否成对。",
    "expected expression":
        "此处需要一个有效表达式，请检查附近代码，并优先处理前面的诊断。",
}

def explain_diagnostic(message):
    message = re.sub(r" \[-W[^\]]+\]$", "", message)

    if message in EXPLANATIONS:
        return EXPLANATIONS[message]

    if message in {
        "expected ';' at end of declaration",
        "expected ';' after expression",
    }:
        return (
            "这条语句缺少英文分号 ;。"
            "请检查报错位置及上一条语句的结尾。"
        )

    match = re.fullmatch(
        r"use of undeclared identifier '([^']+)'",
        message,
    )

    if match:
        identifier = match.group(1)
        return f"尚未声明标识符：{identifier}。请先声明再使用，并检查名称拼写。"

    match = re.fullmatch(
        r"too few arguments to function call, expected (\d+), have (\d+)",
        message,
    )

    if match:
        expected, actual = match.groups()
        return (
            f"函数调用参数不足：需要{expected}个，实际提供{actual}个。"
            "请补齐参数。"
        )

    match = re.fullmatch(
        r"too many arguments to function call, expected (\d+), have (\d+)",
        message,
    )

    if match:
        expected, actual = match.groups()
        return (
            f"函数调用参数过多：需要{expected}个，实际提供{actual}个。"
            "请删除多余参数，并核对函数声明。"
        )


    match = re.fullmatch(r"'([^']+)' declared here", message)

    if match:
        identifier = match.group(1)
        return f"标识符{identifier}在此处声明，请核对其声明。"

    return None

def exit_with_error(code, message, file_path=None):
    report = {
        "file": file_path,
        "success": False,
        "diagnostics": [],
        "compiler_output": "",
        "error": {
            "code": code,
            "message": message,
        },
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(2)

if len(sys.argv) != 2:
    exit_with_error(
        "INVALID_ARGUMENTS",
        "用法：python3 backend/diagnose.py <C文件路径>",
    )

source = Path(sys.argv[1]).resolve()

if not source.is_file():
    exit_with_error(
        "FILE_NOT_FOUND",
        "输入文件不存在或不是普通文件。",
        str(source),
    )

try:
    source_size = source.stat().st_size
except OSError:
    exit_with_error(
        "FILE_ACCESS_FAILED",
        "无法读取输入文件信息，请检查路径和权限。",
        str(source),
    )

if source_size > MAX_SOURCE_BYTES:
    exit_with_error(
        "SOURCE_TOO_LARGE",
        f"输入文件过大，最大允许{MAX_SOURCE_BYTES}字节。",
        str(source),
    )

try:
    result = run_compiler(
        ["clang", "-fsyntax-only", "-fno-color-diagnostics", str(source)],
        timeout=5,
        max_output_bytes=MAX_COMPILER_OUTPUT_BYTES,
    )
except CompilerOutputLimitExceeded:
    exit_with_error(
        "COMPILER_OUTPUT_TOO_LARGE",
        f"编译器输出过大，最大允许{MAX_COMPILER_OUTPUT_BYTES}字节。",
        str(source),
    )
except FileNotFoundError:
    exit_with_error(
        "COMPILER_NOT_FOUND",
        "找不到 clang，请检查是否已安装。",
        str(source),
    )
except subprocess.TimeoutExpired:
    exit_with_error(
        "COMPILER_TIMEOUT",
        "检查超时：超过了5秒。",
        str(source),
    )
except OSError:
    exit_with_error(
        "COMPILER_START_FAILED",
        "编译器运行异常，请检查程序路径、执行权限和系统资源。",
        str(source),
    )

diagnostics = []

pattern = re.compile(
    r"^(.*):(\d+):(\d+): (fatal error|error|warning|note): (.*)$"
)

for output_line in result.stderr.splitlines():
    match = pattern.match(output_line)

    if match:
        file_path, line, column, severity, message = match.groups()

        diagnostics.append({
            "file": file_path,
            "line": int(line),
            "column": int(column),
            "severity": severity,
            "message": message,
            "explanation": explain_diagnostic(message),
        })

report = {
    "file": str(source),
    "success": result.returncode == 0,
    "diagnostics": diagnostics,
    "compiler_output": result.stderr,
    "error": None,
}

print(json.dumps(report, ensure_ascii=False, indent=2))
sys.exit(0 if report["success"] else 1)
