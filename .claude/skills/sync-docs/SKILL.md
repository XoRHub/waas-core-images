---
name: sync-docs
description: >-
  Check whether README.md, CONTRACT.md and the header comment/docstring
  of each changed toolkit file (.github/actions/**/*.sh, *.py,
  action.yml, .github/workflows/*.yml) still accurately describe
  current behavior after a code change, and fix any drift found.
  Invoke proactively before committing a change that touches the
  pipeline (.github/, images.yaml) or the runtime contract
  (base/rootfs, base/*/Dockerfile), or whenever explicitly asked to
  update docs for a recent change. Do not invoke for changes with no
  doc surface (pure test files).
---

# sync-docs

Every toolkit script and workflow opens with a short header explaining
its flow, the env it consumes and the non-obvious "why" of its design;
README.md mirrors the pipeline (§ Pipeline, § CI toolkit, § Image
metadata) and CONTRACT.md mirrors `base/rootfs`. All of them drift
easily — this skill is the review pass that catches it.

## Procedure

1. **Scope the diff.** `git diff` (uncommitted) or `git show`/`git diff
   HEAD~1` (committed). Look at `.github/`, `images.yaml`,
   `base/*/manifest.yaml`, `base/*/Dockerfile`, `base/rootfs/**`.

2. **For each changed script/workflow/action**, re-read its header or
   `description:` and check it against the new code:
   - flow (step order/count), env vars and inputs it claims to read;
   - tools/images/registries/tags it names;
   - stated rationale — is the constraint still true?
   - cross-references to other files/jobs/actions by name.

3. **For each changed subsystem**, grep README.md and CONTRACT.md for
   the related keywords (job names, action names, inputs, env vars,
   tags, paths) and check those sections the same way. A change under
   `base/rootfs` or a `WAAS_*` variable almost always touches
   CONTRACT.md — and is a semver decision to state in the commit.

4. **Fix only the drift you found.** Terse comments that explain the
   why; no comment where nothing non-obvious would be lost; no new
   sections beyond what the diff changed.

5. **Report concisely**: which files got a doc fix and why, and which
   changed files were checked and found accurate.

## When NOT to touch something

- Purely internal changes with no effect on documented behavior.
- Generated files (`github-matrices.json`, `Dockerfile.generated`,
  `sbom.cdx.json`).
- `ci/tests/`.
