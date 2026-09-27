# AGENTS.md

Operating rules for AI coding agents in this repository. Read
`README.md`, `CONTRACT.md` and `HARDENING.md` first.

## What this repo is

The `core-*` base images of WaaS Linux workspaces (TigerVNC Xvnc as
the display server and the only remote-access listener, `tini` +
`supervisord`, unprivileged, read-only-rootfs friendly) plus the CI
toolkit (`.github/actions/`) that `waas-workspace-images` consumes at
`actions/v1`. Every `base/rootfs` file and every `WAAS_*` variable
is API: `CONTRACT.md` lists what derived images rely on, and changing
it is a semver decision (remove/rename = major).

## Source of truth vs. generated files

Source: `images.yaml` and one `manifest.yaml` per `base/*` directory
(discovered automatically — adding an image is adding a directory).
Generated, gitignored, never hand-edited: `github-matrices.json`
(`uv run .github/actions/pipeline/generate_pipeline.py`),
`*/Dockerfile.generated` (recipes; none in this repo), `sbom.cdx.json`.
The per-image docs go to the CI job summary only. There is no
`version:` field anywhere: the version is the git tag.

`base/rootfs` is shared by every core through a buildx named context
(manifest `buildContexts:`); a change there rebuilds all of them.

## Non-negotiable constraints

Enforced by CI (smoke test, hadolint, shellcheck, trivy) and by
`HARDENING.md`. Do not weaken them without the user asking explicitly,
and flag it if a change would:

- Add a setuid/setgid binary, or run anything as root past build time.
  Final `USER` stays `1000:1000` (`waas_user`), configurable only via
  the `WAAS_UID`/`WAAS_GID`/`WAAS_USER` build args.
- Require write access outside `/home/waas_user` and `/tmp`.
- Add a second remote-desktop or shell daemon (xrdp, sshd, …) or a
  build arg that could. VNC is the only protocol the platform accepts
  for a Linux workspace (waas#117); the smoke test only ever probes VNC.
- Bake a secret into a layer — passwords arrive via runtime env.
- Install packages without `--no-install-recommends` (dnf:
  `install_weak_deps=False`), a cache purge and a suid-bit re-strip.
- Add a `recipe:` block to a directory that also has a `Dockerfile`
  (the compiler refuses it — one source of truth per image).
- Ship sudo in a core; the reduced `-dev` profile is a derived-image
  variant only.

## Toolkit rules

- One copy of each script, next to its `action.yml`, invoked through
  `${{ github.action_path }}`; shared helpers live in
  `.github/actions/lib.sh`. Never duplicate a script into a workflow.
- `generate_pipeline.py` serves both repos: keep external parents
  (`cores:`), selective builds and carry-forward working for a tree
  with in-pipeline parents even though this repo has none — the tests
  in `ci/tests/` model the workspace tree.
- A change under `.github/actions/` is a change to the workspace
  repo's toolkit: compatible → move the `actions/v1` tag; breaking
  (renamed input, changed script env contract) → `actions/v2`.
- Comments explain a non-obvious *why*; identifiers say the what.

## Verification loop

```
make lint                      # hadolint + shellcheck + actionlint, via docker
make test                      # python3 -m unittest discover -s ci/tests
make build IMAGE=<variant>     # core-ubuntu-noble | core-debian-13 | core-fedora-43
make smoke IMAGE=<variant>     # the same smoke test CI runs
```

Run `make test` before declaring a change to any `.github/actions/**.py`
done, `make lint` for any `.sh`/Dockerfile/workflow change, and
`make build` + `make smoke` for every variant a `base/` change touches.
Before committing a change to the pipeline (`.github/`, `images.yaml`),
run the `sync-docs` skill so README/CONTRACT and script headers stay
accurate.

## Commit style

Conventional commits, scoped: `feat(base): …`, `fix(ci): …`,
`docs: …`, `chore(deps): …`. A change to `CONTRACT.md` says which
semver level it is.
