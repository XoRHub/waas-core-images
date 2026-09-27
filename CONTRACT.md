# Runtime contract

What a derived image (the `waas-workspace-images` desktops and apps)
and the WaaS platform may rely on from a `core-*` image. Every item
below is implemented in `base/*/Dockerfile` and the shared `base/rootfs`; the
smoke test (`.github/actions/build/smoke_test.sh`) exercises the
hardening half of it on every build.

Versioned by semver on the git tags (`vX.Y.Z`): removing or renaming
anything listed here is a **major**, adding is a **minor**, rebuilds
and fixes that change nothing here are a **patch**.

## Identity and filesystem

| Item | Value |
|---|---|
| User | `waas_user`, UID/GID `1000:1000`; build args `WAAS_USER`, `WAAS_UID`, `WAAS_GID`; `ENV WAAS_USER` baked |
| Home | `/home/waas_user` (`HOME`, `WORKDIR`), shell `/bin/bash`. Seeded from `/etc/skel` at boot when `~/.profile` is missing (fresh PVC); `~/Desktop` created |
| Writable paths | `/home/waas_user` and `/tmp` only. All mutable state lives under `WAAS_RUNTIME_DIR` (`/tmp/waas`); the image runs with `--read-only --cap-drop ALL --security-opt no-new-privileges` |
| setuid/setgid | none — every bit is stripped after package installation |
| `/etc/machine-id` | baked (identical across containers), for dbus on a read-only rootfs |

## Process model

| Item | Value |
|---|---|
| Entrypoint | `ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/waas-entrypoint"]`, no `CMD`; arguments are ignored |
| `waas-entrypoint` | bash, `set -euo pipefail`, `umask 077`. Renders `$RUNDIR/supervisor.d/{10-xvnc,15-pulseaudio,20-session}.conf`, then `exec supervisord -n -c /etc/waas/supervisord.conf` |
| supervisord | unprivileged; includes `$WAAS_RUNTIME_DIR/supervisor.d/*.conf`; logs to stdout; control socket `$WAAS_RUNTIME_DIR/supervisor.sock` |
| `/etc/waas/entrypoint.d/*.sh` | image hooks, **sourced** by `waas-entrypoint` before supervisord starts, in glob order. They share its strict mode, env and helpers: `log`, `fail`, `RUNDIR`, `RESOLUTION`, `DEPTH`, `XAUTHORITY`, `HOME`, `DISPLAY`. The way to add a supervised service: write `$RUNDIR/supervisor.d/NN-<name>.conf` (use a priority above 20) |
| `/etc/waas/init.d/*.sh` | runtime hooks (ConfigMap mount), sourced after `entrypoint.d/`, same privileges (UID 1000). Separate directory so a mount cannot shadow the image's own hooks |
| `WAAS_PROFILE` | `standard` (default) or `dev`; `dev` logs a loud boot warning. The toolkit passes build arg `WAAS_PROFILE=dev` to `profile: dev` variants — a derived Dockerfile bakes it with `ARG WAAS_PROFILE=standard` / `ENV WAAS_PROFILE=${WAAS_PROFILE}` |

## Session

`waas-session` (supervisord priority 20, autorestart) waits for the X
server, starts `vncconfig -nowin` (clipboard bridge), then runs exactly
one of:

| Env (baked by the derived image) | Behaviour |
|---|---|
| `WAAS_APP=<cmd>` | single-app kiosk: `openbox --replace --config-file /etc/waas/openbox-app.rc.xml` (undecorated, maximised, no keybindings, no menu; dialogs keep decorations) then `exec dbus-run-session -- <cmd>`; the app is the session, exiting it restarts the session |
| `WAAS_STARTUP=<cmd>` | full session: `exec dbus-launch --exit-with-session <cmd>` (a desktop sets `startxfce4`) |
| neither | fallback `openbox` + `xterm` |

Both set is fatal. Both are baked empty in the core (`ENV WAAS_STARTUP=
WAAS_APP=`). Word splitting applies: a command may carry arguments.

`waas-resize WIDTHxHEIGHT` resizes the running display server-side
(RandR); `DISPLAY` is `:1`, `XAUTHORITY` is `$RUNDIR/Xauthority`
(MIT-MAGIC-COOKIE, no `-ac`).

## Network and protocol

| Item | Value |
|---|---|
| VNC | TigerVNC `Xvnc :1` on **5901**, RFB 3.8, `-SecurityTypes VncAuth -rfbauth`, `-AlwaysShared`, `-UseBlacklist=0` (Kubernetes TCP probes must not trip the lockout). TCP-open on 5901 ⇔ session ready. The only session listener: no xrdp, no sshd, no build arg to add one |
| `WAAS_DESKTOP_PASSWORD` | **required**; boot fails without it. Hashed to `$RUNDIR/vnc.passwd` (0600) then scrubbed: children see `WAAS_DESKTOP_PASSWORD=''` |
| Refused at boot | `VNC_PW`, `RDP_PASSWORD` (secret-bearing legacy names — a silent alias would let the platform's generated password shadow yours); `WAAS_VNC_ENABLED` other than `1` |
| `WAAS_VNC_RESOLUTION` | default `1920x1080`, must match `^[0-9]+x[0-9]+$`; legacy `VNC_RESOLUTION` honoured as fallback |
| `WAAS_VNC_COL_DEPTH` | default `24`; legacy `VNC_COL_DEPTH` honoured as fallback |
| Audio | `WAAS_AUDIO_ENABLED=1` (default): PulseAudio in user mode (no root, no rtkit), null sink `waas`, module loading disabled after start. Session apps get `PULSE_SERVER=unix:$RUNDIR/pulse/native`; guacd streams the sink monitor over **TCP 4713**, anonymous auth (the NetworkPolicy is the boundary, as for 5901). `WAAS_AUDIO_ENABLED=0` starts no daemon. Policy template: `/etc/waas/pulse/default.pa.tpl` (`@RUNDIR@`) |
| `EXPOSE` | `5901 4713` |

Every runtime variable is `WAAS_`-prefixed; build `ARG`s are a
separate, unprefixed namespace.

## Tools present

Available to hooks and derived images on both the apt and dnf cores:
`Xvnc`, `vncconfig`, `vncpasswd`, `supervisord`, `tini`, `openbox`,
`xterm`, `xauth`, `xrandr`, `dbus-launch`, `dbus-run-session`,
`dbus-uuidgen`, `pulseaudio`, `pactl`, `bash`, CA certificates, DejaVu +
misc X fonts. `xsetroot` is apt-only (`waas-session` guards it).
Language: `LANG=LC_ALL=C.UTF-8`.

## Building on a core

```dockerfile
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER 0
RUN apt-get update && \
    apt-get install -y --no-install-recommends <packages> && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /var/cache/* /var/log/*
RUN find / -xdev -perm /6000 -type f -exec chmod a-s {} + 2>/dev/null || true
ENV WAAS_APP="<command>"      # or WAAS_STARTUP, or neither
USER 1000:1000
```

- `BASE_IMAGE` is a `tag@digest` ref to a core (`images.yaml` `cores:`
  in the consumer repo), never a floating tag.
- Root-run steps inherit `HOME=/home/waas_user`: remove any root-owned
  dotfile they leave there (`~/.gnupg`, `~/.wget-hsts`), it would
  shadow the user's on a fresh PVC.
- `INSTALL_SUDO=1` (reduced `-dev` profile, `HARDENING.md`) is a
  derived-image convention: the toolkit refuses it on a variant without
  the `-dev` suffix and `profile: dev`, and the smoke test then expects
  exactly `/usr/bin/sudo` setuid. The core itself ships no sudo.
- The `recipe:` template of the toolkit emits exactly this shape.

## Image references

`ghcr.io/xorhub/waas-core-images/<variant>` — `core-ubuntu-noble`,
`core-debian-13`, `core-fedora-43`; `linux/amd64` + `linux/arm64`.

| Tag | Meaning |
|---|---|
| `X.Y.Z` | release, immutable |
| `X.Y` | latest patch of that minor |
| `latest` | latest release |
| `edge` | latest green `main` |
| `g<sha>` | the image set of one `main` commit (8-char short SHA) |

Releases are cosign-signed (keyless, identity
`https://github.com/XoRHub/waas-core-images/.github/workflows/build.yml@refs/heads/main`
and `…/release.yml@…`) with a CycloneDX SBOM attested to the digest.
