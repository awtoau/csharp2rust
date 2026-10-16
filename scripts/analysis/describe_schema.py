#!/usr/bin/env python3
"""Describe a corpus SQLite schema without executing corpus queries.

Run:
    python3 scripts/dev.py describe-schema --db PATH [--format json|text]

The database is opened with SQLite's read-only URI mode. This command only
uses schema PRAGMAs and sqlite_master; it accepts no SQL input and makes no
database changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


FORMAT_VERSION = 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Describe a corpus SQLite schema using a read-only connection."
    )
    result.add_argument(
        "--db",
        required=True,
        metavar="PATH",
        help="SQLite corpus database path (opened read-only).",
    )
    result.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="Output format (default: json).",
    )
    return result


def open_read_only(path_text: str) -> sqlite3.Connection:
    path = Path(path_text).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"database is not a regular file: {path_text}")

    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    return connection


def schema_objects(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE type IN ('index', 'table', 'trigger', 'view')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()
    return [
        {"name": name, "sql": sql, "table": table, "type": object_type}
        for object_type, name, table, sql in rows
    ]


def foreign_keys(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    grouped: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in connection.execute(f"PRAGMA foreign_key_list({quote_identifier(table)})"):
        grouped[row["id"]].append(row)

    result = []
    for foreign_key_id, rows in sorted(grouped.items()):
        ordered = sorted(rows, key=lambda row: row["seq"])
        result.append(
            {
                "columns": [
                    {"from": row["from"], "to": row["to"]}
                    for row in ordered
                ],
                "id": foreign_key_id,
                "match": ordered[0]["match"],
                "on_delete": ordered[0]["on_delete"],
                "on_update": ordered[0]["on_update"],
                "references_table": ordered[0]["table"],
            }
        )
    return result


def indexes(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    result = []
    for row in connection.execute(f"PRAGMA index_list({quote_identifier(table)})"):
        name = row["name"]
        columns = [
            {
                "column": index_row["name"],
                "column_id": index_row["cid"],
                "descending": bool(index_row["desc"]),
            }
            for index_row in connection.execute(
                f"PRAGMA index_xinfo({quote_identifier(name)})"
            )
            if index_row["key"]
        ]
        result.append(
            {
                "columns": columns,
                "name": name,
                "origin": row["origin"],
                "partial": bool(row["partial"]),
                "unique": bool(row["unique"]),
            }
        )
    return sorted(result, key=lambda item: item["name"])


def tables(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    names = [
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    result = []
    for name in names:
        columns = [
            {
                "default": row["dflt_value"],
                "name": row["name"],
                "not_null": bool(row["notnull"]),
                "primary_key_position": row["pk"],
                "type": row["type"],
            }
            for row in connection.execute(f"PRAGMA table_info({quote_identifier(name)})")
        ]
        primary_key = [
            column["name"]
            for column in sorted(
                (column for column in columns if column["primary_key_position"]),
                key=lambda column: column["primary_key_position"],
            )
        ]
        result.append(
            {
                "columns": columns,
                "foreign_keys": foreign_keys(connection, name),
                "indexes": indexes(connection, name),
                "name": name,
                "primary_key": primary_key,
            }
        )
    return result


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def relationship_summary(tables_data: list[dict[str, Any]]) -> dict[str, Any]:
    relationships = []
    for table in tables_data:
        for foreign_key in table["foreign_keys"]:
            relationships.append(
                {
                    "columns": foreign_key["columns"],
                    "from_table": table["name"],
                    "match": foreign_key["match"],
                    "on_delete": foreign_key["on_delete"],
                    "on_update": foreign_key["on_update"],
                    "to_table": foreign_key["references_table"],
                }
            )
    relationships.sort(
        key=lambda item: (
            item["from_table"],
            item["to_table"],
            [(column["from"], column["to"]) for column in item["columns"]],
        )
    )

    outgoing: dict[str, int] = defaultdict(int)
    incoming: dict[str, int] = defaultdict(int)
    for relationship in relationships:
        outgoing[relationship["from_table"]] += 1
        incoming[relationship["to_table"]] += 1
    summaries = [
        {
            "incoming_foreign_keys": incoming[table["name"]],
            "outgoing_foreign_keys": outgoing[table["name"]],
            "table": table["name"],
        }
        for table in tables_data
    ]
    return {
        "foreign_key_count": len(relationships),
        "foreign_keys": relationships,
        "table_summaries": summaries,
    }


def describe(connection: sqlite3.Connection) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    objects = schema_objects(connection)
    canonical_schema = json.dumps(
        objects, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    table_data = tables(connection)
    triggers = [
        {"name": item["name"], "sql": item["sql"], "table": item["table"]}
        for item in objects
        if item["type"] == "trigger"
    ]
    views = [
        {"name": item["name"], "sql": item["sql"]}
        for item in objects
        if item["type"] == "view"
    ]
    return {
        "database": {
            "application_id": connection.execute("PRAGMA application_id").fetchone()[0],
            "schema_sha256": hashlib.sha256(canonical_schema).hexdigest(),
            "schema_version": connection.execute("PRAGMA schema_version").fetchone()[0],
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
        },
        "format_version": FORMAT_VERSION,
        "relationships": relationship_summary(table_data),
        "tables": table_data,
        "triggers": triggers,
        "views": views,
    }


def text_description(document: dict[str, Any]) -> str:
    database = document["database"]
    lines = [
        "Corpus schema "
        f"(schema_version={database['schema_version']}, "
        f"user_version={database['user_version']}, "
        f"sha256={database['schema_sha256']})",
        f"Tables ({len(document['tables'])}):",
    ]
    for table in document["tables"]:
        columns = ", ".join(
            f"{column['name']} {column['type'] or 'ANY'}"
            + (" NOT NULL" if column["not_null"] else "")
            for column in table["columns"]
        )
        details = []
        if table["primary_key"]:
            details.append("PK " + ", ".join(table["primary_key"]))
        if table["indexes"]:
            details.append(f"{len(table['indexes'])} index(es)")
        if table["foreign_keys"]:
            details.append(f"{len(table['foreign_keys'])} FK(s)")
        suffix = f" [{'; '.join(details)}]" if details else ""
        lines.append(f"- {table['name']}: {columns}{suffix}")

    relationships = document["relationships"]["foreign_keys"]
    lines.append(f"Relationships ({len(relationships)}):")
    for relationship in relationships:
        from_columns = ", ".join(column["from"] for column in relationship["columns"])
        to_columns = ", ".join(column["to"] or "?" for column in relationship["columns"])
        actions = (
            f" ON UPDATE {relationship['on_update']}"
            f" ON DELETE {relationship['on_delete']}"
        )
        lines.append(
            f"- {relationship['from_table']}({from_columns}) -> "
            f"{relationship['to_table']}({to_columns}){actions}"
        )
    lines.append(f"Triggers ({len(document['triggers'])}):")
    lines.extend(
        f"- {trigger['name']} on {trigger['table']}" for trigger in document["triggers"]
    )
    if document["views"]:
        lines.append(f"Views ({len(document['views'])}):")
        lines.extend(f"- {view['name']}" for view in document["views"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        with open_read_only(args.db) as connection:
            document = describe(connection)
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"describe-schema: {error}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(document, indent=2, sort_keys=True))
    else:
        print(text_description(document))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
