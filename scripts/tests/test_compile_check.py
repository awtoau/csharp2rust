"""Focused regression tests for rustc diagnostic grouping."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "compile_check", ROOT / "scripts" / "validation" / "compile_check.py")
assert SPEC is not None and SPEC.loader is not None
compile_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compile_check)


class CompileCheckTests(unittest.TestCase):
    def test_groups_code_less_errors_by_message(self) -> None:
        expected_pattern = {"code": None, "message": "expected a pattern"}
        expected_expression = {"code": None, "message": "expected expression, found `;`"}

        self.assertEqual(
            compile_check.error_group_key(expected_pattern),
            "no-code: expected a pattern",
        )
        self.assertEqual(
            compile_check.error_group_key(expected_expression),
            "no-code: expected expression, found `;`",
        )
        self.assertNotEqual(
            compile_check.error_group_key(expected_pattern),
            compile_check.error_group_key(expected_expression),
        )

    def test_preserves_rustc_error_code_grouping(self) -> None:
        diagnostic = {
            "code": {"code": "E0308"},
            "message": "mismatched types",
        }

        self.assertEqual(compile_check.error_group_key(diagnostic), "E0308")
