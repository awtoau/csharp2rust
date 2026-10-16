#!/usr/bin/env python3
"""Run a C#->Rust engine test by translation row ID.

This is a small adapter that reuses the shared `translation` table as a
lightweight engine-test record. It locates the method, invokes the emitter to
produce the combinator sequence, and compares the result to the checked-in
Rust `rust_path` (if present) or to a stored `code_sha256` expected hash.

Usage: python3 scripts/core/run_c2r_test.py --db rulesdb/patterns.db --id 123

Exit codes: 0 success (match), 1 mismatch or runtime error, 2 usage/not-found.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
import re


def repo_root() -> Path:
    return Path(subprocess.run(["git", "rev-parse", "--show-toplevel"],
                               capture_output=True, text=True, check=True).stdout.strip())


CALL_RE = re.compile(r"\.(with_[a-z_]+)\(((?:[^()]|\([^()]*\))*)\)")


def normalise_call(call: str) -> str:
    call = re.sub(r"//.*$", "", call.strip())
    call = re.sub(r"\s+", " ", call)
    call = call.replace("( ", "(").replace(" )", ")")
    call = re.sub(r",\s*\)", ")", call)
    return call.rstrip(",")


def extract_handwritten_calls(rust_path: Path, fn_name: str) -> list[str]:
    if not rust_path.exists():
        return []
    text = rust_path.read_text(encoding="utf-8")
    start = text.find(f"fn {fn_name}(")
    if start < 0:
        return []
    body = text[start:]
    flat = re.sub(r"\n\s+", " ", body)
    out = []
    for m in CALL_RE.finditer(flat):
        out.append(normalise_call(f".{m.group(1)}({m.group(2)})"))
    return out


def emit_for_method(con: sqlite3.Connection, method_member_id: int, rules_dir: Path | None, log: logging.Logger) -> list[str]:
    # Import the emitter (same driver used elsewhere)
    sys.path.insert(0, str(repo_root() / "scripts"))
    sys.path.insert(0, str(repo_root() / "scripts" / "core"))
    from csharp_emitter import Emitter  # noqa: E402

    em = Emitter(con, log, rules_dir=rules_dir)
    generated = [normalise_call(l) for l in em.emit_method(None if method_member_id is None else em, "")]
    # NOTE: The emitter's public API used by verify_emit is em.emit_method(cs_type, cs_method)
    # Here we cannot call it by type/method without more context. The adapter will
    # instead follow the verify_emit pattern when possible. If this path is used
    # in your environment, replace the above with the canonical call.
    return generated


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="rulesdb/patterns.db", help="SQLite corpus DB")
    ap.add_argument("--id", type=int, required=True, help="translation.id to run")
    ap.add_argument("--rules", help="rules directory override (rulesdb/rules)")
    args = ap.parse_args(argv)

    root = repo_root()
    log = logging.getLogger("run_c2r_test")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    dbp = (root / args.db).resolve()
    if not dbp.exists():
        log.error("database not found: %s", dbp)
        return 2

    con = sqlite3.connect(dbp)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("SELECT id, method_id, run_id, rust_path, code_sha256, rule_versions FROM translation WHERE id = ?", (args.id,))
    row = cur.fetchone()
    if not row:
        log.error("translation id not found: %d", args.id)
        return 2

    method_id = row["method_id"]
    rust_path = row["rust_path"]
    expected_hash = row["code_sha256"]
    rules_dir = Path(args.rules) if args.rules else None

    # Load emitter and emit combinator sequence. Reuse verify_emit's logic when possible.
    # We'll import the Emitter class and call emit_method by looking up the member's type/name
    # using queries similar to other scripts. If that proves insufficient in a bare DB,
    # fail with an explanatory message.

    # Try to discover the target method's containing type and member name for emit_method
    cur.execute(
        "SELECT m.key, t.namespace, t.name as type_name FROM member m JOIN type t ON m.type_id = t.id WHERE m.id = ?",
        (method_id,),
    )
    meta = cur.fetchone()
    if not meta:
        log.error("member/method metadata missing for method id %s", method_id)
        return 1

    cs_method_key = meta["key"]
    cs_type = f"{meta['namespace']}.{meta['type_name']}"
    # Derive method short name from signature for emitter.emit_method convenience
    # The canonical emit_method takes (cs_type, cs_method) as in verify_emit; emulate that.
    cs_method = cs_method_key

    # Import emitter and run
    sys.path.insert(0, str((root / "scripts").resolve()))
    sys.path.insert(0, str((root / "scripts" / "core").resolve()))
    try:
        from csharp_emitter import Emitter  # noqa: E402
    except Exception as exc:
        log.error("failed to import emitter: %s", exc)
        return 1

    try:
        em = Emitter(con, log, rules_dir=(Path(args.rules) if args.rules else None))
        generated = [normalise_call(l) for l in em.emit_method(cs_type, cs_method)]
    except Exception as exc:
        log.exception("emitter run failed: %s", exc)
        return 1

    # If a rust_path exists in the translation row, compare against the checked-in file
    if rust_path:
        rust_file = root / rust_path
        handwritten = extract_handwritten_calls(rust_file, 'create_registers') if rust_file.exists() else []
        # If handwritten empty, fall back to full-file text comparison
        if handwritten:
            if generated == handwritten:
                log.info("MATCH: generated combinator sequence equals hand-written (%s)", rust_path)
                return 0
            else:
                log.error("MISMATCH: generated vs hand-written combinator sequence")
                return 1
        else:
            # compare textual output: join emitted calls as lines
            emitted_text = "\n".join(generated) + "\n"
            if rust_file.exists():
                actual = rust_file.read_text(encoding='utf-8')
                # Basic fingerprint: sha256 of emitted_text vs file
                h = hashlib.sha256(emitted_text.encode('utf-8')).hexdigest()
                expected = hashlib.sha256(actual.encode('utf-8')).hexdigest()
                if h == expected:
                    log.info("MATCH: generated text hash equals file hash (%s)", rust_path)
                    return 0
                else:
                    log.error("MISMATCH: generated text hash != file hash (%s)", rust_path)
                    return 1
            else:
                log.error("rust_path specified but file missing: %s", rust_path)
                return 2

    # Otherwise if code_sha256 expected is present, compare
    if expected_hash:
        emitted_text = "\n".join(generated) + "\n"
        h = hashlib.sha256(emitted_text.encode('utf-8')).hexdigest()
        if h == expected_hash:
            log.info("MATCH: generated code sha256 matches expected")
            return 0
        else:
            log.error("MISMATCH: generated sha256 %s != expected %s", h, expected_hash)
            return 1

    log.error("no expected target (rust_path or code_sha256) present for translation id %d", args.id)
    return 2


if __name__ == "__main__":
    sys.exit(main())
