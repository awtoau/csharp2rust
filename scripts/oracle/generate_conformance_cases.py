#!/usr/bin/env python3
"""Generate deterministic C# boundary programs for differential conformance."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "tests" / "conformance" / "boundary_cases.json"
OUT = ROOT / "tmp" / "conformance" / "generated"


def program(name: str, expression: str) -> str:
    return f"""using Xunit;

public static class {name}
{{
    [Fact]
    public static int TestEntryPoint()
    {{
        return ({expression}) ? 100 : 101;
    }}
}}
"""


def main() -> int:
    catalog = json.loads(CATALOG.read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    cases: list[tuple[str, str]] = []
    for index, value in enumerate(catalog["integer_literals"]):
        cases.append((f"Integer_{index}", f"({value}) == ({value})"))
    for index, value in enumerate(catalog["boolean_literals"]):
        cases.append((f"Boolean_{index}", value))
    for index, value in enumerate(catalog["string_literals"]):
        escaped = json.dumps(value)[1:-1]
        cases.append((f"String_{index}", f"\"{escaped}\" == \"{escaped}\""))
    for name, expression in cases:
        (OUT / f"{name}.cs").write_text(program(name, expression))
    print(f"generated {len(cases)} C# conformance input program(s) in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
