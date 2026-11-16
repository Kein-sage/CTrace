import unittest

from runtime_ubsan import parse_integer_divide_by_zero


DIAGNOSTIC = "/work/source.c:4:15: runtime error: division by zero"
SUMMARY = "SUMMARY: UndefinedBehaviorSanitizer: integer-divide-by-zero /work/source.c:4:15 "
INPUT_FILE = "练习/分数.c"


class RuntimeUbsanTests(unittest.TestCase):
    def test_matching_report_retains_both_lines_and_maps_the_input_file(self):
        stderr = f"program message\n{DIAGNOSTIC}\n{SUMMARY}\n"
        result = parse_integer_divide_by_zero(stderr, INPUT_FILE)

        self.assertEqual(result["kind"], "integer_divide_by_zero")
        self.assertEqual(result["location"], {"file": INPUT_FILE, "line": 4, "column": 15})
        self.assertEqual(result["raw"], f"{DIAGNOSTIC}\n{SUMMARY}\n")
        self.assertIn("除数", result["explanation"])

    def test_requires_both_lines_and_an_integer_check_summary(self):
        for stderr in (
            "",
            f"{DIAGNOSTIC}\n",
            f"{SUMMARY}\n",
            f"{DIAGNOSTIC}\n{SUMMARY.replace('integer-divide-by-zero', 'undefined-behavior')}\n",
            f"{DIAGNOSTIC}\n{SUMMARY.replace('integer-divide-by-zero', 'float-divide-by-zero')}\n",
            f"{DIAGNOSTIC}\n{SUMMARY}",
        ):
            with self.subTest(stderr=stderr):
                self.assertIsNone(parse_integer_divide_by_zero(stderr, INPUT_FILE))

    def test_does_not_map_mismatched_or_non_source_locations(self):
        for stderr in (
            f"{DIAGNOSTIC}\n{SUMMARY.replace(':4:15', ':5:15')}\n",
            f"{DIAGNOSTIC}\n{SUMMARY.replace(':4:15', ':4:16')}\n",
            (f"{DIAGNOSTIC}\n{SUMMARY}\n").replace("/work/source.c", "/work/header.h"),
            (f"{DIAGNOSTIC}\n{SUMMARY}\n").replace(":4:15", ":0:15"),
            (f"{DIAGNOSTIC}\n{SUMMARY}\n").replace(":4:15", ":4:0"),
            (f"{DIAGNOSTIC}\n{SUMMARY}\n").replace(":4:15", ":4:" + "9" * 5000),
        ):
            with self.subTest(stderr=stderr[:160]):
                self.assertIsNone(parse_integer_divide_by_zero(stderr, INPUT_FILE))

    def test_rejects_ambiguous_reports_and_embedded_text(self):
        pair = f"{DIAGNOSTIC}\n{SUMMARY}\n"
        for stderr in (
            pair * 2,
            f"prefix {pair}",
            f"{DIAGNOSTIC}\ninterleaved stderr\n{SUMMARY}\n",
        ):
            with self.subTest(stderr=stderr):
                self.assertIsNone(parse_integer_divide_by_zero(stderr, INPUT_FILE))
