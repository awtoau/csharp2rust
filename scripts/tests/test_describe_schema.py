#!/usr/bin/env python3
"""Focused fixture tests for the read-only corpus schema description command."""

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
TMP_ROOT = ROOT / "tmp"


class DescribeSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        TMP_ROOT.mkdir(exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="schema-fixture-", dir=TMP_ROOT))
        self.database = self.directory / "fixture.db"
        with sqlite3.connect(self.database) as connection:
            connection.executescript(
                """
                PRAGMA user_version = 7;
                CREATE TABLE parent (
                    tenant_id INTEGER NOT NULL,
                    id INTEGER NOT NULL,
                    name TEXT,
                    PRIMARY KEY (tenant_id, id)
                );
                CREATE TABLE child (
                    id INTEGER PRIMARY KEY,
                    tenant_id INTEGER NOT NULL,
                    parent_id INTEGER NOT NULL,
                    label TEXT DEFAULT 'new',
                    FOREIGN KEY (tenant_id, parent_id)
                        REFERENCES parent (tenant_id, id)
                        ON UPDATE CASCADE
                        ON DELETE RESTRICT
                );
                CREATE INDEX child_label_index ON child(label DESC);
                CREATE TABLE audit (
                    child_id INTEGER NOT NULL,
                    event TEXT NOT NULL
                );
                CREATE TRIGGER child_audit
                AFTER INSERT ON child
                BEGIN
                    INSERT INTO audit(child_id, event) VALUES (NEW.id, 'insert');
                END;
                CREATE VIEW child_names AS
                    SELECT child.id, parent.name
                    FROM child JOIN parent
                        ON child.tenant_id = parent.tenant_id
                       AND child.parent_id = parent.id;
                """
            )

    def tearDown(self) -> None:
        shutil.rmtree(self.directory)

    def command(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(DEV), "describe-schema", "--db", str(self.database), *args],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_json_contract_reports_schema_metadata_deterministically(self) -> None:
        first = self.command()
        second = self.command()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)

        document = json.loads(first.stdout)
        self.assertEqual(document["format_version"], 1)
        self.assertEqual(document["database"]["user_version"], 7)
        self.assertRegex(document["database"]["schema_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual([table["name"] for table in document["tables"]],
                         ["audit", "child", "parent"])
        self.assertEqual(document["views"][0]["name"], "child_names")

        child = next(table for table in document["tables"] if table["name"] == "child")
        self.assertEqual(child["primary_key"], ["id"])
        self.assertEqual(
            child["foreign_keys"][0]["columns"],
            [{"from": "tenant_id", "to": "tenant_id"}, {"from": "parent_id", "to": "id"}],
        )
        self.assertEqual(child["foreign_keys"][0]["on_update"], "CASCADE")
        self.assertEqual(child["foreign_keys"][0]["on_delete"], "RESTRICT")
        index = next(index for index in child["indexes"]
                     if index["name"] == "child_label_index")
        self.assertEqual(index["columns"], [
            {"column": "label", "column_id": 3, "descending": True},
        ])
        self.assertEqual(document["triggers"], [{
            "name": "child_audit",
            "sql": document["triggers"][0]["sql"],
            "table": "child",
        }])
        self.assertIn("AFTER INSERT ON child", document["triggers"][0]["sql"])
        self.assertEqual(document["relationships"]["foreign_key_count"], 1)
        self.assertEqual(document["relationships"]["foreign_keys"][0]["from_table"], "child")
        summaries = {entry["table"]: entry for entry in document["relationships"]["table_summaries"]}
        self.assertEqual(summaries["child"]["outgoing_foreign_keys"], 1)
        self.assertEqual(summaries["parent"]["incoming_foreign_keys"], 1)

    def test_text_mode_is_compact_and_no_sql_interface_can_mutate_fixture(self) -> None:
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        result = self.command("--format", "text")
        rejected = self.command("--sql", "DROP TABLE child")
        after = hashlib.sha256(self.database.read_bytes()).hexdigest()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Tables (3):", result.stdout)
        self.assertIn("child(tenant_id, parent_id) -> parent(tenant_id, id)", result.stdout)
        self.assertIn("Triggers (1):", result.stdout)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(before, after)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='child'"
                ).fetchone(),
                ("child",),
            )

    def test_dev_registry_advertises_the_read_only_contract(self) -> None:
        result = subprocess.run(
            [sys.executable, str(DEV), "describe"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        command = json.loads(result.stdout)["commands"]["describe-schema"]
        self.assertEqual(command["args"], "--db PATH [--format json|text]")
        self.assertEqual(command["kind"], "analysis")


if __name__ == "__main__":
    unittest.main()
