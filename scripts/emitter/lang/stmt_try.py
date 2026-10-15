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
    """Emit proven typed catches; decline filters and complex control flow."""
    if not getattr(em, "_emitting_conformance", False):
        return None
    children = em.children(oid)
    if len(children) < 2 or children[0][1] != "Block":
        return None
    body_id = children[0][0]
    pad = "    " * indent
    if len(children) == 2 and children[1][1] == "Block":
        finally_id = children[1][0]
        if has_return(em, body_id) or has_return(em, finally_id):
            return None
        body = em.emit_block(body_id, indent + 1)
        final_after = em.emit_block(finally_id, indent)
        final_before_rethrow = em.emit_block(finally_id, indent + 2)
        return [
            f"{pad}// C# try/finally: managed unwind runs cleanup before propagation.",
            f"{pad}let managed_exception = csharp_rt::catch(|| {{",
            *body,
            f"{pad}}});",
            f"{pad}match managed_exception {{",
            f"{pad}    Ok(()) => {{}},",
            f"{pad}    Err(exception) => {{",
            *final_before_rethrow,
            f"{pad}        csharp_rt::rethrow(exception);",
            f"{pad}    }},",
            f"{pad}}};",
            *final_after,
        ]
    catch_nodes = list(children[1:])
    finally_id = None
    if catch_nodes[-1][1] == "Block":
        finally_id = catch_nodes.pop()[0]
    if not catch_nodes or any(node[1] != "CatchClause" for node in catch_nodes):
        return None
    if has_return(em, body_id):
        return None
    catches: list[tuple[str, int]] = []
    for catch_node in catch_nodes:
        catch_id = catch_node[0]
        row = em.con.execute(
            "SELECT detail FROM operation WHERE id=?", (catch_id,)
        ).fetchone()
        try:
            detail = json.loads(row[0]) if row and row[0] else {}
        except json.JSONDecodeError:
            return None
        exception_type = detail.get("extype")
        catch_children = em.children(catch_id)
        if (detail.get("filter") or not isinstance(exception_type, str)
                or len(catch_children) != 1 or catch_children[0][1] != "Block"):
            return None
        catches.append((exception_type, catch_children[0][0]))
    if finally_id is not None and (
            any(has_return(em, catch_body_id) for _, catch_body_id in catches)
            or has_return(em, finally_id)):
        return None
    body = em.emit_block(body_id, indent + 1)
    handlers: list[tuple[str, list[str]]] = []
    for exception_type, catch_body_id in catches:
        previous_caught = getattr(em, "_caught_exception_name", None)
        previous_finally_scope = getattr(em, "_catch_finally_scope", None)
        em._caught_exception_name = "exception"
        em._catch_finally_scope = (
            (finally_id, catch_body_id) if finally_id is not None else None
        )
        try:
            handler = em.emit_block(catch_body_id, indent + 1)
        finally:
            em._caught_exception_name = previous_caught
            em._catch_finally_scope = previous_finally_scope
        handlers.append((exception_type, handler))
    final_after = em.emit_block(finally_id, indent) if finally_id is not None else []
    final_before_rethrow = (
        em.emit_block(finally_id, indent + 2) if finally_id is not None else []
    )
    return [
        f"{pad}// C# try/catch: typed managed unwind; unmatched exceptions rethrow.",
        f"{pad}let managed_exception = csharp_rt::catch(|| {{",
        *body,
        f"{pad}}});",
        f"{pad}match managed_exception {{",
        f"{pad}    Ok(()) => {{}},",
        *[
            line
            for exception_type, handler in handlers
            for line in (
                f'{pad}    Err(exception) if csharp_rt::is_type(&exception, "{exception_type}") => {{',
                *handler,
                f"{pad}    }},",
            )
        ],
        f"{pad}    Err(exception) => {{",
        *final_before_rethrow,
        f"{pad}        csharp_rt::rethrow(exception);",
        f"{pad}    }},",
        f"{pad}}};",
        *final_after,
    ]
