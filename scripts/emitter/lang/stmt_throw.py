"""C# `throw new Exception(...)` through the managed exception runtime."""

from __future__ import annotations

from emitter import core


def exception_base_types(em, type_name: str) -> list[str]:
    row = em.con.execute(
        "SELECT base_type_id, base_extern FROM type WHERE key=?", (type_name,)
    ).fetchone()
    if row is None:
        return []
    base_type_id, base_extern = row
    bases: list[str] = []
    while base_type_id is not None:
        row = em.con.execute(
            "SELECT key, base_type_id, base_extern FROM type WHERE id=?",
            (base_type_id,),
        ).fetchone()
        if row is None:
            return []
        base_name, base_type_id, base_extern = row
        bases.append(base_name)
    if base_extern:
        bases.append(base_extern)
    return bases


def enclosing_finally(em, oid: int, indent: int) -> list[str]:
    scope = getattr(em, "_catch_finally_scope", None)
    if scope is None:
        return []
    finally_id, catch_body_id = scope
    row = em.con.execute(
        "SELECT parent_id FROM operation WHERE id=?", (oid,)
    ).fetchone()
    if row is None or row[0] != catch_body_id:
        return []
    return em.emit_block(finally_id, indent)


@core.stmt("Throw")
def throw_stmt(em, oid: int, indent: int) -> list[str] | None:
    if not getattr(em, "_emitting_conformance", False):
        return None
    children = em.children(oid)
    if not children:
        caught = getattr(em, "_caught_exception_name", None)
        if not caught:
            return None
        pad = "    " * indent
        return [
            *enclosing_finally(em, oid, indent),
            f"{pad}// C# throw;: rethrow the active managed exception.",
            f"{pad}csharp_rt::rethrow({caught});",
        ]
    if len(children) != 1:
        return None
    exception_id = children[0][0]
    while True:
        row = em.con.execute(
            "SELECT kind FROM operation WHERE id=?", (exception_id,)
        ).fetchone()
        if not row or row[0] != "Conversion":
            break
        converted = em.children(exception_id)
        if len(converted) != 1:
            return None
        exception_id = converted[0][0]
    row = em.con.execute(
        "SELECT kind, type FROM operation WHERE id=?", (exception_id,)
    ).fetchone()
    if not row or row[0] != "ObjectCreation" or not row[1]:
        return None
    arguments = [child[0] for child in em.children(exception_id)
                 if child[1] == "Argument"]
    message = em.emit_expr(arguments[0]) if arguments else '""'
    base_types = exception_base_types(em, row[1])
    exception = f'csharp_rt::CsException::new("{row[1]}", {message})'
    if base_types:
        rendered_bases = ", ".join(f'"{base}"' for base in base_types)
        exception += f".with_base_types([{rendered_bases}])"
    pad = "    " * indent
    return [
        *enclosing_finally(em, oid, indent),
        f'{pad}// C# throw: managed exception unwind, not a Rust panic.',
        f"{pad}csharp_rt::throw({exception});",
    ]
