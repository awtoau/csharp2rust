"""Regression tests for Renode expression-position gaps."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from emitter.plugins.renode_expressions import RenodeExpressions


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


class RenodeExpressionTests(unittest.TestCase):
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
