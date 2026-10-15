"""C# `throw new Exception(...)` through the managed exception runtime."""

from __future__ import annotations

from emitter import core


@core.stmt("Throw")
def throw_stmt(em, oid: int, indent: int) -> list[str] | None:
    if not getattr(em, "_emitting_conformance", False):
        return None
    children = em.children(oid)
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
    pad = "    " * indent
    return [
        f'{pad}// C# throw: managed exception unwind, not a Rust panic.',
        f'{pad}csharp_rt::throw(csharp_rt::CsException::new('
        f'"{row[1]}", {message}));',
    ]
