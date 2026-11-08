import io
import runpy
from unittest.mock import patch
import tempfile
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def check_example(filename):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "backend" / "diagnose.py"),
            str(ROOT / "examples" / filename),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )


class DiagnoseTests(unittest.TestCase):
    def test_correct_code(self):
        result = check_example("correct.c")
        self.assertEqual(result.returncode, 0)

        report = json.loads(result.stdout)
        self.assertIsNone(report["error"])
        self.assertTrue(report["success"])
        self.assertEqual(report["diagnostics"], [])

    def test_missing_semicolon(self):
        result = check_example("missing_semicolon.c")
        self.assertEqual(result.returncode, 1)

        report = json.loads(result.stdout)
        self.assertIsNone(report["error"])
        self.assertFalse(report["success"])
        self.assertEqual(len(report["diagnostics"]), 1)

        diagnostic = report["diagnostics"][0]
        self.assertEqual(diagnostic["line"], 1)
        self.assertEqual(diagnostic["column"], 26)
        self.assertEqual(diagnostic["severity"], "error")
        self.assertIn("expected ';'", diagnostic["message"])
        self.assertEqual(
            diagnostic["explanation"],
            "return 语句末尾缺少分号，请在 return 表达式后添加英文分号 ;。",
        )

    def test_undeclared_variable(self):
        result = check_example("undeclared_variable.c")
        self.assertEqual(result.returncode, 1)

        report = json.loads(result.stdout)
        self.assertFalse(report["success"])
        self.assertEqual(len(report["diagnostics"]), 1)

        diagnostic = report["diagnostics"][0]
        self.assertEqual(diagnostic["line"], 3)
        self.assertEqual(diagnostic["column"], 12)
        self.assertEqual(diagnostic["severity"], "error")
        self.assertEqual(
            diagnostic["message"],
            "use of undeclared identifier 'score'",
        )
        self.assertEqual(
            diagnostic["explanation"],
            "尚未声明标识符：score。请先声明再使用，并检查名称拼写。",
        )

    def test_missing_brace(self):
        result = check_example("missing_brace.c")
        self.assertEqual(result.returncode, 1)

        report = json.loads(result.stdout)
        self.assertFalse(report["success"])

        errors = [
            item for item in report["diagnostics"]
            if item["severity"] == "error"
        ]
        notes = [
            item for item in report["diagnostics"]
            if item["severity"] == "note"
        ]

        self.assertEqual(len(errors), 1)
        self.assertEqual(len(notes), 1)

        error = errors[0]
        self.assertEqual(error["line"], 3)
        self.assertEqual(error["column"], 14)
        self.assertEqual(error["message"], "expected '}'")
        self.assertEqual(
            error["explanation"],
            "缺少右花括号 }，请检查代码块是否完整闭合。",
        )

        note = notes[0]
        self.assertEqual(note["line"], 2)
        self.assertEqual(note["column"], 1)
        self.assertEqual(note["message"], "to match this '{'")
        self.assertEqual(
            note["explanation"],
            "此处的左花括号需要与右花括号配对。",
        )

    def test_unterminated_string(self):
        result = check_example("unterminated_string.c")
        self.assertEqual(result.returncode, 1)

        report = json.loads(result.stdout)
        self.assertFalse(report["success"])

        warnings = [
            item for item in report["diagnostics"]
            if item["severity"] == "warning"
        ]
        errors = [
            item for item in report["diagnostics"]
            if item["severity"] == "error"
        ]

        self.assertEqual(len(warnings), 1)
        self.assertEqual(len(errors), 1)

        warning = warnings[0]
        self.assertEqual(warning["line"], 3)
        self.assertEqual(warning["column"], 27)
        self.assertIn("missing terminating", warning["message"])
        self.assertEqual(
            warning["explanation"],
            "字符串缺少结束的双引号，请检查英文双引号是否成对。",
        )

        error = errors[0]
        self.assertEqual(error["message"], "expected expression")
        self.assertEqual(
            error["explanation"],
            "此处需要一个有效表达式，请检查附近代码，并优先处理前面的诊断。",
        )

    def test_wrong_argument_count(self):
        result = check_example("wrong_argument_count.c")
        self.assertEqual(result.returncode, 1)

        report = json.loads(result.stdout)
        self.assertFalse(report["success"])

        errors = [
            item for item in report["diagnostics"]
            if item["severity"] == "error"
        ]
        notes = [
            item for item in report["diagnostics"]
            if item["severity"] == "note"
        ]

        self.assertEqual(len(errors), 1)
        self.assertEqual(len(notes), 1)

        error = errors[0]
        self.assertEqual(error["line"], 8)
        self.assertEqual(error["column"], 17)
        self.assertEqual(
            error["message"],
            "too few arguments to function call, expected 2, have 1",
        )
        self.assertEqual(
            error["explanation"],
            "函数调用参数不足：需要2个，实际提供1个。请补齐参数。",
        )

        note = notes[0]
        self.assertEqual(note["line"], 1)
        self.assertEqual(note["column"], 5)
        self.assertEqual(note["message"], "'add' declared here")
        self.assertEqual(
            note["explanation"],
            "标识符add在此处声明，请核对其声明。",
        )

    def test_missing_arguments(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "backend" / "diagnose.py")],
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")

        report = json.loads(result.stdout)
        self.assertFalse(report["success"])
        self.assertIsNone(report["file"])
        self.assertEqual(report["diagnostics"], [])
        self.assertEqual(report["compiler_output"], "")
        self.assertEqual(report["error"]["code"], "INVALID_ARGUMENTS")

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_file = Path(directory) / "missing.c"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "backend" / "diagnose.py"),
                    str(missing_file),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, "")

            report = json.loads(result.stdout)
            self.assertFalse(report["success"])
            self.assertEqual(report["file"], str(missing_file.resolve()))
            self.assertEqual(report["diagnostics"], [])
            self.assertEqual(report["error"]["code"], "FILE_NOT_FOUND")

    def check_source_size(self, size):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "size_check.c"
            code = b"int main(void) { return 0; }\n"
            source.write_bytes(code + b" " * (size - len(code)))

            return subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "backend" / "diagnose.py"),
                    str(source),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )

    def test_source_at_size_limit(self):
        result = self.check_source_size(65536)
        self.assertEqual(result.returncode, 0)

        report = json.loads(result.stdout)
        self.assertTrue(report["success"])
        self.assertIsNone(report["error"])

    def test_source_over_size_limit(self):
        result = self.check_source_size(65537)
        self.assertEqual(result.returncode, 2)

        report = json.loads(result.stdout)
        self.assertFalse(report["success"])
        self.assertEqual(report["diagnostics"], [])
        self.assertEqual(report["error"]["code"], "SOURCE_TOO_LARGE")



class CompilerFailureTests(unittest.TestCase):
    def check_compiler_failure(self, failure, expected_code):
        script = ROOT / "backend" / "diagnose.py"
        source = ROOT / "examples" / "correct.c"

        with (
            patch("sys.argv", [str(script), str(source)]),
            patch("subprocess.run", side_effect=failure) as compiler,
            patch("sys.stdout", new_callable=io.StringIO) as output,
            self.assertRaises(SystemExit) as raised,
        ):
            runpy.run_path(str(script), run_name="__main__")

        compiler.assert_called_once()
        self.assertEqual(raised.exception.code, 2)

        report = json.loads(output.getvalue())
        self.assertFalse(report["success"])
        self.assertEqual(report["file"], str(source.resolve()))
        self.assertEqual(report["diagnostics"], [])
        self.assertEqual(report["compiler_output"], "")
        self.assertEqual(report["error"]["code"], expected_code)
        self.assertTrue(report["error"]["message"])

    def test_compiler_not_found(self):
        self.check_compiler_failure(
            FileNotFoundError("模拟找不到编译器"),
            "COMPILER_NOT_FOUND",
        )

    def test_compiler_timeout(self):
        self.check_compiler_failure(
            subprocess.TimeoutExpired(cmd=["clang"], timeout=5),
            "COMPILER_TIMEOUT",
        )

    def test_compiler_start_failed(self):
        self.check_compiler_failure(
            PermissionError("模拟没有执行权限"),
            "COMPILER_START_FAILED",
        )
