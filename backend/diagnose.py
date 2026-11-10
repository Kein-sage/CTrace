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
    "expected ')'":
        "缺少右圆括号 )，请检查函数调用或表达式中的圆括号是否成对。",
    "to match this '('":
        "此处的左圆括号需要与右圆括号配对。",
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
        r"format specifies type '([^']+)' but the argument has type '([^']+)'",
        message,
    )

    if match:
        expected, actual = match.groups()
        return (
            f"格式说明符要求的参数类型是 {expected}，"
            f"实际提供的是 {actual}。"
            "请核对格式说明符与对应参数，使类型一致。"
        )

    match = re.fullmatch(
        r"use of undeclared identifier '([^']+)'"
        r"(?:; did you mean '([^']+)'\?)?",
        message,
    )

    if match:
        identifier, suggestion = match.groups()
        if suggestion:
            return (
                f"尚未声明标识符：{identifier}。"
                f"你是否想使用 {suggestion}？请核对名称拼写及其声明。"
            )
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

    match = re.fullmatch(r"'([^']+)' file not found", message)

    if match:
        header_name = match.group(1)
        return (
            f"找不到头文件：{header_name}。"
            "请检查头文件名称和包含路径；"
            "若来自第三方库，请确认已安装相应开发文件。"
        )

    match = re.fullmatch(
        r"call to undeclared function '([^']+)'; ISO C99 and later "
        r"do not support implicit function declarations",
        message,
    )

    if match:
        function_name = match.group(1)
        return (
            f"调用了尚未声明的函数：{function_name}。"
            "请在调用前声明或定义函数，并检查函数名拼写；"
            "若使用库函数，请包含对应头文件。"
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
        "diagnostics_truncated": False,
        "compiler_output": "",
        "error": {
            "code": code,
            "message": message,
        },
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(2)

def parse_diagnostics(output):
    diagnostics = []
    located = re.compile(
        r"^(.*):(\d+):(\d+): (fatal error|error|warning|note): (.*)$"
    )
    unlocated = re.compile(
        r"^(?:clang(?:-\d+)?(?:\.exe)?: )?"
        r"(fatal error|error|warning|note): (.*)$"
    )

    for output_line in output.splitlines():
        match = located.match(output_line)
        if match:
            file_path, line, column, severity, message = match.groups()
            line, column = int(line), int(column)
        else:
            match = unlocated.match(output_line)
            if not match:
                continue
            severity, message = match.groups()
            file_path = line = column = None

        diagnostics.append({
            "file": file_path,
            "line": line,
            "column": column,
            "severity": severity,
            "message": message,
            "explanation": explain_diagnostic(message),
        })

    return diagnostics


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv

    if len(args) != 1:
        exit_with_error(
            "INVALID_ARGUMENTS",
            "用法：python3 backend/diagnose.py <C文件路径>",
        )

    source = Path(args[0])
    try:
        source = source.resolve()
        if not source.is_file():
            exit_with_error(
                "FILE_NOT_FOUND",
                "输入文件不存在或不是普通文件。",
                str(source),
            )
        source_size = source.stat().st_size
    except (OSError, RuntimeError):
        exit_with_error(
            "FILE_ACCESS_FAILED",
            "无法访问输入文件，请检查路径、权限及符号链接。",
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
            [
                "clang", "-x", "c", "-fsyntax-only",
                "-fno-color-diagnostics", str(source),
            ],
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

    diagnostics = parse_diagnostics(result.stderr)

    report = {
        "file": str(source),
        "success": result.returncode == 0,
        "diagnostics": diagnostics,
        "diagnostics_truncated": any(
            item["severity"] == "fatal error"
            and item["message"].startswith("too many errors emitted, stopping now")
            for item in diagnostics
        ),
        "compiler_output": result.stderr,
        "error": None,
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
