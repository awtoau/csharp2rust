# csharp2rust project rules

Load chain: this file → the global awto agent rules.

This repository is currently a placeholder. The implementation lives in
`awto-renode-rs`; its `CLAUDE.md` and `PLAN.md` are the authoritative rules
until the extraction happens, and those rules move here with the code.

## Assume this repo will be made public

It is private today only because it is empty. Write every tracked file as
though it were already public:

- **No absolute paths.** Reference repos by name and files by repo-relative
  path. Scripts resolve the workspace with `git rev-parse --show-toplevel` or
  `Path(__file__).parents[N]`, never a hardcoded root. External trees come from
  environment variables documented in a tracked `.env.example`.
- **No private-work references.** No names of private repos, products,
  customers, or hardware programs. Corpora are described generically.

## The one rule that matters

**BUILD THE CONVERTER. DO NOT WRITE THE OUTPUT BY HAND.**

When asked to translate something, the task is to make the converter able to
produce it, not to produce it. If the converter cannot yet emit a construct,
the correct output is a gap the converter reports — never a hand-written
stand-in that looks finished. This has already gone wrong once in
`awto-renode-rs`: two hand-written peripherals passed their traces and their
mutation testing, and regenerating found invented code that no test could see
because it was behaviourally inert.

## Nothing project-specific in the transpiler source

The whole point of this repository existing separately from its first corpus.
The language mapping is data. A mapping that needs knowledge of a particular
codebase is a project rule belonging to that project, not to this one.

## Tooling

- Scripts → `scripts/<name>.py`, committed. No shell scripts.
- Logs → `tmp/logs/<name>.log`, stdout+stderr. `tmp/` is untracked.
