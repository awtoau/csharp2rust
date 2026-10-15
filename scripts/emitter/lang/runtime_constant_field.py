"""Whitelisted BCL static readonly fields with language-defined values."""

from __future__ import annotations

from emitter import core


@core.expr("FieldReference", priority=10)
def runtime_constant_field(em, oid):
    """Fold only data-defined BCL constants verified against Roslyn's value."""
    if not getattr(em, "_emitting_conformance", False):
        return None
    row = em.con.execute(
        "SELECT symbol, const_value, type FROM operation WHERE id=?", (oid,)
    ).fetchone()
    if not row:
        return None
    symbol, const, rtype = row
    values = em.language.get("references", {}).get(
        "RuntimeConstantField", {}
    ).get("values", {})
    expected = values.get(symbol)
    if expected is None or const != expected:
        return None
    return em.literal(const, rtype)
