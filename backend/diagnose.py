import re
import json
import subprocess
import sys
from pathlib import Path


if len(sys.argv) != 2:
    print("用法：python3 backend/diagnose.py <C文件路径>")
    sys.exit(2)

source = Path(sys.argv[1]).resolve()

if not source.is_file():
    print(f"找不到文件：{source}")
    sys.exit(2)

try:
    result = subprocess.run(
        ["clang", "-fsyntax-only", "-fno-color-diagnostics", str(source)],
        capture_output=True,
        text=True,
        timeout=5,
    )
except FileNotFoundError:
    print("找不到 clang，请检查是否已安装。")
    sys.exit(2)
except subprocess.TimeoutExpired:
    print("检查超时：超过了 5 秒。")
    sys.exit(2)

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
        })

report = {
    "file": str(source),
    "success": result.returncode == 0,
    "diagnostics": diagnostics,
    "compiler_output": result.stderr,
}

print(json.dumps(report, ensure_ascii=False, indent=2))
sys.exit(0 if report["success"] else 1)
