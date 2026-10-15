#!/usr/bin/env python3
"""Per-module rustc error counts from the scratch crate `compile_check.py --keep` leaves.

Usage: python3 scripts/validation/compile_modules.py --out tmp/permod-new.json
       python3 scripts/validation/compile_modules.py --compare tmp/permod-old.json tmp/permod-new.json
Why: `compile_check.py --ratchet` aborts before the per-module gate when a declared-clean
module was not emitted, so it names no regressions. This reads the same cargo output directly.
Log: tmp/logs/compile_modules.log
"""
import argparse
import collections
import json
import subprocess
import sys
from pathlib import Path


def repo() -> Path:
    return Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True).strip())


def measure(crate: Path) -> dict:
    proc = subprocess.run(["cargo", "check", "--message-format=json", "--quiet"],
                          cwd=crate, capture_output=True, text=True)
    if not proc.stdout.strip():
        raise SystemExit("cargo produced no output; not a clean build")
    per = collections.Counter()
    codes = collections.Counter()
    for line in proc.stdout.splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        d = m.get("message", {})
        if m.get("reason") != "compiler-message" or d.get("level") != "error":
            continue
        for s in d.get("spans", []):
            if s.get("file_name", "").endswith(".rs"):
                per[Path(s["file_name"]).stem] += 1
                codes[(d.get("code") or {}).get("code") or "no-code"] += 1
                break
    mods = sorted(p.stem for p in (crate / "src").rglob("*.rs"))
    return {"modules": mods, "errors": dict(per), "codes": dict(codes)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--crate", default="tmp/compile_check")
    ap.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"))
    a = ap.parse_args()
    root = repo()
    lines = []
    if a.compare:
        o, n = (json.loads(Path(p).read_text()) for p in a.compare)
        mo, mn = set(o["modules"]), set(n["modules"])
        co, cn = mo - set(o["errors"]), mn - set(n["errors"])
        both = mo & mn
        lines.append(f"modules old {len(mo)} new {len(mn)}; clean old {len(co)} new {len(cn)}")
        lines.append(f"modules only in old: {sorted(mo - mn)}")
        lines.append(f"modules only in new: {sorted(mn - mo)}")
        lines.append(f"REGRESSED (clean old, errors new): "
                     f"{sorted((m, n['errors'][m]) for m in both & co if m in n['errors'])}")
        lines.append(f"NEWLY CLEAN (errors old, clean new): "
                     f"{sorted(m for m in both & cn if m in o['errors'])}")
        lines.append(f"new modules with errors: "
                     f"{sorted((m, n['errors'][m]) for m in mn - mo if m in n['errors'])}")
        lines.append(f"error codes old {o['codes']} new {n['codes']}")
    else:
        res = measure(root / a.crate)
        Path(root / a.out).write_text(json.dumps(res, indent=1))
        lines.append(f"{len(res['modules'])} modules, {len(res['errors'])} with errors, "
                     f"{sum(res['errors'].values())} errors")
    log = root / "tmp" / "logs" / "compile_modules.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
