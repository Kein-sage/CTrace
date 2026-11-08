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
        self.assertTrue(report["success"])
        self.assertEqual(report["diagnostics"], [])

    def test_missing_semicolon(self):
        result = check_example("missing_semicolon.c")
        self.assertEqual(result.returncode, 1)

        report = json.loads(result.stdout)
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
