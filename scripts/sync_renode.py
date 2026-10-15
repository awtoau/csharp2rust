#!/usr/bin/env python3
"""Pin a worktree to an upstream Renode tag and re-ingest the corpus from it.

Issue #80. Wraps three gotchas a plain `git worktree add` + `dotnet run` hits,
discovered doing this by hand against v1.17.0:

1. `git worktree add` does not init submodules. `src/Infrastructure` (where
   the actual C# lives) is one, so the worktree is empty there until
   `git submodule update --init --recursive` runs inside it.
2. `Infrastructure.csproj` targets `net6.0`, but its own `GirCore.Gst-1.0`
   pin (0.7.0) only ships net8.0/9.0/10.0 assets -- restore fails with
   "GirCore.Gst-1.0 0.7.0 is not compatible with net6.0" otherwise. Renode's
   own `build.sh` papers over this at build time by generating a
   `Directory.Build.targets` that overrides TargetFrameworks to `$TFM`
   (currently net8.0) -- that file is gitignored, so a bare worktree lacks
   it. This script reproduces just that one override, read out of `build.sh`
   itself so it tracks upstream rather than drifting from a hardcoded value.
3. Without (1)+(2), MSBuildWorkspace fails to resolve `System` itself (0
   metadata refs) and the ingest "succeeds" having walked nothing real --
   it does not error loudly, so this is easy to miss.

Run:  python3 scripts/sync_renode.py --tag v1.17.0
Log:  ./tmp/logs/sync_renode.log
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_STORE = "/mnt/2tb/git_mirror/renode"


def repo_root() -> Path:
    return Path(subprocess.run(["git", "rev-parse", "--show-toplevel"],
                               capture_output=True, text=True, check=True).stdout.strip())


def read_env(root: Path, key: str) -> str | None:
    f = root / ".env"
    if not f.exists():
        return None
    for line in f.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def write_env(root: Path, key: str, value: str) -> None:
    f = root / ".env"
    lines = f.read_text().splitlines() if f.exists() else []
    lines = [l for l in lines if not l.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    f.write_text("\n".join(lines) + "\n")


def sh(log: logging.Logger, argv: list[str], **kw) -> subprocess.CompletedProcess:
    log.info("+ %s", " ".join(argv))
    return subprocess.run(argv, check=True, **kw)


def tfm_from_build_sh(store: Path) -> str:
    """Read the TFM build.sh overrides to, so this tracks upstream rather
    than hardcoding a value that silently goes stale."""
    m = re.search(r'^TFM="([^"]+)"', (store / "build.sh").read_text(), re.M)
    if not m:
        raise SystemExit("build.sh: could not find TFM=\"...\" -- upstream build.sh changed shape")
    return m.group(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True, help="upstream tag to pin to, e.g. v1.17.0")
    ap.add_argument("--store", default=None,
                     help=f"shared Renode mirror (default: $RENODE_STORE or {DEFAULT_STORE})")
    ap.add_argument("--db", default=None,
                     help="output corpus db (default: tmp/patterns-<tag>.db, "
                          "NEVER rulesdb/patterns.db -- diff before replacing the canonical one)")
    args = ap.parse_args()

    root = repo_root()
    (root / "tmp" / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                         handlers=[logging.FileHandler(root / "tmp" / "logs" / "sync_renode.log", mode="w"),
                                   logging.StreamHandler()])
    log = logging.getLogger("sync_renode")

    if args.db and Path(args.db).name == "patterns.db" and "rulesdb" in args.db:
        log.error("refusing to write directly to the canonical rulesdb/patterns.db -- "
                   "diff the scratch db first (see issue #80)")
        return 1

    store = Path(args.store or os.environ.get("RENODE_STORE") or DEFAULT_STORE)
    if not (store / ".git").exists():
        log.error("not a git store: %s", store)
        return 1

    worktree = root / "tmp" / f"renode-{args.tag}"
    db = root / (args.db or f"tmp/patterns-{args.tag}.db")

    sh(log, ["git", "-C", str(store), "fetch", "--no-tags", "origin", "tag", args.tag])

    existing = subprocess.run(["git", "-C", str(store), "worktree", "list"],
                              capture_output=True, text=True, check=True).stdout
    if str(worktree) not in existing:
        sh(log, ["git", "-C", str(store), "worktree", "add", str(worktree), args.tag])
    else:
        log.info("worktree already present: %s", worktree)

    sh(log, ["git", "-C", str(worktree), "submodule", "update", "--init", "--recursive",
             "--", "src/Infrastructure"])

    tfm = tfm_from_build_sh(worktree)
    (worktree / "Directory.Build.targets").write_text(
        "<Project>\n"
        "  <PropertyGroup>\n"
        f"    <TargetFrameworks>{tfm}</TargetFrameworks>\n"
        "    <EnableWindowsTargeting>true</EnableWindowsTargeting>\n"
        "  </PropertyGroup>\n"
        "</Project>\n")
    log.info("wrote Directory.Build.targets (TargetFrameworks=%s, read from build.sh)", tfm)

    project = worktree / "src" / "Infrastructure" / "src" / "Infrastructure.csproj"
    sh(log, ["dotnet", "restore", str(project)])

    env = dict(os.environ, RENODE_SRC=str(worktree))
    sh(log, ["dotnet", "run", "--project", "frontend/RenodeIngest", "--",
             "--db", str(db)], cwd=root, env=env)

    write_env(root, "RENODE_SRC", str(worktree))
    log.info("done. corpus at %s; RENODE_SRC in .env now points at %s", db, worktree)
    log.info("next: diff against the current corpus (scripts/analysis/*), then "
             "compile_check.py --ratchet with --db pointed at the new corpus "
             "before replacing rulesdb/patterns.db")
    return 0


if __name__ == "__main__":
    sys.exit(main())
