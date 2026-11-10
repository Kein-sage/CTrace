import contextlib
import io
import json
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import diagnose


ROOT = Path(__file__).resolve().parent.parent


class ImportSafetyTests(unittest.TestCase):
    def test_import_does_not_run_cli(self):
        with (
            patch("sys.argv", ["diagnose.py"]),
            patch("compiler_runner.run_compiler") as compiler,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            module = runpy.run_path(
                str(ROOT / "backend" / "diagnose.py"),
                run_name="ctrace_import_probe",
            )
        compiler.assert_not_called()
        self.assertEqual(output.getvalue(), "")
        self.assertTrue(callable(module["main"]))


class ExplanationTests(unittest.TestCase):
    def test_spelling_suggestion(self):
        message = "use of undeclared identifier 'scroe'; did you mean 'score'?"
        explanation = diagnose.explain_diagnostic(message)
        self.assertIn("scroe", explanation)
        self.assertIn("score", explanation)
        self.assertIn("是否", explanation)

    def test_plain_undeclared_identifier_keeps_existing_explanation(self):
        self.assertEqual(
            diagnose.explain_diagnostic("use of undeclared identifier 'score'"),
            "尚未声明标识符：score。请先声明再使用，并检查名称拼写。",
        )

    def test_parenthesis_error_and_note(self):
        for message in ("expected ')'", "to match this '('"):
            with self.subTest(message=message):
                self.assertIn("圆括号", diagnose.explain_diagnostic(message))

    def test_warning_suffix_does_not_break_suggestion(self):
        message = (
            "use of undeclared identifier 'scroe'; did you mean 'score'? "
            "[-Wexample]"
        )
        diagnostic = diagnose.parse_diagnostics("sample.c:1:2: error: " + message)[0]
        self.assertIn("score", diagnostic["explanation"])
        self.assertEqual(diagnostic["message"], message)

    def test_unknown_or_unrecognized_suffix_remains_unexplained(self):
        for message in (
            "a new compiler diagnostic",
            "use of undeclared identifier 'score'; unexpected extra text",
        ):
            with self.subTest(message=message):
                self.assertIsNone(diagnose.explain_diagnostic(message))


    def test_format_type_mismatch_explanation(self):
        cases = (
            ("int", "char *"),
            ("char *", "int"),
            ("double", "int"),
            ("int", "int *"),
        )
        for expected, actual in cases:
            with self.subTest(expected=expected, actual=actual):
                message = (
                    f"format specifies type '{expected}' "
                    f"but the argument has type '{actual}' [-Wformat]"
                )
                explanation = diagnose.explain_diagnostic(message)
                self.assertIsNotNone(explanation)
                self.assertEqual(
                    explanation,
                    f"格式说明符要求的参数类型是 {expected}，"
                    f"实际提供的是 {actual}。"
                    "请核对格式说明符与对应参数，使类型一致。",
                )

    def test_unrecognized_format_message_remains_unexplained(self):
        messages = (
            "format specifies type 'int'",
            "format specifies type 'int' but the argument has type 'Count' (aka 'long')",
            "data argument not used by format string [-Wformat-extra-args]",
        )
        for message in messages:
            with self.subTest(message=message):
                self.assertIsNone(diagnose.explain_diagnostic(message))

class DiagnosticParsingTests(unittest.TestCase):
    def test_driver_diagnostics_have_no_source_location(self):
        for prefix in ("clang: ", "clang-18: ", "clang.exe: ", ""):
            with self.subTest(prefix=prefix):
                diagnostics = diagnose.parse_diagnostics(
                    prefix + "warning: input unused [-Wunused-command-line-argument]"
                )
                self.assertEqual(len(diagnostics), 1)
                diagnostic = diagnostics[0]
                self.assertEqual(diagnostic["severity"], "warning")
                for field in ("file", "line", "column"):
                    self.assertIsNone(diagnostic[field])

    def test_windows_path_and_existing_location_are_preserved(self):
        path = "C:/class work/main.c"
        diagnostic = diagnose.parse_diagnostics(
            path + ":12:7: error: expected ')'"
        )[0]
        self.assertEqual(diagnostic["file"], path)
        self.assertEqual((diagnostic["line"], diagnostic["column"]), (12, 7))
        self.assertEqual(diagnostic["severity"], "error")

    def test_source_echo_and_summary_are_not_extra_diagnostics(self):
        output = "\n".join((
            "sample.c:1:3: error: expected ')'",
            "    1 | int main( {",
            "      |           ^",
            "1 error generated.",
        ))
        self.assertEqual(len(diagnose.parse_diagnostics(output)), 1)


class MainReportTests(unittest.TestCase):
    def run_main(self, argv, *, result=None, failure=None):
        with (
            patch("diagnose.run_compiler", return_value=result, side_effect=failure) as compiler,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            try:
                code = diagnose.main(argv)
            except SystemExit as error:
                code = error.code
        return code, json.loads(output.getvalue()), compiler

    def test_error_limit_is_visible_and_marked(self):
        stderr = (
            "sample.c:1:1: error: use of undeclared identifier 'x'\n"
            "fatal error: too many errors emitted, stopping now [-ferror-limit=]\n"
        )
        code, report, _ = self.run_main(
            [str(ROOT / "examples" / "correct.c")],
            result=subprocess.CompletedProcess(["clang"], 1, "", stderr),
        )
        self.assertEqual(code, 1)
        self.assertTrue(report["diagnostics_truncated"])
        self.assertEqual(len(report["diagnostics"]), 2)
        self.assertEqual(report["diagnostics"][-1]["severity"], "fatal error")
        self.assertIsNone(report["diagnostics"][-1]["line"])
        self.assertEqual(report["compiler_output"], stderr)
        self.assertIsNone(report["error"])

    def test_warning_only_check_still_succeeds(self):
        stderr = "clang: warning: an example driver warning\n"
        code, report, _ = self.run_main(
            [str(ROOT / "examples" / "correct.c")],
            result=subprocess.CompletedProcess(["clang"], 0, "", stderr),
        )
        self.assertEqual(code, 0)
        self.assertTrue(report["success"])
        self.assertFalse(report["diagnostics_truncated"])
        self.assertEqual(report["diagnostics"][0]["severity"], "warning")

    def test_other_fatal_errors_are_not_error_limit_truncation(self):
        code, report, _ = self.run_main(
            [str(ROOT / "examples" / "correct.c")],
            result=subprocess.CompletedProcess(
                ["clang"], 1, "", "sample.c:1:1: fatal error: 'absent.h' file not found\n"
            ),
        )
        self.assertEqual(code, 1)
        self.assertFalse(report["diagnostics_truncated"])

    def test_tool_error_keeps_json_and_exit_code(self):
        code, report, _ = self.run_main(
            [str(ROOT / "examples" / "correct.c")],
            failure=FileNotFoundError("compiler unavailable"),
        )
        self.assertEqual(code, 2)
        self.assertEqual(report["error"]["code"], "COMPILER_NOT_FOUND")
        self.assertFalse(report["diagnostics_truncated"])

    def test_path_failures_return_json_without_starting_compiler(self):
        cases = (
            ("resolve", PermissionError("denied")),
            ("resolve", RuntimeError("symlink loop")),
            ("is_file", PermissionError("denied")),
            ("stat", OSError("cannot stat")),
        )
        source = str(ROOT / "examples" / "correct.c")
        for operation, failure in cases:
            with self.subTest(operation=operation, failure=type(failure).__name__):
                with patch("diagnose.Path." + operation, side_effect=failure):
                    code, report, compiler = self.run_main([source])
                compiler.assert_not_called()
                self.assertEqual(code, 2)
                self.assertEqual(report["error"]["code"], "FILE_ACCESS_FAILED")
                self.assertFalse(report["success"])

    def test_language_is_explicit_for_non_c_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "lesson.txt"
            source.write_text("int main(void) { return missing; }\n", encoding="utf-8")
            _, _, compiler = self.run_main(
                [str(source)],
                result=subprocess.CompletedProcess(["clang"], 1, "", ""),
            )
        command = compiler.call_args.args[0]
        language_option = command.index("-x")
        self.assertEqual(command[language_option + 1], "c")
        self.assertLess(language_option, len(command) - 1)
        self.assertEqual(command[-1], str(source.resolve()))


@unittest.skipUnless(shutil.which("clang"), "Clang is required for real compiler checks")
class FileLanguageIntegrationTests(unittest.TestCase):
    def test_file_name_does_not_change_c_check_result(self):
        with tempfile.TemporaryDirectory() as directory:
            for filename in ("lesson.c", "lesson.txt", "lesson", "lesson.cpp"):
                for valid in (False, True):
                    with self.subTest(filename=filename, valid=valid):
                        source = Path(directory) / filename
                        value = "0" if valid else "missing_identifier"
                        source.write_text(
                            "int main(void) { return " + value + "; }\n",
                            encoding="utf-8",
                        )
                        result = subprocess.run(
                            [sys.executable, str(ROOT / "backend" / "diagnose.py"), str(source)],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            timeout=10,
                        )
                        report = json.loads(result.stdout)
                        self.assertEqual(result.returncode, 0 if valid else 1)
                        self.assertEqual(report["success"], valid)
                        self.assertIsNone(report["error"])
                        if not valid:
                            self.assertTrue(any(
                                item["severity"] == "error"
                                and "missing_identifier" in item["message"]
                                for item in report["diagnostics"]
                            ))
