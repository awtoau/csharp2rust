#!/usr/bin/env python3
"""Run versioned, read-only semantic queries over an explicit corpus database.

Run:
    python3 scripts/dev.py list-recipes
    python3 scripts/dev.py query-recipe --db PATH --recipe NAME [recipe input]

Only the recipes published by ``list`` are available. This command does not
accept SQL and opens supplied databases with SQLite's read-only URI mode.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


FORMAT_VERSION = 1
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

PROVENANCE_COLUMNS = (
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
)


@dataclass(frozen=True)
class Recipe:
    name: str
    semantic_intent: str
    parameters: tuple[dict[str, str], ...]
    semantic_columns: tuple[str, ...]
    referenced_tables: tuple[str, ...]
    query: Callable[[sqlite3.Connection, argparse.Namespace], list[dict[str, Any]]]

    def description(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "output_columns": [*PROVENANCE_COLUMNS, *self.semantic_columns],
            "parameters": list(self.parameters),
            "referenced_tables": list(self.referenced_tables),
            "semantic_intent": self.semantic_intent,
            "version": 1,
        }


OPERATION_PROVENANCE = """
    o.run_id AS corpus_run_id,
    cr.renode_commit AS corpus_commit,
    cr.tool_version AS corpus_tool_version,
    f.path AS source_path,
    t.key AS containing_type,
    mb.id AS member_id,
    mb.key AS member_key,
    mb.name AS member_name,
    m.signature AS method_signature,
    o.id AS operation_id,
    o.span_start AS span_start,
    o.span_len AS span_len
"""

METHOD_PROVENANCE = """
    mb.run_id AS corpus_run_id,
    cr.renode_commit AS corpus_commit,
    cr.tool_version AS corpus_tool_version,
    f.path AS source_path,
    t.key AS containing_type,
    mb.id AS member_id,
    mb.key AS member_key,
    mb.name AS member_name,
    m.signature AS method_signature,
    NULL AS operation_id,
    NULL AS span_start,
    NULL AS span_len
"""


def rows(connection: sqlite3.Connection, statement: str,
         parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(statement, parameters)]


def operation_kind(connection: sqlite3.Connection, args: argparse.Namespace) -> list[dict[str, Any]]:
    return rows(
        connection,
        f"""
        SELECT {OPERATION_PROVENANCE},
               o.kind AS operation_kind, o.type AS operation_type,
               o.symbol AS operation_symbol, o.const_value AS constant_value
        FROM operation o
        JOIN corpus_run cr ON cr.id = o.run_id
        JOIN member mb ON mb.id = o.method_id
        JOIN type t ON t.id = mb.type_id
        JOIN file f ON f.id = t.file_id
        LEFT JOIN method m ON m.member_id = mb.id
        WHERE o.kind = ?
        ORDER BY f.path, t.key, mb.key, o.span_start, o.id
        LIMIT ?
        """,
        (args.kind, args.limit),
    )


def operation_symbol(connection: sqlite3.Connection, args: argparse.Namespace) -> list[dict[str, Any]]:
    return rows(
        connection,
        f"""
        SELECT {OPERATION_PROVENANCE},
               o.kind AS operation_kind, o.type AS operation_type,
               o.symbol AS operation_symbol, o.const_value AS constant_value
        FROM operation o
        JOIN corpus_run cr ON cr.id = o.run_id
        JOIN member mb ON mb.id = o.method_id
        JOIN type t ON t.id = mb.type_id
        JOIN file f ON f.id = t.file_id
        LEFT JOIN method m ON m.member_id = mb.id
        WHERE o.symbol = ?
        ORDER BY f.path, t.key, mb.key, o.span_start, o.id
        LIMIT ?
        """,
        (args.symbol, args.limit),
    )


def throw_not_implemented(connection: sqlite3.Connection,
                          args: argparse.Namespace) -> list[dict[str, Any]]:
    return rows(
        connection,
        f"""
        SELECT {OPERATION_PROVENANCE.replace("o.", "th.")},
               th.kind AS operation_kind, cv.type AS exception_base_type,
               oc.symbol AS constructor_symbol
        FROM operation th
        JOIN operation cv ON cv.parent_id = th.id AND cv.kind = 'Conversion'
        JOIN operation oc ON oc.parent_id = cv.id AND oc.kind = 'ObjectCreation'
        JOIN corpus_run cr ON cr.id = th.run_id
        JOIN member mb ON mb.id = th.method_id
        JOIN type t ON t.id = mb.type_id
        JOIN file f ON f.id = t.file_id
        LEFT JOIN method m ON m.member_id = mb.id
        WHERE th.kind = 'Throw'
          AND oc.symbol = 'System.NotImplementedException.NotImplementedException()'
        ORDER BY f.path, t.key, mb.key, th.span_start, th.id
        LIMIT ?
        """,
        (args.limit,),
    )


def method_shape(connection: sqlite3.Connection, args: argparse.Namespace) -> list[dict[str, Any]]:
    return rows(
        connection,
        f"""
        SELECT {METHOD_PROVENANCE},
               mf.fingerprint AS method_fingerprint,
               mf.norm_version AS normalization_version
        FROM method_fingerprint mf
        JOIN method m ON m.member_id = mf.method_id
        JOIN member mb ON mb.id = m.member_id
        JOIN corpus_run cr ON cr.id = mb.run_id
        JOIN type t ON t.id = mb.type_id
        JOIN file f ON f.id = t.file_id
        WHERE mf.fingerprint = ?
        ORDER BY f.path, t.key, m.signature, mb.id
        LIMIT ?
        """,
        (args.fingerprint, args.limit),
    )


def local_register_builder(connection: sqlite3.Connection,
                           args: argparse.Namespace) -> list[dict[str, Any]]:
    # A local is a register builder only when (1) a fluent register extension
    # call is rooted at that local and (2) a variable declarator in the same
    # method contains a `.Define(` or dictionary `Add` register form. These
    # markers are fixed parts of the committed register-DSL semantics, not
    # caller-supplied query fragments.
    return rows(
        connection,
        f"""
        WITH RECURSIVE receiver(call_id, node_id, method_id, steps) AS (
            SELECT o.id, o.id, o.method_id, 0
            FROM operation o
            WHERE o.kind = 'Invocation'
              AND (
                  o.symbol LIKE '%PeripheralRegisterExtensions.%'
                  OR o.symbol LIKE '%DoubleWordRegisterExtensions.%'
                  OR o.symbol LIKE '%PeripheralRegister.%'
                  OR o.symbol LIKE '%PeripheralRegister<T>.%'
              )
            UNION ALL
            SELECT receiver.call_id, child.id, receiver.method_id, receiver.steps + 1
            FROM receiver
            JOIN operation child
              ON child.parent_id = receiver.node_id AND child.ordinal = 0
            WHERE receiver.steps < 64
              AND child.kind IN ('Invocation', 'Conversion', 'Parenthesized', 'Argument')
        ),
        local_roots AS (
            SELECT DISTINCT receiver.call_id, local_ref.symbol AS local_symbol
            FROM receiver
            JOIN operation local_ref
              ON local_ref.parent_id = receiver.node_id
             AND local_ref.ordinal = 0
             AND local_ref.kind = 'LocalReference'
        )
        SELECT {OPERATION_PROVENANCE.replace("o.", "call.")},
               call.symbol AS combinator_symbol,
               local_roots.local_symbol AS builder_local_symbol,
               declarator.id AS builder_declaration_operation_id,
               (
                   SELECT form.symbol
                   FROM operation form
                   WHERE form.method_id = call.method_id
                     AND form.kind = 'Invocation'
                     AND form.span_start >= declarator.span_start
                     AND form.span_start < declarator.span_start + declarator.span_len
                     AND (
                         form.symbol LIKE '%.Define(%'
                         OR form.symbol LIKE '%Dictionary<TKey, TValue>.Add%'
                     )
                   ORDER BY form.id
                   LIMIT 1
               ) AS register_form_symbol
        FROM local_roots
        JOIN operation call ON call.id = local_roots.call_id
        JOIN member mb ON mb.id = call.method_id
        JOIN corpus_run cr ON cr.id = call.run_id
        JOIN type t ON t.id = mb.type_id
        JOIN file f ON f.id = t.file_id
        LEFT JOIN method m ON m.member_id = mb.id
        JOIN operation declarator
          ON declarator.method_id = call.method_id
         AND declarator.kind = 'VariableDeclarator'
        WHERE (
              local_roots.local_symbol =
                  json_extract(COALESCE(declarator.detail, '{{}}'), '$.local')
              OR local_roots.local_symbol LIKE '% ' ||
                  json_extract(COALESCE(declarator.detail, '{{}}'), '$.local')
        )
          AND EXISTS (
              SELECT 1
              FROM operation form
              WHERE form.method_id = call.method_id
                AND form.kind = 'Invocation'
                AND form.span_start >= declarator.span_start
                AND form.span_start < declarator.span_start + declarator.span_len
                AND (
                    form.symbol LIKE '%.Define(%'
                    OR form.symbol LIKE '%Dictionary<TKey, TValue>.Add%'
                )
          )
        ORDER BY f.path, t.key, mb.key, call.span_start, call.id
        LIMIT ?
        """,
        (args.limit,),
    )


def cluster_members(connection: sqlite3.Connection, args: argparse.Namespace) -> list[dict[str, Any]]:
    return rows(
        connection,
        f"""
        SELECT {OPERATION_PROVENANCE},
               pc.id AS cluster_id, pc.granularity AS cluster_granularity,
               pc.fingerprint AS cluster_fingerprint,
               pc.member_count AS cluster_member_count,
               pc.node_count AS cluster_node_count
        FROM cluster_member cm
        JOIN pattern_cluster pc ON pc.id = cm.cluster_id
        JOIN operation o ON o.id = cm.operation_id
        JOIN corpus_run cr ON cr.id = o.run_id
        JOIN member mb ON mb.id = o.method_id
        JOIN type t ON t.id = mb.type_id
        JOIN file f ON f.id = t.file_id
        LEFT JOIN method m ON m.member_id = mb.id
        WHERE cm.cluster_id = ?
        ORDER BY f.path, t.key, mb.key, o.span_start, o.id
        LIMIT ?
        """,
        (args.cluster_id, args.limit),
    )


RECIPES = (
    Recipe(
        "cluster-members",
        "List operation members of one structural cluster.",
        ({"name": "cluster-id", "type": "positive integer", "required": "true"},),
        ("cluster_id", "cluster_granularity", "cluster_fingerprint",
         "cluster_member_count", "cluster_node_count"),
        ("cluster_member", "corpus_run", "file", "member", "method", "operation",
         "pattern_cluster", "type"),
        cluster_members,
    ),
    Recipe(
        "local-register-builder",
        "Find register-DSL combinator calls rooted at a locally declared register builder.",
        (),
        ("combinator_symbol", "builder_local_symbol",
         "builder_declaration_operation_id", "register_form_symbol"),
        ("corpus_run", "file", "member", "method", "operation", "type"),
        local_register_builder,
    ),
    Recipe(
        "method-shape",
        "List methods with one exact normalized operation-tree fingerprint.",
        ({"name": "fingerprint", "type": "non-empty string", "required": "true"},),
        ("method_fingerprint", "normalization_version"),
        ("corpus_run", "file", "member", "method", "method_fingerprint", "type"),
        method_shape,
    ),
    Recipe(
        "operation-kind",
        "List operations with one exact Roslyn IOperation kind.",
        ({"name": "kind", "type": "non-empty string", "required": "true"},),
        ("operation_kind", "operation_type", "operation_symbol", "constant_value"),
        ("corpus_run", "file", "member", "method", "operation", "type"),
        operation_kind,
    ),
    Recipe(
        "operation-symbol",
        "List operations with one exact resolved symbol.",
        ({"name": "symbol", "type": "non-empty string", "required": "true"},),
        ("operation_kind", "operation_type", "operation_symbol", "constant_value"),
        ("corpus_run", "file", "member", "method", "operation", "type"),
        operation_symbol,
    ),
    Recipe(
        "throw-not-implemented",
        "List Throw -> Conversion -> NotImplementedException construction facts.",
        (),
        ("operation_kind", "exception_base_type", "constructor_symbol"),
        ("corpus_run", "file", "member", "method", "operation", "type"),
        throw_not_implemented,
    ),
)
RECIPE_BY_NAME = {recipe.name: recipe for recipe in RECIPES}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="emit the stable recipe registry as JSON")
    query = commands.add_parser("query", help="run one registered semantic query")
    query.add_argument("--db", required=True, metavar="PATH",
                       help="SQLite corpus database path (opened read-only).")
    query.add_argument("--recipe", required=True, metavar="NAME",
                       help="Recipe name from the list command.")
    query.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                       help=f"Maximum rows to return (1-{MAX_LIMIT}; default {DEFAULT_LIMIT}).")
    query.add_argument("--kind", help="Exact IOperation kind for operation-kind.")
    query.add_argument("--symbol", help="Exact resolved symbol for operation-symbol.")
    query.add_argument("--fingerprint", help="Exact fingerprint for method-shape.")
    query.add_argument("--cluster-id", type=int, help="Positive cluster id for cluster-members.")
    return result


def open_read_only(path_text: str) -> sqlite3.Connection:
    path = Path(path_text).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"database is not a regular file: {path_text}")
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def validate_query(args: argparse.Namespace, arg_parser: argparse.ArgumentParser) -> Recipe:
    recipe = RECIPE_BY_NAME.get(args.recipe)
    if recipe is None:
        arg_parser.error(f"unknown recipe: {args.recipe!r}; run list-recipes")
    if not 1 <= args.limit <= MAX_LIMIT:
        arg_parser.error(f"--limit must be between 1 and {MAX_LIMIT}")

    supplied = {
        name.replace("_", "-")
        for name in ("kind", "symbol", "fingerprint", "cluster_id")
        if getattr(args, name) is not None
    }
    accepted = {parameter["name"] for parameter in recipe.parameters}
    unexpected = sorted(supplied - accepted)
    if unexpected:
        arg_parser.error(
            f"--{unexpected[0]} is not an input for recipe {recipe.name!r}"
        )
    for parameter in recipe.parameters:
        value = getattr(args, parameter["name"].replace("-", "_"))
        if parameter["required"] == "true" and (value is None or not str(value).strip()):
            arg_parser.error(
                f"--{parameter['name']} is required for recipe {recipe.name!r}"
            )
    if args.cluster_id is not None and args.cluster_id < 1:
        arg_parser.error("--cluster-id must be a positive integer")
    for name in ("kind", "symbol", "fingerprint"):
        value = getattr(args, name)
        if value is not None and len(value) > 512:
            arg_parser.error(f"--{name} may not exceed 512 characters")
    return recipe


def main(argv: list[str] | None = None) -> int:
    arg_parser = parser()
    args = arg_parser.parse_args(argv)
    if args.command == "list":
        print(json.dumps(
            {"format_version": FORMAT_VERSION,
             "recipes": [recipe.description() for recipe in RECIPES]},
            indent=2,
            sort_keys=True,
        ))
        return 0

    recipe = validate_query(args, arg_parser)
    try:
        with open_read_only(args.db) as connection:
            result_rows = recipe.query(connection, args)
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"query-recipe: {error}", file=sys.stderr)
        return 1
    print(json.dumps(
        {
            "format_version": FORMAT_VERSION,
            "limit": args.limit,
            "recipe": recipe.description(),
            "rows": result_rows,
        },
        indent=2,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
