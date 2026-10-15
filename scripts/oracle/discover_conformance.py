#!/usr/bin/env python3
"""Discover static integer TestEntryPoint programs in dotnet/runtime."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ENTRY = re.compile(r"\bstatic\s+(?:int|uint|long|bool)\s+TestEntryPoint\s*\(")


def main() -> int:
    source = os.environ.get("DOTNET_RUNTIME_SRC")
    if not source:
        print("DOTNET_RUNTIME_SRC is not set (see .env.example)", file=sys.stderr)
        return 1
    tests = Path(source) / "src" / "tests"
    if not tests.is_dir():
        print(f"dotnet/runtime tests not found: {tests}", file=sys.stderr)
        return 1
    candidates = []
    for path in sorted(tests.rglob("*.cs")):
        if ENTRY.search(path.read_text(errors="replace")):
            candidates.append({
                "path": path.relative_to(tests).as_posix(),
                "directory": path.parent.relative_to(tests).as_posix(),
            })
    revision = subprocess.run(
        ["git", "-C", str(Path(source)), "rev-parse", "HEAD"],
        capture_output=True, text=True)
    doc = {
        "source": "dotnet-runtime",
        "revision": revision.stdout.strip() if revision.returncode == 0 else "unknown",
        "candidates": candidates,
    }
    out = ROOT / "tmp" / "conformance" / "dotnet-runtime-manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(f"discovered {len(candidates)} static TestEntryPoint candidate(s): {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
