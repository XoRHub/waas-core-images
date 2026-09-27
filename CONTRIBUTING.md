# Contributing

This repo builds the `core-*` images every WaaS Linux workspace is
built on, and hosts the CI toolkit both image repos use. Read
`README.md` (pipeline), `CONTRACT.md` (what derived images rely on) and
`HARDENING.md` (what CI enforces) first.

## Prerequisites

Docker with buildx, [uv](https://docs.astral.sh/uv/). hadolint,
shellcheck and actionlint run through docker (`make lint`).

## Local loop

```
make build IMAGE=core-ubuntu-noble   # core-debian-13, core-fedora-43
make run   IMAGE=core-ubuntu-noble   # VNC on localhost:15901, password devpassword
make smoke IMAGE=core-ubuntu-noble   # the CI smoke test: hardening flags, suid sweep, RFB, audio
make lint
make test
```

These wrap the same scripts CI runs (`.github/actions/*/`), so "works
locally" means "works in CI".

## Before opening a PR

- `make lint` and `make test` pass.
- `make build` + `make smoke` pass for every variant your change
  touches — a `base/ubuntu` change touches `core-ubuntu-noble` and
  `core-debian-13`; a `base/rootfs` change touches all three.
- If you changed anything `CONTRACT.md` lists (a `WAAS_*` variable, a
  hook, a path, a tool), update it and state the semver level in the
  PR: removing or renaming is a major, adding is a minor.
- If you changed the toolkit (`.github/actions/`), remember it is
  consumed by `waas-workspace-images` at `actions/v1`: a compatible
  change moves that tag after merge, a breaking one needs `actions/v2`.

CI builds only what your PR impacts (`README.md` § Pipeline) and pushes
`g<sha>` tags for it; nothing is promoted from a PR.

## Releases

`release.yml` — `Actions → release → Run workflow`, choose the bump.
It promotes the already-tested images of the current `main` commit,
signs them, and creates `vX.Y.Z` + the GitHub Release. Patch releases
also happen automatically: weekly after the no-cache rebuild, and after
an automerged Renovate patch/digest PR (label `auto-release`). The
label must exist in the repository for Renovate to apply it.

## Dependency updates

Renovate (`renovate.json`): OS base image digests, the tool images
pinned in the toolkit scripts/workflows/Makefile, and GitHub Actions.
Patch and digest updates automerge once the required checks pass and
trigger a patch release; OS release bumps (`os-bump` label) and any
minor/major update are reviewed by hand.

## Commit messages

Conventional commits, scoped: `feat(base): …`, `fix(ci): …`,
`docs: …`, `chore(deps): …`. See `git log --oneline` for the scopes in
use.
