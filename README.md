# waas-core-images

The `core-*` base images of WaaS Linux workspaces, and the CI toolkit
that builds every WaaS image. A core is a headless graphical session
over VNC — TigerVNC's Xvnc as the display server, openbox fallback,
unprivileged PulseAudio for guacd's audio stream, `tini` +
`supervisord`, non-root, read-only-rootfs friendly. Desktops and apps
live in `waas-workspace-images` and build `FROM` a core pinned by
digest.

```
base/ubuntu          core-ubuntu-noble, core-debian-13 (one OS-parameterized Dockerfile)
base/fedora          core-fedora-43 (dnf sibling)
base/rootfs          entrypoint, session, supervisord and audio config shared by every core
images.yaml          layers, OS matrix, default archs/build args, trivy gate
CONTRACT.md          the runtime API derived images rely on (semver'd)
HARDENING.md         verifiable hardening checklist + threat model
.github/actions/     shared CI toolkit (composite actions), consumed by both repos
.github/workflows/   build, release, rebuild-weekly
ci/tests/            unit tests of the toolkit's Python
```

## Images

`ghcr.io/xorhub/waas-core-images/{core-ubuntu-noble,core-debian-13,core-fedora-43}`,
`linux/amd64` + `linux/arm64` manifest lists. Tags: `X.Y.Z`
(immutable release), `X.Y`, `latest`, `edge` (latest green `main`),
`g<sha>` (one `main` commit). Consumers pin `X.Y.Z@sha256:…`.

Run one standalone under the same constraints WaaS enforces:

```shell
docker run --rm -it \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp --tmpfs /home/waas_user:mode=1777 \
  -p 5901:5901 -e WAAS_DESKTOP_PASSWORD=changeme \
  ghcr.io/xorhub/waas-core-images/core-ubuntu-noble:latest
```

Verify a release (keyless cosign; the SBOM is attested to the digest):

```shell
cosign verify ghcr.io/xorhub/waas-core-images/core-ubuntu-noble:1.0.0 \
  --certificate-identity-regexp 'github.com/XoRHub/waas-core-images' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
cosign verify-attestation --type cyclonedx ghcr.io/xorhub/waas-core-images/core-ubuntu-noble:1.0.0 \
  --certificate-identity-regexp 'github.com/XoRHub/waas-core-images' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The runtime contract — user, writable paths, `WAAS_*` variables, ports,
session modes, hooks — is `CONTRACT.md`. The short version: port 5901,
`WAAS_DESKTOP_PASSWORD` required, everything else optional.

## Pipeline

`build.yml` runs on every PR and `main` push:

1. **setup** — the `pipeline` action reads `images.yaml` + every
   `<layer>/*/manifest.yaml` and decides what to build. Against the
   last *green* push build of `main` (so a failed run leaves nothing
   unbuilt) or the PR base, a variant is rebuilt iff its directory or
   one of its `buildContexts:` changed, its in-pipeline parent is
   rebuilt, its `cores:` entry changed, or a shared input changed
   (`images.yaml` outside `cores:`, anything under `.github/`) — then
   everything is. Manual and scheduled runs rebuild everything. `main`
   runs are queued (carry-forward reads the `edge` the previous run
   moved); PR runs supersede each other.
2. **carry** — variants not rebuilt get their current `edge` digest
   retagged `g<sha>` (zero build), so every `main` commit carries the
   full set.
3. **layer-0** — per image and per arch, natively on `ubuntu-24.04` /
   `ubuntu-24.04-arm`: build → smoke test (`HARDENING.md` enforced) →
   push `g<sha>-<arch>`. **scan-0** (trivy, `images.yaml` `scan:`)
   runs against the pushed tag and never blocks the merge; a finding
   fails its own job, visibly.
4. **merge-0** — manifest list `g<sha>` with OCI index annotations; on
   `main` also `edge`, cosign signature and SBOM attestation on the
   digest.

Releases are **promotions**, never rebuilds: `release.yml` computes the
next `vX.Y.Z` from the existing tags (first release `1.0.0`), retags
the `g<sha>` images of the target `main` commit to `X.Y.Z` + `X.Y` +
`latest` (an `X.Y.Z` pointing elsewhere is refused; one already at that
digest is accepted, so a partial failure can be re-run), signs them,
then creates the
git tag and GitHub Release. It runs on manual dispatch (`bump`
patch|minor|major), from `rebuild-weekly.yml` (full `no_cache` rebuild
every Monday, then a patch release — the only step without a PR, it
produces the version Renovate then proposes downstream), and
automatically after a green `main` build whose commit came from a PR
labelled `auto-release` (Renovate patch/digest updates, which
automerge once the checks pass). Minor/major bumps stay manual PRs.

`WAAS_IMAGES_BUILD_STRATEGY=qemu` (repository variable) routes every
build to the amd64 fleet under emulation if the arm fleet is down.

## CI toolkit (`.github/actions/`)

One copy of each script, shared with `waas-workspace-images` through
composite actions. This repo uses them as `./.github/actions/<name>`;
the workspace repo pins `XoRHub/waas-core-images/.github/actions/<name>@actions/v1`
(Renovate bumps it). `actions/vN` tags are distinct from the image
tags `vX.Y.Z`: move `actions/v1` forward for compatible toolkit
changes, cut `actions/v2` for a breaking one.

| Action | Does |
|---|---|
| `pipeline` | matrices (`layer<d>`, `merge<d>`, `carry`, `variants`, `scan_*`) from `images.yaml` + manifests; materialises `recipe:` Dockerfiles; per-image docs to the job summary. Inputs `since`, `all`, `max-depth` |
| `build` | build → smoke test (`build/smoke_test.sh`, the hardening gate) → push `g<sha>-<arch>` for one matrix entry (`image: ${{ toJSON(matrix) }}`) |
| `merge` | manifest list `g<sha>`; `publish` adds `edge` + sign + SBOM attest; `carry` retags `edge` instead |
| `promote` | `g<sha>` → `X.Y.Z`, `X.Y`, `latest` + sign (idempotent) |
| `scan` | trivy gate for one pushed arch tag |

Each action calls its script through `${{ github.action_path }}`; the
scripts share `lib.sh` (exports the matrix entry passed as `IMAGE_JSON`,
derives registry/sha/URL from the runner env, wraps cosign/trivy), so a
consumer workflow needs no `env:` block beyond `permissions: packages:
write, id-token: write, actions: read`.
Parents: a manifest's `from:` names either another variant of the same
pipeline (built one depth later `FROM <registry>/<parent>:g<sha>`) or a
key of `images.yaml`'s `cores:` map (`name: registry/ref:tag@sha256:…`,
passed straight as `BASE_IMAGE`). `buildContexts:` (manifest root or
per variant) adds buildx named contexts — root-relative paths, used by
`COPY --from=<name>` — for files shared between image directories. A
consumer with a deeper tree copies `build.yml` and adds a
`layer-N`/`scan-N`/`merge-N` set per depth (`layer-N` needs `carry` and
`merge-(N-1)`), raising `max-depth`.

## Image metadata

Set by the toolkit, never per Dockerfile — as config labels on each
arch image and as `index:` annotations on the manifest list:
`org.opencontainers.image.{title,description,revision,created,source,documentation,licenses,vendor}`,
`io.xorhub.waas.os` (`ubuntu-noble`, `debian-13`, `fedora-43`),
`io.xorhub.waas.layer`, `io.xorhub.waas.profile` (`standard`|`dev`),
`io.xorhub.waas.parent` (the resolved parent ref, empty on a core).
Inspect with `docker buildx imagetools inspect <ref> --raw`.

## Local loop

```
make build IMAGE=core-ubuntu-noble   # any variant; args/contexts resolved from the manifests
make run   IMAGE=core-ubuntu-noble   # VNC on localhost:15901, password devpassword
make smoke IMAGE=core-ubuntu-noble   # the CI smoke test against the local build
make lint                            # hadolint + shellcheck + actionlint (docker)
make test                            # toolkit unit tests (uv)
```

Requirements: Docker with buildx, [uv](https://docs.astral.sh/uv/)
(every Python script pins its dependencies inline, PEP 723).
