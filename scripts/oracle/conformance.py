#!/usr/bin/env python3
"""Differential C#-to-Rust conformance runner for static TestEntryPoint cases."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GENERATE = ROOT / "scripts" / "oracle" / "generate_conformance_cases.py"
INGEST_PROJECT = ROOT / "frontend" / "RenodeIngest"
EMITTER = ROOT / "scripts" / "core" / "csharp_emitter.py"


def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True)


def write_csharp_runner(case: Path, source: Path, type_name: str) -> None:
    (case / "Case.csproj").write_text("""<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net9.0</TargetFramework></PropertyGroup>
</Project>
""")
    shutil.copy2(source, case / source.name)
    (case / "Xunit.cs").write_text(
        "namespace Xunit { public sealed class FactAttribute : System.Attribute {} }\n"
        "public sealed class OuterLoopAttribute : System.Attribute {}\n")
    (case / "Program.cs").write_text(
        f"System.Console.WriteLine({type_name}.TestEntryPoint());\n")


def write_rust_runner(case: Path, emitted: str) -> None:
    (case / "Cargo.toml").write_text("""[package]
name = "conformance-case"
version = "0.0.0"
edition = "2021"

[workspace]
""")
    src = case / "src"
    src.mkdir()
    (src / "main.rs").write_text(
        emitted + "\nfn main() { println!(\"{}\", test_entry_point()); }\n")


def output_value(result: subprocess.CompletedProcess[str]) -> int | None:
    if result.returncode:
        return None
    lines = result.stdout.strip().splitlines()
    if not lines:
        return None
    try:
        return int(lines[-1])
    except ValueError:
        return None


def run_case(entry: tuple[str, str], source_dir: Path, db: Path,
             cases: Path) -> tuple[str, str, str]:
    type_name, relative_source = entry
    source = source_dir / relative_source
    case_name = relative_source.replace("/", "__").removesuffix(".cs")
    case = cases / case_name
    case.mkdir()
    write_csharp_runner(case, source, type_name)
    csharp = output_value(run(["dotnet", "run", "--project", "Case.csproj"], case))
    if csharp is None:
        return relative_source, "source_failed", "C# entry point did not produce an integer"
    emission = run([
        sys.executable, str(EMITTER), "--db", str(db),
        "--type", type_name, "--method", "TestEntryPoint", "--static-function",
    ], ROOT)
    if emission.returncode or not emission.stdout.strip():
        return relative_source, "withheld", emission.stderr.strip() or "no emitted Rust"
    write_rust_runner(case, emission.stdout)
    rust = output_value(run(["cargo", "run", "--quiet"], case))
    return relative_source, "agreed" if rust == csharp else "disagreed", ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", type=Path)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("-j", "--jobs", type=int, default=32)
    args = ap.parse_args()

    work = ROOT / "tmp" / "conformance"
    source_dir = args.source_dir or work / "generated"
    if args.source_dir is None:
        generated = run([sys.executable, str(GENERATE)], ROOT)
        if generated.returncode:
            print(generated.stderr, file=sys.stderr)
            return 1

    db = work / "corpus.db"
    ingested = run([
        "dotnet", "run", "--project", str(INGEST_PROJECT), "--",
        "--source-dir", str(source_dir), "--source-name", "conformance",
        "--source-revision", "local", "--db", str(db), "-j", "32",
    ], ROOT)
    if ingested.returncode:
        print(ingested.stdout + ingested.stderr, file=sys.stderr)
        return 1

    con = sqlite3.connect(db)
    types = [row for row in con.execute(
        """SELECT t.name, f.path FROM type t JOIN member mb ON mb.type_id=t.id
           JOIN method m ON m.member_id=mb.id
           JOIN file f ON f.id=t.file_id
           WHERE mb.name='TestEntryPoint' AND mb.is_static=1 AND m.has_body=1
           ORDER BY f.path, t.name""")]
    con.close()
    cases = work / "cases"
    shutil.rmtree(cases, ignore_errors=True)
    cases.mkdir()
    if args.jobs < 1:
        ap.error("--jobs must be positive")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(
            lambda entry: run_case(entry, source_dir, db, cases), types))
    results.sort()
    counts = {status: sum(1 for _name, found, _reason in results if found == status)
              for status in ("agreed", "disagreed", "withheld", "source_failed")}
    print(json.dumps({
        "cases": len(types),
        "emitted": counts["agreed"] + counts["disagreed"],
        **counts,
        "reasons": {name: reason for name, status, reason in results if reason},
    }, sort_keys=True))
    if not args.keep:
        shutil.rmtree(cases, ignore_errors=True)
    return 1 if counts["disagreed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
