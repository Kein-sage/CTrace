import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("clang"), "Clang is required for real compiler checks")
class DiagnosticCorpusTests(unittest.TestCase):
    def check_source(self, source):
        result = subprocess.run(
            [sys.executable, str(ROOT / "backend" / "diagnose.py"), str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        self.assertEqual(result.stderr, "")
        report = json.loads(result.stdout)
        self.assertIsNone(report["error"])
        return result.returncode, report

    def test_identifier_spelling_suggestion_and_declaration(self):
        code, report = self.check_source(ROOT / "examples" / "typo_identifier.c")
        self.assertEqual(code, 1)
        self.assertFalse(report["success"])
        self.assertFalse(report["diagnostics_truncated"])
        errors = [
            item for item in report["diagnostics"] if item["severity"] == "error"
        ]
        self.assertEqual(len(errors), 1)
        error = errors[0]
        self.assertIn("did you mean 'score'?", error["message"])
        self.assertEqual((error["line"], error["column"]), (3, 12))
        self.assertIn("scroe", error["explanation"])
        self.assertIn("score", error["explanation"])
        notes = [
            item for item in report["diagnostics"] if item["severity"] == "note"
        ]
        self.assertTrue(any(
            item["line"] == 2 and "score" in item["message"]
            and item["explanation"] is not None for item in notes
        ))

    def test_missing_parenthesis_and_matching_note(self):
        code, report = self.check_source(ROOT / "examples" / "missing_parenthesis.c")
        self.assertEqual(code, 1)
        self.assertFalse(report["success"])
        errors = [
            item for item in report["diagnostics"] if item["severity"] == "error"
        ]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["message"], "expected ')'")
        self.assertEqual((errors[0]["line"], errors[0]["column"]), (6, 26))
        self.assertIn("圆括号", errors[0]["explanation"])
        notes = [
            item for item in report["diagnostics"]
            if item["severity"] == "note" and item["message"] == "to match this '('"
        ]
        self.assertEqual(len(notes), 1)
        self.assertEqual((notes[0]["line"], notes[0]["column"]), (6, 24))
        self.assertIn("圆括号", notes[0]["explanation"])

    def test_unexplained_format_warning_is_preserved_without_failing_check(self):
        code, report = self.check_source(ROOT / "examples" / "format_mismatch.c")
        self.assertEqual(code, 0)
        self.assertTrue(report["success"])
        self.assertFalse(report["diagnostics_truncated"])
        self.assertFalse(any(
            item["severity"] in {"error", "fatal error"}
            for item in report["diagnostics"]
        ))
        warnings = [
            item for item in report["diagnostics"]
            if item["severity"] == "warning"
            and "format specifies type" in item["message"]
        ]
        self.assertEqual(len(warnings), 1)
        self.assertIsNone(warnings[0]["explanation"])
        self.assertIn(warnings[0]["message"], report["compiler_output"])

    def test_real_error_limit_reports_incomplete_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "many_errors.c"
            source.write_text(
                "int main(void) {\n"
                + "".join(f"    missing_{index:02d};\n" for index in range(30))
                + "    return 0;\n}\n",
                encoding="utf-8",
            )
            code, report = self.check_source(source)
        self.assertEqual(code, 1)
        self.assertFalse(report["success"])
        self.assertTrue(report["diagnostics_truncated"])
        errors = [
            item for item in report["diagnostics"] if item["severity"] == "error"
        ]
        self.assertGreater(len(errors), 0)
        self.assertLess(len(errors), 30)
        fatal_errors = [
            item for item in report["diagnostics"]
            if item["severity"] == "fatal error"
            and item["message"].startswith("too many errors emitted, stopping now")
        ]
        self.assertEqual(len(fatal_errors), 1)
        for field in ("file", "line", "column"):
            self.assertIsNone(fatal_errors[0][field])
        self.assertIn(fatal_errors[0]["message"], report["compiler_output"])

    def test_header_error_keeps_header_location(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            header = folder / "bad.h"
            header.write_text("int header_value = ;\n", encoding="utf-8")
            source = folder / "main.c"
            source.write_text(
                '#include "bad.h"\nint main(void) { return 0; }\n',
                encoding="utf-8",
            )
            code, report = self.check_source(source)
            self.assertEqual(report["file"], str(source.resolve()))
            errors = [
                item for item in report["diagnostics"] if item["severity"] == "error"
            ]
            self.assertEqual(len(errors), 1)
            self.assertEqual(Path(errors[0]["file"]).resolve(), header.resolve())
            self.assertEqual(errors[0]["line"], 1)
        self.assertEqual(code, 1)
        self.assertFalse(report["success"])
        self.assertFalse(report["diagnostics_truncated"])

    def test_unicode_source_column_counts_utf8_bytes(self):
        source_line = "int main(void) { /* 中文 */ return missing_value; }"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "unicode.c"
            source.write_text(source_line + "\n", encoding="utf-8")
            code, report = self.check_source(source)
        self.assertEqual(code, 1)
        errors = [
            item for item in report["diagnostics"]
            if item["severity"] == "error" and "missing_value" in item["message"]
        ]
        self.assertEqual(len(errors), 1)
        expected_column = source_line.encode("utf-8").index(b"missing_value") + 1
        self.assertGreater(expected_column, source_line.index("missing_value") + 1)
        self.assertEqual((errors[0]["line"], errors[0]["column"]), (1, expected_column))
