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


def throwing_program(name: str, exception_type: str) -> str:
    return f"""using Xunit;

public static class {name}
{{
    [Fact]
    public static int TestEntryPoint()
    {{
        throw new {exception_type}("conformance");
    }}
}}
"""


def caught_throwing_program(
        name: str, thrown_exception_type: str, caught_exception_type: str) -> str:
    return f"""using Xunit;

public static class {name}
{{
    [Fact]
    public static int TestEntryPoint()
    {{
        try
        {{
            throw new {thrown_exception_type}("conformance");
        }}
        catch ({caught_exception_type})
        {{
            return 100;
        }}
        return 101;
    }}
}}
"""


def finally_throwing_program(
        name: str, thrown_exception_type: str, caught_exception_type: str) -> str:
    return f"""using Xunit;

public static class {name}
{{
    [Fact]
    public static int TestEntryPoint()
    {{
        var value = 0;
        try
        {{
            throw new {thrown_exception_type}("conformance");
        }}
        catch ({caught_exception_type})
        {{
            value = 100;
        }}
        finally
        {{
            value += 1;
        }}
        return value;
    }}
}}
"""


def rethrowing_program(name: str, exception_type: str) -> str:
    return f"""using Xunit;

public static class {name}
{{
    [Fact]
    public static int TestEntryPoint()
    {{
        try
        {{
            throw new {exception_type}("conformance");
        }}
        catch ({exception_type})
        {{
            throw;
        }}
        return 101;
    }}
}}
"""


def unmatched_finally_program(
        name: str, thrown_exception_type: str, inner_catch_exception_type: str,
        outer_catch_exception_type: str) -> str:
    return f"""using Xunit;

public static class {name}
{{
    [Fact]
    public static int TestEntryPoint()
    {{
        var value = 0;
        try
        {{
            try
            {{
                throw new {thrown_exception_type}("conformance");
            }}
            catch ({inner_catch_exception_type})
            {{
                value = 101;
            }}
            finally
            {{
                value = 100;
            }}
        }}
        catch ({outer_catch_exception_type})
        {{
            return value;
        }}
        return 101;
    }}
}}
"""


def rethrowing_finally_program(name: str, exception_type: str) -> str:
    return f"""using Xunit;

public static class {name}
{{
    [Fact]
    public static int TestEntryPoint()
    {{
        var value = 0;
        try
        {{
            try
            {{
                throw new {exception_type}("conformance");
            }}
            catch ({exception_type})
            {{
                throw;
            }}
            finally
            {{
                value = 100;
            }}
        }}
        catch ({exception_type})
        {{
            return value;
        }}
        return 101;
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
    for index, exception_type in enumerate(catalog["exception_types"]):
        (OUT / f"Throw_{index}.cs").write_text(
            throwing_program(f"Throw_{index}", exception_type))
    for index, exception_type in enumerate(catalog["caught_exception_types"]):
        (OUT / f"Catch_{index}.cs").write_text(
            caught_throwing_program(f"Catch_{index}", exception_type, exception_type))
    for index, case in enumerate(catalog["base_catch_exception_types"]):
        (OUT / f"CatchBase_{index}.cs").write_text(
            caught_throwing_program(
                f"CatchBase_{index}", case["thrown"], case["caught"]))
    for index, case in enumerate(catalog["unmatched_catch_exception_types"]):
        (OUT / f"CatchUnmatched_{index}.cs").write_text(
            caught_throwing_program(
                f"CatchUnmatched_{index}", case["thrown"], case["caught"]))
    for index, case in enumerate(catalog["finally_exception_types"]):
        (OUT / f"Finally_{index}.cs").write_text(
            finally_throwing_program(
                f"Finally_{index}", case["thrown"], case["caught"]))
    for index, exception_type in enumerate(catalog["rethrow_exception_types"]):
        (OUT / f"Rethrow_{index}.cs").write_text(
            rethrowing_program(f"Rethrow_{index}", exception_type))
    for index, case in enumerate(catalog["unmatched_finally_exception_types"]):
        (OUT / f"FinallyUnmatched_{index}.cs").write_text(
            unmatched_finally_program(
                f"FinallyUnmatched_{index}",
                case["thrown"],
                case["inner_caught"],
                case["outer_caught"],
            ))
    for index, exception_type in enumerate(catalog["rethrow_finally_exception_types"]):
        (OUT / f"FinallyRethrow_{index}.cs").write_text(
            rethrowing_finally_program(f"FinallyRethrow_{index}", exception_type))
    print(f"generated {len(cases)} C# conformance input program(s) in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
