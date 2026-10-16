"""Regression tests for Renode expression-position gaps."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emitter.plugins.renode_expressions import RenodeExpressions
from emitter.lang.expressions import Expressions


class FakeEmitter(RenodeExpressions):
    def __init__(self, root: int) -> None:
        self._current_type = "Derived"
        self._emitted_fns: set[str] = set()
        self._stmt_expression_root = root
        self._loop_env: dict[str, int] = {}
        self.expressions: list[dict[str, str]] = []
        self.project: dict[str, object] = {}
        self.gaps: list[str] = []
        self.unhandled: dict[str, int] = {}

    def kind_of(self, _oid: int) -> str:
        return "InstanceReference"

    def local_closure_body(self, _all_kids: object) -> None:
        return None


class FormatEmitter(Expressions):
    def __init__(self) -> None:
        self.language = {
            "format_strings": {
                "specs": {"x": "x", "X": "X"},
                "padded_hex": "0{width}{spec}",
            },
        }
        self.unhandled: dict[str, int] = {}


class RenodeExpressionTests(unittest.TestCase):
    def test_composite_format_preserves_repeated_argument_indexes(self) -> None:
        emitter = FormatEmitter()

        self.assertEqual(
            emitter.csharp_format(
                "RTC writing byte 0x{0:x} to register {1} (0x{1:x})"),
            "RTC writing byte 0x{0:x} to register {1} (0x{1:x})",
        )
        self.assertEqual(emitter.unhandled, {})

    def test_composite_format_reports_unsupported_alignment(self) -> None:
        emitter = FormatEmitter()

        self.assertEqual(
            emitter.csharp_format("value {0,8:X}"),
            "value {{GAP: unsupported C# composite format}}",
        )
        self.assertEqual(emitter.unhandled, {"format:unsupported-composite": 1})

    def test_log_message_drops_surplus_arguments_without_placeholders(self) -> None:
        self.assertEqual(
            RenodeExpressions._log_message_args(['"plain message"', "value"]),
            ['"plain message"'],
        )

    def test_log_message_keeps_reordered_format_arguments(self) -> None:
        self.assertEqual(
            RenodeExpressions._log_message_args(['"{1:x} then {0}"', "first", "second"]),
            ['"{1:x} then {0}"', "first", "second"],
        )

    def test_log_message_preserves_interpolated_string_lowering(self) -> None:
        self.assertEqual(
            RenodeExpressions._log_message_args(['format!("value {}", value)']),
            ['"value {}", value'],
        )

    def test_nested_base_call_uses_compile_valid_gap_expression(self) -> None:
        emitter = FakeEmitter(root=1)

        emitted = emitter.plugin_expr(
            2, "Invocation", "Base.Foo()", None, None, None, [], [],
            [(2, "InstanceReference", None)],
        )

        self.assertEqual(emitted, 'unimplemented!("GAP: base-class call")')
        self.assertEqual(emitter.unhandled, {"expr:BaseClassCall": 1})

    def test_statement_root_base_call_remains_explicit_comment_gap(self) -> None:
        emitter = FakeEmitter(root=2)

        emitted = emitter.plugin_expr(
            2, "Invocation", "Base.Foo()", None, None, None, [], [],
            [(2, "InstanceReference", None)],
        )

        self.assertEqual(emitted, "/* GAP: base-class call */")
        self.assertEqual(emitter.unhandled, {})
