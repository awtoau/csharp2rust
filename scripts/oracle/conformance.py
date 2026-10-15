#!/usr/bin/env python3
"""Differential C#-to-Rust conformance runner for static TestEntryPoint cases."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GENERATE = ROOT / "scripts" / "oracle" / "generate_conformance_cases.py"
INGEST_PROJECT = ROOT / "frontend" / "RenodeIngest"
EMITTER = ROOT / "scripts" / "core" / "csharp_emitter.py"
RESULT_SCHEMA = """
CREATE TABLE IF NOT EXISTS conformance_run (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    converter_revision TEXT NOT NULL,
    jobs INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS conformance_result (
    run_id INTEGER NOT NULL REFERENCES conformance_run(id),
    case_path TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    PRIMARY KEY (run_id, case_path)
);
CREATE INDEX IF NOT EXISTS conformance_result_case
    ON conformance_result(case_path, status);
"""


def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True)


def write_csharp_runner(case: Path, sources: list[Path], type_name: str) -> None:
    (case / "Case.csproj").write_text("""<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net9.0</TargetFramework></PropertyGroup>
</Project>
""")
    for source in sources:
        shutil.copy2(source, case / source.name)
    (case / "ConformanceStubs.cs").write_text(
        "namespace Xunit { public sealed class FactAttribute : System.Attribute {} }\n"
        "public sealed class OuterLoopAttribute : System.Attribute {}\n")
    (case / "ConformanceRunner.cs").write_text(
        "try\n"
        "{\n"
        f"    System.Console.WriteLine(\"return:\" + {type_name}.TestEntryPoint());\n"
        "}\n"
        "catch (System.Exception exception)\n"
        "{\n"
        "    System.Console.WriteLine(\"throw:\" + exception.GetType().FullName);\n"
        "}\n")


def write_rust_runner(case: Path, emitted: str) -> None:
    (case / "Cargo.toml").write_text("""[package]
name = "conformance-case"
version = "0.0.0"
edition = "2021"

[dependencies]
csharp-rt = { path = "%RUNTIME_PATH%" }

[workspace]
""".replace("%RUNTIME_PATH%", str(ROOT / "src" / "csharp-rt")))
    src = case / "src"
    src.mkdir()
    (src / "main.rs").write_text(
        emitted
        + "\nfn main() {\n"
          "    match csharp_rt::catch(|| test_entry_point()) {\n"
          "        Ok(value) => println!(\"return:{}\", value),\n"
          "        Err(exception) => println!(\"throw:{}\", exception.type_name()),\n"
          "    }\n"
          "}\n")


def output_value(result: subprocess.CompletedProcess[str]) -> str | None:
    if result.returncode:
        return None
    lines = result.stdout.strip().splitlines()
    if not lines:
        return None
    value = lines[-1]
    return value if value.startswith(("return:", "throw:")) else None


def run_case(entry: tuple[str, str], source_dir: Path, db: Path,
             cases: Path) -> tuple[str, str, str]:
    type_name, relative_source = entry
    source = source_dir / relative_source
    case_name = relative_source.replace("/", "__").removesuffix(".cs")
    case = cases / case_name
    case.mkdir()
    write_csharp_runner(case, [source], type_name)
    csharp = output_value(run(["dotnet", "run", "--project", "Case.csproj"], case))
    if csharp is None:
        return relative_source, "source_failed", "C# entry point produced no tagged observation"
    emission = run([
        sys.executable, str(EMITTER), "--db", str(db),
        "--type", type_name, "--method", "TestEntryPoint", "--static-function",
    ], ROOT)
    if emission.returncode or not emission.stdout.strip():
        return relative_source, "withheld", emission.stderr.strip() or "no emitted Rust"
    write_rust_runner(case, emission.stdout)
    rust = output_value(run(["cargo", "run", "--quiet"], case))
    return relative_source, "agreed" if rust == csharp else "disagreed", ""


def failure_reason(result: subprocess.CompletedProcess[str]) -> str:
    output = (result.stderr or result.stdout).strip().splitlines()
    return output[-1] if output else f"exit status {result.returncode}"


def converter_revision() -> str:
    result = run(["git", "rev-parse", "HEAD"], ROOT)
    if result.returncode:
        raise RuntimeError(f"cannot determine converter revision: {failure_reason(result)}")
    return result.stdout.strip()


def create_result_run(db: Path, source_name: str, source_revision: str,
                      jobs: int) -> tuple[sqlite3.Connection, int]:
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.executescript(RESULT_SCHEMA)
    cursor = con.execute(
        """INSERT INTO conformance_run(
               started_at, source_name, source_revision, converter_revision, jobs)
           VALUES (?, ?, ?, ?, ?)""",
        (datetime.now(timezone.utc).isoformat(), source_name, source_revision,
         converter_revision(), jobs))
    con.commit()
    return con, cursor.lastrowid


def record_results(con: sqlite3.Connection, run_id: int,
                   results: list[tuple[str, str, str, int]]) -> None:
    con.executemany(
        """INSERT INTO conformance_result(
               run_id, case_path, status, reason, duration_ms)
           VALUES (?, ?, ?, ?, ?)""",
        [(run_id, *result) for result in results])
    con.commit()


def timed_case(work) -> tuple[str, str, str, int]:
    started = time.monotonic()
    name, status, reason = work()
    return name, status, reason, round((time.monotonic() - started) * 1000)


def run_runtime_candidate(candidate: dict[str, str], tests: Path,
                          cases: Path) -> tuple[str, str, str]:
    relative_source = candidate["path"]
    case = cases / relative_source.replace("/", "__").removesuffix(".cs")
    sources = case / "sources"
    sources.mkdir(parents=True)
    source = tests / relative_source
    staged_sources = []
    for companion in sorted(source.parent.glob("*.cs")):
        destination = sources / companion.name
        shutil.copy2(companion, destination)
        staged_sources.append(destination)
    db = case / "corpus.db"
    ingested = run([
        "dotnet", "run", "--project", str(INGEST_PROJECT), "--",
        "--source-dir", str(sources), "--source-name", "dotnet-runtime",
        "--source-revision", "manifest", "--db", str(db), "-j", "32",
    ], ROOT)
    if ingested.returncode:
        return relative_source, "ingest_failed", failure_reason(ingested)
    con = sqlite3.connect(db)
    entries = list(con.execute(
        """SELECT t.name FROM type t JOIN member mb ON mb.type_id=t.id
           JOIN method m ON m.member_id=mb.id
           WHERE mb.name='TestEntryPoint' AND mb.is_static=1 AND m.has_body=1
           ORDER BY t.name"""))
    con.close()
    if len(entries) != 1:
        return relative_source, "ambiguous_entry", (
            f"expected one ingested TestEntryPoint, found {len(entries)}")
    type_name = entries[0][0]
    write_csharp_runner(case, staged_sources, type_name)
    csharp = output_value(run(["dotnet", "run", "--project", "Case.csproj"], case))
    if csharp is None:
        return relative_source, "source_failed", "C# entry point produced no tagged observation"
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
    ap.add_argument("--runtime-manifest", type=Path)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--results-db", type=Path)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("-j", "--jobs", type=int, default=32)
    args = ap.parse_args()

    work = ROOT / "tmp" / "conformance"
    source_dir = args.source_dir or work / "generated"
    if args.runtime_manifest and args.source_dir:
        ap.error("--runtime-manifest and --source-dir are mutually exclusive")
    if args.source_dir is None:
        if args.runtime_manifest is None:
            generated = run([sys.executable, str(GENERATE)], ROOT)
            if generated.returncode:
                print(generated.stderr, file=sys.stderr)
                return 1
        else:
            runtime = os.environ.get("DOTNET_RUNTIME_SRC")
            if not runtime:
                ap.error("DOTNET_RUNTIME_SRC must be set for --runtime-manifest")
            manifest = json.loads(args.runtime_manifest.read_text())
            source_dir = Path(runtime) / "src" / "tests"
    source_name = "conformance"
    source_revision = "local"
    if args.runtime_manifest:
        source_name = manifest["source"]
        source_revision = manifest["revision"]

    cases = work / "cases"
    shutil.rmtree(cases, ignore_errors=True)
    cases.mkdir()
    if args.jobs < 1:
        ap.error("--jobs must be positive")
    results_db = args.results_db or work / "results.db"
    result_con, result_run = create_result_run(
        results_db, source_name, source_revision, args.jobs)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
            if args.runtime_manifest:
                results = list(pool.map(
                    lambda candidate: timed_case(
                        lambda: run_runtime_candidate(candidate, source_dir, cases)),
                    manifest["candidates"]))
            else:
                db = work / "corpus.db"
                ingested = run([
                    "dotnet", "run", "--project", str(INGEST_PROJECT), "--",
                    "--source-dir", str(source_dir), "--source-name", source_name,
                    "--source-revision", source_revision, "--db", str(db), "-j", "32",
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
                results = list(pool.map(
                    lambda entry: timed_case(
                        lambda: run_case(entry, source_dir, db, cases)),
                    types))
        results.sort()
        record_results(result_con, result_run, results)
    finally:
        result_con.close()
    statuses = ("agreed", "disagreed", "withheld", "source_failed",
                "ingest_failed", "ambiguous_entry")
    counts = {status: sum(1 for _name, found, _reason, _duration in results if found == status)
              for status in statuses}
    report = {
        "cases": len(results),
        "emitted": counts["agreed"] + counts["disagreed"],
        **counts,
        "reasons": {
            name: reason for name, status, reason, _duration in results if reason
        },
    }
    rendered = json.dumps(report, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n")
    print(rendered)
    if not args.keep:
        shutil.rmtree(cases, ignore_errors=True)
    return 1 if counts["disagreed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
