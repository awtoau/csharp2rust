#!/usr/bin/env python3
"""Focused fixture tests for the versioned read-only corpus recipe registry."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "scripts" / "dev.py"
SCHEMA = ROOT / "rulesdb" / "schema.sql"
TMP_ROOT = ROOT / "tmp"
PROVENANCE_COLUMNS = {
    "corpus_run_id",
    "corpus_commit",
    "corpus_tool_version",
    "source_path",
    "containing_type",
    "member_id",
    "member_key",
    "member_name",
    "method_signature",
    "operation_id",
    "span_start",
    "span_len",
}


class CorpusRecipeTests(unittest.TestCase):
    def setUp(self) -> None:
        TMP_ROOT.mkdir(exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="recipe-fixture-", dir=TMP_ROOT))
        self.database = self.directory / "fixture.db"
        with sqlite3.connect(self.database) as connection:
            connection.executescript(SCHEMA.read_text(encoding="utf-8"))
            connection.executescript(
                """
                INSERT INTO corpus_run
                    (id, started_at, renode_commit, tool_version, config)
                    VALUES (1, '2026-10-16T00:00:00Z', 'fixture-commit', 'fixture-tool', 'tree');
                INSERT INTO file (id, run_id, path, sha256, loc)
                    VALUES (1, 1, 'Models/Fixture.cs', 'fixture-sha', 40);
                INSERT INTO type
                    (id, run_id, file_id, key, namespace, name, kind, accessibility)
                    VALUES (1, 1, 1, 'Fixture.Widget', 'Fixture', 'Widget', 'class', 'public');
                INSERT INTO member
                    (id, run_id, type_id, key, kind, name, declared_type, accessibility)
                    VALUES (1, 1, 1, 'Fixture.Widget.Run()', 'method', 'Run', 'void', 'public');
                INSERT INTO method (member_id, signature, return_type, has_body)
                    VALUES (1, 'void Fixture.Widget.Run()', 'void', 1);
                INSERT INTO local (id, method_id, name, type)
                    VALUES (1, 1, 'reg', 'DoubleWordRegister');
                INSERT INTO operation
                    (id, run_id, method_id, parent_id, ordinal, depth, kind, type, symbol,
                     span_start, span_len)
                    VALUES
                    (1, 1, 1, NULL, 0, 0, 'Invocation', 'void',
                     'Fixture.Widget.Helper()', 1, 2),
                    (2, 1, 1, NULL, 1, 0, 'Throw', NULL, NULL, 5, 3),
                    (3, 1, 1, 2, 0, 1, 'Conversion', 'System.Exception', NULL, 5, 3),
                    (4, 1, 1, 3, 0, 2, 'ObjectCreation', 'System.NotImplementedException',
                     'System.NotImplementedException.NotImplementedException()', 5, 3),
                    (5, 1, 1, NULL, 2, 0, 'VariableDeclarator', NULL, NULL, 20, 10),
                    (6, 1, 1, 5, 0, 1, 'Invocation', NULL,
                     'Fixture.Register.Define(Fixture.Widget)', 21, 2),
                    (7, 1, 1, NULL, 3, 0, 'Invocation', NULL,
                     'Antmicro.Renode.Core.Structure.Registers.PeripheralRegisterExtensions.WithFlag()', 31, 2),
                    (8, 1, 1, 7, 0, 1, 'Argument', NULL, NULL, 31, 1),
                    (9, 1, 1, 8, 0, 2, 'LocalReference', NULL, 'Fixture.Widget reg', 31, 1);
                INSERT INTO method_fingerprint (method_id, fingerprint, norm_version)
                    VALUES (1, 'fixture-method-shape', 'v1');
                INSERT INTO pattern_cluster
                    (id, run_id, granularity, fingerprint, member_count, node_count, exemplar_id,
                     exemplar_op)
                    VALUES (1, 1, 'statement', 'fixture-cluster', 1, 2, 1, 1);
                INSERT INTO cluster_member (cluster_id, operation_id, method_id)
                    VALUES (1, 1, 1);
                """
            )
            connection.execute(
                "UPDATE operation SET detail=? WHERE id=5",
                ('{"local":"reg"}',),
            )

    def tearDown(self) -> None:
        shutil.rmtree(self.directory)

    def command(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(DEV), *args],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def query(self, recipe: str, *args: str) -> dict:
        result = self.command(
            "query-recipe", "--db", str(self.database), "--recipe", recipe, *args
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_registry_has_only_versioned_bounded_recipes_with_provenance(self) -> None:
        result = self.command("list-recipes")

        self.assertEqual(result.returncode, 0, result.stderr)
        registry = json.loads(result.stdout)
        self.assertEqual(registry["format_version"], 1)
        recipes = registry["recipes"]
        self.assertEqual(
            [recipe["name"] for recipe in recipes],
            [
                "cluster-members",
                "local-register-builder",
                "method-shape",
                "operation-kind",
                "operation-symbol",
                "throw-not-implemented",
            ],
        )
        for recipe in recipes:
            self.assertEqual(recipe["version"], 1)
            self.assertTrue(PROVENANCE_COLUMNS <= set(recipe["output_columns"]))
            self.assertTrue(recipe["semantic_intent"])
            self.assertTrue(recipe["referenced_tables"])

    def test_each_recipe_returns_required_provenance_and_semantic_facts(self) -> None:
        cases = (
            ("operation-kind", ("--kind", "Invocation"), "operation_kind"),
            ("operation-symbol", ("--symbol", "Fixture.Widget.Helper()"), "operation_symbol"),
            ("throw-not-implemented", (), "constructor_symbol"),
            ("method-shape", ("--fingerprint", "fixture-method-shape"), "method_fingerprint"),
            ("local-register-builder", (), "register_form_symbol"),
            ("cluster-members", ("--cluster-id", "1"), "cluster_id"),
        )
        for recipe, arguments, semantic_column in cases:
            with self.subTest(recipe=recipe):
                document = self.query(recipe, *arguments)
                self.assertEqual(document["recipe"]["name"], recipe)
                self.assertLessEqual(len(document["rows"]), document["limit"])
                self.assertTrue(document["rows"])
                row = document["rows"][0]
                self.assertTrue(PROVENANCE_COLUMNS <= set(row))
                self.assertEqual(row["corpus_run_id"], 1)
                self.assertEqual(row["source_path"], "Models/Fixture.cs")
                self.assertEqual(row["method_signature"], "void Fixture.Widget.Run()")
                self.assertIn(semantic_column, row)

    def test_results_are_deterministic_limited_and_database_remains_unchanged(self) -> None:
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        first = self.command(
            "query-recipe", "--db", str(self.database), "--recipe", "operation-kind",
            "--kind", "Invocation", "--limit", "1",
        )
        second = self.command(
            "query-recipe", "--db", str(self.database), "--recipe", "operation-kind",
            "--kind", "Invocation", "--limit", "1",
        )
        after = hashlib.sha256(self.database.read_bytes()).hexdigest()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(len(json.loads(first.stdout)["rows"]), 1)
        self.assertEqual(before, after)

    def test_invalid_recipe_parameters_and_limits_are_rejected(self) -> None:
        failures = (
            ("query-recipe", "--db", str(self.database), "--recipe", "unknown"),
            ("query-recipe", "--db", str(self.database), "--recipe", "operation-kind"),
            ("query-recipe", "--db", str(self.database), "--recipe", "throw-not-implemented",
             "--kind", "Throw"),
            ("query-recipe", "--db", str(self.database), "--recipe", "cluster-members",
             "--cluster-id", "0"),
            ("query-recipe", "--db", str(self.database), "--recipe", "operation-kind",
             "--kind", "Invocation", "--limit", "101"),
        )
        for arguments in failures:
            with self.subTest(arguments=arguments):
                result = self.command(*arguments)
                self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
