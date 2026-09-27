# Hardening checklist

Applies to every image built from this repo and, through the shared
smoke test, to every image built on one. Each item says where it is
enforced, so the list is verifiable. App-specific gaps (browser
sandboxes, dev profiles' package installs) are documented in
`waas-workspace-images`.

## Enforced at build time (Dockerfile)

- [x] **Non-root user** with fixed, build-arg-configurable UID/GID
      (`1000:1000`, `waas_user`, home `/home/waas_user`). Ubuntu's
      default `ubuntu` user (UID 1000 squatter) is removed.
      → `base/*/Dockerfile`; verify `docker run --rm --entrypoint id <img>`.
      (Every `verify:` uses `--entrypoint`: the images carry no `CMD`
      and `waas-entrypoint` ignores its arguments.)
- [x] **No setuid/setgid binaries**: all `+s` bits stripped after the
      package layer, and asserted by the smoke test on every build
      (`-dev` derived images must show exactly `/usr/bin/sudo`).
      → verify `docker run --rm --entrypoint find <img> / -xdev -perm /6000 -type f`
      → empty (a few `Permission denied` on stderr is the non-root user
      at work).
- [x] **No secrets in the image**: the password arrives at runtime via
      env, is hashed to a 0600 file in tmpfs and scrubbed from the
      environment. CI runs `trivy --scanners vuln,secret`.
      → `waas-entrypoint`.
- [x] **Minimal packages**: `--no-install-recommends` /
      `install_weak_deps=False`, caches, lists and logs removed.
- [x] **No remote-desktop or shell surface beyond VNC**: no Dockerfile
      installs `xrdp` or `sshd`, no build arg can add them, the
      entrypoint has no code path to start them, ports 3389/2222 are
      not exposed. The platform reaches a Linux workspace over VNC and
      nothing else (waas#117). `WAAS_VNC_ENABLED=0` (once "RDP only") is
      refused at boot rather than ignored.
      → verify `docker run --rm --entrypoint sh <img> -c 'command -v xrdp; command -v sshd'`.
- [x] **Pinned supply chain**: OS base images pinned by Renovate digest,
      CI tool images version-pinned, cores consumed downstream by
      `tag@digest`.

## Enforced at runtime (image design + smoke test)

- [x] **Read-only rootfs compatible**: only `/home/waas_user` and `/tmp`
      are written. CI runs every image with `--read-only --cap-drop
      ALL --security-opt no-new-privileges` and a tmpfs on exactly those
      two paths.
      → `.github/actions/build/smoke_test.sh`.
- [x] **Zero capabilities required**: all ports > 1024, no PAM, no
      chown at startup (fsGroup handles the PVC).
- [x] **X display protected** by a MIT-MAGIC-COOKIE (no `Xvnc -ac`).
- [x] **VNC auth always on** (`-SecurityTypes VncAuth`, `-rfbauth`);
      an empty password refuses to start. No credential-less mode.
- [x] **Audio via an unprivileged PulseAudio** (user mode, no rtkit):
      null sink + native protocol on TCP 4713 for guacd, module loading
      frozen after startup. TCP auth is anonymous by design: the
      guacd-only NetworkPolicy is the boundary for 4713 exactly as for
      the cleartext VNC port. `WAAS_AUDIO_ENABLED=0` disables it.
      → `waas-entrypoint`, `etc/waas/pulse/default.pa.tpl`; the smoke
      test runs `pactl info` against tcp:4713.

## To apply on the platform side (documented contract)

- [ ] Pod `securityContext`: `runAsNonRoot`, `runAsUser/fsGroup: 1000`,
      `seccompProfile: RuntimeDefault`, `allowPrivilegeEscalation:
      false`, `capabilities.drop: [ALL]`, `readOnlyRootFilesystem: true`
      + emptyDir on `/tmp`. Meets PodSecurity **restricted**; AppArmor
      `runtime/default` is sufficient.
- [ ] NetworkPolicy: only guacd reaches 5901/4713; no east-west between
      workspaces. Mandatory while audio is enabled.
- [ ] Template credentials from Vault/ESO; the images are
      `valueFrom`-ready.

## Reduced profile: `-dev` derived images

A workspace whose users need `sudo apt install` in-session cannot get it
from a runtime flag — sudo is setuid, apt writes outside the writable
paths, `no-new-privileges` kills setuid transitions. It is a build-time
variant of a *derived* image with a distinct `-dev` tag; the core ships
no sudo. Items lifted there, and only these:

- setuid: exactly `/usr/bin/sudo` (NOPASSWD for UID 1000), asserted by
  the smoke test, which also runs `sudo -n true` for real;
- read-only rootfs: the template must set `readOnlyRootFilesystem:
  false` (in-session installs die with the pod; only the home PVC
  survives);
- `allowPrivilegeEscalation: true`, and the runtime **default**
  capability set instead of `drop: [ALL]` (a setuid binary regains
  capabilities only within the bounding set — an ALL-dropped pod keeps
  sudo dead).

Everything else holds. Guard rails: the pipeline generator refuses
`INSTALL_SUDO=1` on a variant whose name lacks `-dev` or whose
`profile:` is not `dev`; the image bakes `WAAS_PROFILE=dev` and the
entrypoint logs a boot warning.

## Runtime init hook (`/etc/waas/init.d/`)

Sourced by `waas-entrypoint` after the image's own `entrypoint.d/`
hooks (separate directory: a mount over `entrypoint.d/` would shadow
them). Mount a ConfigMap there for per-workspace initialisation without
a rebuild. No privilege change: UID 1000, and anything installed
outside the home PVC is lost on restart.

## Threat model for desktop traffic

The browser session is TLS-terminated at the ingress; wwt → guacd →
workspace runs on the pod network. VNC between guacd and the workspace
is **cleartext**, accepted because both endpoints share a namespace,
the NetworkPolicy restricts the path to guacd, and guacd's VNC client
support for X509/VeNCrypt is unreliable. If the pod network itself is
in scope, enable WireGuard/IPsec at the CNI layer rather than
per-protocol TLS — it also covers guacd → wwt.

## Known, accepted gaps

- `/etc/machine-id` is identical across containers of one image (baked
  for read-only dbus); nothing shipped uses it as identity.
- Clipboard is text-only over VNC (`vncconfig`); files and images are
  not bridged. Browser-window resizes are not pushed by guacd's VNC
  client — `waas-resize` inside the session is the workaround.
