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


def finally_replaces_exception_program(
        name: str, thrown_exception_type: str, finally_exception_type: str) -> str:
    return f"""using Xunit;

public static class {name}
{{
    [Fact]
    public static int TestEntryPoint()
    {{
        try
        {{
            try
            {{
                throw new {thrown_exception_type}("conformance");
            }}
            catch (System.InvalidOperationException)
            {{
            }}
            finally
            {{
                throw new {finally_exception_type}("finally");
            }}
        }}
        catch ({finally_exception_type})
        {{
            return 100;
        }}
        return 101;
    }}
}}
"""


def multiple_catch_program(
        name: str, thrown_exception_type: str, first_catch_type: str,
        second_catch_type: str) -> str:
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
        catch ({first_catch_type})
        {{
            return 101;
        }}
        catch ({second_catch_type})
        {{
            return 100;
        }}
        return 101;
    }}
}}
"""


def finally_without_catch_program(name: str, exception_type: str) -> str:
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


def catch_variable_program(name: str, exception_type: str) -> str:
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
        catch ({exception_type} exception)
        {{
            return exception.Message == "conformance" ? 100 : 101;
        }}
        return 101;
    }}
}}
"""


def custom_exception_inheritance_program(name: str) -> str:
    return f"""using Xunit;

public class {name}BaseException : System.Exception
{{
    public {name}BaseException(string message) : base(message) {{ }}
}}

public class {name}DerivedException : {name}BaseException
{{
    public {name}DerivedException(string message) : base(message) {{ }}
}}

public static class {name}
{{
    [Fact]
    public static int TestEntryPoint()
    {{
        try
        {{
            throw new {name}DerivedException("conformance");
        }}
        catch ({name}BaseException)
        {{
            return 100;
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
    for index, case in enumerate(catalog["finally_replaces_exception_types"]):
        (OUT / f"FinallyReplaces_{index}.cs").write_text(
            finally_replaces_exception_program(
                f"FinallyReplaces_{index}",
                case["thrown"],
                case["finally_thrown"],
            ))
    for index, case in enumerate(catalog["multiple_catch_exception_types"]):
        (OUT / f"MultipleCatch_{index}.cs").write_text(
            multiple_catch_program(
                f"MultipleCatch_{index}",
                case["thrown"],
                case["first_catch"],
                case["second_catch"],
            ))
    for index, exception_type in enumerate(catalog["finally_without_catch_exception_types"]):
        (OUT / f"FinallyWithoutCatch_{index}.cs").write_text(
            finally_without_catch_program(
                f"FinallyWithoutCatch_{index}", exception_type))
    for index, exception_type in enumerate(catalog["catch_variable_exception_types"]):
        (OUT / f"CatchVariable_{index}.cs").write_text(
            catch_variable_program(f"CatchVariable_{index}", exception_type))
    for index in range(catalog["custom_exception_inheritance_cases"]):
        (OUT / f"CustomExceptionInheritance_{index}.cs").write_text(
            custom_exception_inheritance_program(
                f"CustomExceptionInheritance_{index}"))
    print(f"generated {len(cases)} C# conformance input program(s) in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
