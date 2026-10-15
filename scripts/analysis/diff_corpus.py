#!/usr/bin/env python3
"""Diff two corpus databases: row counts per table, files and types added/removed/changed.

Usage: python3 scripts/analysis/diff_corpus.py [--old rulesdb/patterns-old.db] [--new rulesdb/patterns.db]
Log:   tmp/logs/diff_corpus.log
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def repo() -> Path:
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True).strip())


def tables(c):
    return [r[0] for r in c.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by 1")]


def main() -> int:
    root = repo()
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default=str(root / "rulesdb" / "patterns-old.db"))
    ap.add_argument("--new", default=str(root / "rulesdb" / "patterns.db"))
    ap.add_argument("--json", default=str(root / "tmp" / "diff_corpus.json"))
    a = ap.parse_args()
    out = []

    def say(s=""):
        print(s)
        out.append(s)

    old, new = sqlite3.connect(a.old), sqlite3.connect(a.new)
    for label, c in (("old", old), ("new", new)):
        say(f"{label}: " + str(c.execute(
            "select renode_commit, started_at from corpus_run").fetchall()))

    say("\n== row counts per table ==")
    say(f"{'table':22}{'old':>12}{'new':>12}{'delta':>12}")
    counts = {}
    for t in sorted(set(tables(old)) | set(tables(new))):
        o = old.execute(f"select count(*) from {t}").fetchone()[0] if t in tables(old) else None
        n = new.execute(f"select count(*) from {t}").fetchone()[0] if t in tables(new) else None
        counts[t] = (o, n)
        d = "" if o is None or n is None else f"{n - o:+d}"
        say(f"{t:22}{o!s:>12}{n!s:>12}{d:>12}")

    files_o = dict(old.execute("select path, sha256 from file"))
    files_n = dict(new.execute("select path, sha256 from file"))
    f_add = sorted(set(files_n) - set(files_o))
    f_rem = sorted(set(files_o) - set(files_n))
    f_chg = sorted(p for p in set(files_o) & set(files_n) if files_o[p] != files_n[p])
    say(f"\n== files: +{len(f_add)} -{len(f_rem)} changed {len(f_chg)} unchanged "
        f"{len(set(files_o) & set(files_n)) - len(f_chg)} ==")

    q = ("select t.key, t.kind, f.path from type t join file f on f.id=t.file_id")
    types_o = {k: (kind, p) for k, kind, p in old.execute(q)}
    types_n = {k: (kind, p) for k, kind, p in new.execute(q)}
    t_add = sorted(set(types_n) - set(types_o))
    t_rem = sorted(set(types_o) - set(types_n))
    t_mov = sorted(k for k in set(types_o) & set(types_n) if types_o[k] != types_n[k])
    changed = set(f_chg)
    t_chg = sorted(k for k in set(types_o) & set(types_n) if types_n[k][1] in changed)
    say(f"== types: +{len(t_add)} -{len(t_rem)} moved/kind-changed {len(t_mov)} "
        f"in-changed-files {len(t_chg)} ==")

    # per-type structural signature: members + method signatures
    def sig(c):
        d = {}
        for key, name, kind, sg in c.execute(
            "select t.key, m.name, m.kind, coalesce(me.signature,'') from member m "
            "join type t on t.id=m.type_id left join method me on me.member_id=m.id"):
            d.setdefault(key, set()).add((name, kind, sg))
        return d
    try:
        so, sn = sig(old), sig(new)
        s_chg = sorted(k for k in set(so) & set(sn) if so[k] != sn[k])
        say(f"== types with changed member/method signatures: {len(s_chg)} ==")
    except sqlite3.Error as e:
        s_chg = []
        say(f"(member signature diff skipped: {e})")

    for title, lst in (("files added", f_add), ("files removed", f_rem),
                       ("types added", t_add), ("types removed", t_rem),
                       ("types moved/kind-changed", t_mov),
                       ("types with changed signatures", s_chg)):
        say(f"\n-- {title} ({len(lst)}) --")
        for x in lst:
            say("  " + x)

    p = Path(a.json)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"counts": counts, "files_added": f_add, "files_removed": f_rem,
                             "files_changed": f_chg, "types_added": t_add,
                             "types_removed": t_rem, "types_moved": t_mov,
                             "types_sig_changed": s_chg}, indent=1))
    log = root / "tmp" / "logs" / "diff_corpus.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
