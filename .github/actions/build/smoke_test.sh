#!/bin/sh
# smoke_test.sh — prove the image serves its protocols under the
# hardening contract: read-only rootfs, every capability dropped,
# no-new-privileges, tmpfs on exactly /tmp and /home/waas_user. A
# service that needs to write anywhere else fails here, not in a pod.
#
# Gates: suid sweep (empty; exactly /usr/bin/sudo with SMOKE_PROFILE=dev,
# where sudo is exercised for real), mise wiring when the image ships it,
# then the retried protocol probes: the RFB banner on 5901 (SMOKE_VNC=1)
# and a PulseAudio handshake on tcp:4713 (SMOKE_AUDIO=1).
set -eu

: "${SMOKE_IMAGE:?}"
HOST="${SMOKE_HOST:-localhost}"
NAME="smoke-$$"

# dev profile mirrors the relaxed pod profile its template must set: no
# --read-only, no no-new-privileges, the runtime default capability set
# (a setuid binary regains capabilities only within the bounding set, so
# an ALL-dropped pod keeps sudo dead even with escalation allowed).
PROFILE="${SMOKE_PROFILE:-standard}"
if [ "${PROFILE}" = "dev" ]; then
    HARDEN_FLAGS=""
else
    HARDEN_FLAGS="--read-only --cap-drop ALL --security-opt no-new-privileges"
fi

ENV_FLAGS="-e WAAS_DESKTOP_PASSWORD=smoketest-$$"
for kv in ${SMOKE_ENV:-}; do
    ENV_FLAGS="${ENV_FLAGS} -e ${kv}"
done

cleanup() {
    docker logs "${NAME}" 2>&1 | tail -n 40 || true
    docker rm -f "${NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# shellcheck disable=SC2086
docker run -d --name "${NAME}" \
    ${HARDEN_FLAGS} \
    --tmpfs /tmp \
    --tmpfs /home/waas_user:mode=1777 \
    -p 15901:5901 \
    ${ENV_FLAGS} \
    "${SMOKE_IMAGE}" >/dev/null

SUID_FOUND=$(docker exec "${NAME}" find / -xdev -perm /6000 -type f 2>/dev/null | sort | tr '\n' ' ' || true)
if [ "${PROFILE}" = "dev" ]; then
    if [ "${SUID_FOUND}" != "/usr/bin/sudo " ]; then
        echo "FAIL: dev profile must ship exactly /usr/bin/sudo setuid, got: '${SUID_FOUND}'"
        exit 1
    fi
    echo "OK: suid set is exactly /usr/bin/sudo (dev profile)"
    docker exec "${NAME}" sudo -n true \
        || { echo "FAIL: sudo -n true failed in the dev-profile container"; exit 1; }
    echo "OK: sudo works (dev profile)"
else
    if [ -n "${SUID_FOUND}" ]; then
        echo "FAIL: setuid/setgid binaries found: ${SUID_FOUND}"
        exit 1
    fi
    echo "OK: no setuid/setgid binaries"
fi

# mise must be wired both ways: shims on PATH (non-interactive contexts)
# and `mise activate` in an interactive bash (declare -f: the binary on
# PATH would satisfy command -v without activation ever having run).
if docker exec "${NAME}" sh -c 'command -v mise' >/dev/null 2>&1; then
    docker exec "${NAME}" sh -c 'case ":$PATH:" in *":$HOME/.local/share/mise/shims:"*) exit 0 ;; *) exit 1 ;; esac' \
        || { echo "FAIL: mise is installed but its shims dir is not on PATH"; exit 1; }
    docker exec "${NAME}" bash -ic 'declare -f mise >/dev/null' \
        || { echo "FAIL: mise is not activated in an interactive shell"; exit 1; }
    echo "OK: mise shims on PATH and activated in interactive bash"
fi

# Published ports accept connections before the service is up
# (docker-proxy answers first): the probe itself is the readiness check.
retry() {
    label="$1"; shift
    i=0
    while [ $i -lt 60 ]; do
        if "$@"; then return 0; fi
        [ "$(docker inspect -f '{{.State.Running}}' "${NAME}")" = "true" ] \
            || { echo "FAIL: container exited during ${label} check"; return 1; }
        i=$((i + 1)); sleep 2
    done
    echo "FAIL: ${label} not answering after 120s"
    return 1
}

probe_vnc() {
    BANNER=$(nc -w 5 "${HOST}" 15901 </dev/null 2>/dev/null | dd bs=1 count=12 2>/dev/null || true)
    case "${BANNER}" in "RFB "*) return 0 ;; *) return 1 ;; esac
}

if [ "${SMOKE_VNC:-0}" = "1" ]; then
    retry "VNC" probe_vnc
    echo "OK: VNC answered with '${BANNER}'"
fi

probe_audio() {
    docker exec "${NAME}" pactl --server tcp:127.0.0.1:4713 info >/dev/null 2>&1
}

if [ "${SMOKE_AUDIO:-0}" = "1" ]; then
    retry "AUDIO" probe_audio
    echo "OK: PulseAudio answered on tcp:4713"
fi

echo "smoke test passed for ${SMOKE_IMAGE}"
