"""Recognize a narrow Clang 18 UBSan report in bounded, decoded stderr."""

import re


_INTEGER_DIVIDE_BY_ZERO = re.compile(
    r"^(/work/source\.c:([1-9][0-9]{0,9}):([1-9][0-9]{0,9})):"
    r" runtime error: division by zero\n"
    r"SUMMARY: UndefinedBehaviorSanitizer: integer-divide-by-zero \1 ?\n",
    re.MULTILINE,
)


def parse_integer_divide_by_zero(stderr: str, input_file: str) -> dict | None:
    """Return a candidate diagnostic, never a verdict on program execution.

    The compiled program can print the same text. The caller must keep the
    original stderr and classify execution using independent stage and resource
    evidence. Coordinates are UBSan's reported coordinates, not editor offsets.
    """
    matches = _INTEGER_DIVIDE_BY_ZERO.finditer(stderr)
    match = next(matches, None)
    if match is None or next(matches, None) is not None:
        return None

    return {
        "kind": "integer_divide_by_zero",
        "location": {
            "file": input_file,
            "line": int(match[2]),
            "column": int(match[3]),
        },
        "raw": match[0],
        "explanation": "整数除法或取余的除数为 0。请检查 / 或 % 右侧的表达式为什么会得到 0。",
    }
