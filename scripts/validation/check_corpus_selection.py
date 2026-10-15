#!/usr/bin/env python3
"""Prove the emitter never chooses an arbitrary corpus run."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from csharp_emitter import select_corpus_run


def main() -> int:
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE corpus_run (id INTEGER PRIMARY KEY)")

    assert select_corpus_run(con, None) is None
    con.execute("INSERT INTO corpus_run VALUES (7)")
    assert select_corpus_run(con, None) == 7
    assert select_corpus_run(con, 7) == 7

    try:
        select_corpus_run(con, 8)
    except ValueError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("accepted a missing corpus run")

    con.execute("INSERT INTO corpus_run VALUES (9)")
    try:
        select_corpus_run(con, None)
    except ValueError as exc:
        assert "multiple corpus runs" in str(exc)
    else:
        raise AssertionError("accepted an ambiguous corpus")
    assert select_corpus_run(con, 9) == 9

    print("OK: corpus selection is explicit when a database has multiple runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
