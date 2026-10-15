"""Tested C# `try`/typed-`catch` lowering through the managed runtime."""

from __future__ import annotations

import json

from emitter import core


def has_return(em, oid: int) -> bool:
    row = em.con.execute(
        """
        WITH RECURSIVE descendants(id) AS (
            SELECT ?
            UNION ALL
            SELECT child.id FROM operation child
            JOIN descendants parent ON child.parent_id = parent.id
        )
        SELECT 1 FROM operation
        WHERE id IN descendants AND kind='Return'
        LIMIT 1
        """,
        (oid,),
    ).fetchone()
    return row is not None


@core.stmt("Try")
def try_stmt(em, oid: int, indent: int) -> list[str] | None:
    """Emit the smallest proven catch shape; decline all other forms."""
    if not getattr(em, "_emitting_conformance", False):
        return None
    children = em.children(oid)
    if len(children) != 2 or children[0][1] != "Block" or children[1][1] != "CatchClause":
        return None
    body_id, catch_id = children[0][0], children[1][0]
    if has_return(em, body_id):
        return None
    row = em.con.execute(
        "SELECT detail FROM operation WHERE id=?", (catch_id,)
    ).fetchone()
    try:
        detail = json.loads(row[0]) if row and row[0] else {}
    except json.JSONDecodeError:
        return None
    exception_type = detail.get("extype")
    if detail.get("filter") or not isinstance(exception_type, str):
        return None
    catch_children = em.children(catch_id)
    if len(catch_children) != 1 or catch_children[0][1] != "Block":
        return None
    pad = "    " * indent
    body = em.emit_block(body_id, indent + 1)
    handler = em.emit_block(catch_children[0][0], indent + 1)
    return [
        f"{pad}// C# try/catch: typed managed unwind; unmatched exceptions rethrow.",
        f"{pad}let managed_exception = csharp_rt::catch(|| {{",
        *body,
        f"{pad}}});",
        f"{pad}match managed_exception {{",
        f"{pad}    Ok(()) => {{}},",
        f'{pad}    Err(exception) if csharp_rt::is_type(&exception, "{exception_type}") => {{',
        *handler,
        f"{pad}    }},",
        f"{pad}    Err(exception) => csharp_rt::rethrow(exception),",
        f"{pad}}};",
    ]
