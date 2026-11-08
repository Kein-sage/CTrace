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
