import io
import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from compiler_runner import CompilerOutputLimitExceeded, run_compiler


class CompilerRunnerTests(unittest.TestCase):
    def run_python(self, code, limit=64, timeout=3):
        return run_compiler(
            [sys.executable, "-c", code],
            timeout=timeout,
            max_output_bytes=limit,
        )

    def test_output_at_limit(self):
        result = self.run_python("import os; os.write(2, b'x' * 64)")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "x" * 64)

    def test_output_over_limit_stops_process(self):
        with self.assertRaises(CompilerOutputLimitExceeded):
            self.run_python(
                "import os, time; os.write(2, b'x' * 65); time.sleep(10)"
            )

    def test_combined_output_at_limit(self):
        result = self.run_python(
            "import os; os.write(1, b'a' * 32); os.write(2, b'b' * 32)"
        )
        self.assertEqual(result.stdout, "a" * 32)
        self.assertEqual(result.stderr, "b" * 32)

    def test_combined_output_over_limit(self):
        with self.assertRaises(CompilerOutputLimitExceeded):
            self.run_python(
                "import os; os.write(1, b'a' * 33); os.write(2, b'b' * 32)"
            )

    def test_stdout_and_stderr_remain_separate(self):
        result = self.run_python(
            "import os; os.write(1, b'out'); os.write(2, b'err'); "
            "raise SystemExit(7)"
        )
        self.assertEqual(result.stdout, "out")
        self.assertEqual(result.stderr, "err")
        self.assertEqual(result.returncode, 7)

    def test_real_process_timeout(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_python("import time; time.sleep(10)", timeout=0.2)

    def test_invalid_utf8_is_replaced(self):
        result = self.run_python(r"import os; os.write(2, b'\xff')")
        self.assertEqual(result.stderr, "\ufffd")


class CompilerOutputReportTests(unittest.TestCase):
    def test_output_limit_returns_json_error(self):
        script = Path(__file__).with_name("diagnose.py")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "correct.c"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            with (
                patch("sys.argv", [str(script), str(source)]),
                patch(
                    "compiler_runner.run_compiler",
                    side_effect=CompilerOutputLimitExceeded(),
                ) as compiler,
                patch("sys.stdout", new_callable=io.StringIO) as output,
                self.assertRaises(SystemExit) as raised,
            ):
                runpy.run_path(str(script), run_name="__main__")

        compiler.assert_called_once()
        self.assertEqual(raised.exception.code, 2)
        report = json.loads(output.getvalue())
        self.assertFalse(report["success"])
        self.assertEqual(report["diagnostics"], [])
        self.assertEqual(report["compiler_output"], "")
        self.assertEqual(report["error"]["code"], "COMPILER_OUTPUT_TOO_LARGE")
        self.assertEqual(report["file"], str(source.resolve()))
