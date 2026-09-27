#!/bin/sh
# promote_image.sh — retag the already-tested g<sha> manifest list of
# one image to a release: X.Y.Z (immutable), X.Y (mobile) and, unless
# RELEASE_LATEST=0, latest. Zero build: the digest is unchanged, so the
# signature and SBOM attestation made at merge time still apply; a
# second signature carries the version claim. Idempotent: an X.Y.Z that
# already points at this digest is accepted (a re-run after a partial
# failure), one pointing elsewhere is refused.
set -eu
. "$(dirname "$0")/../lib.sh"

: "${IMG_NAME:?}" "${RELEASE_VERSION:?}"
printf '%s' "${RELEASE_VERSION}" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' \
    || { echo "FATAL: RELEASE_VERSION must be X.Y.Z, got '${RELEASE_VERSION}'"; exit 1; }
IMAGE="${REGISTRY_IMAGE}/${IMG_NAME}"
SOURCE="${IMAGE}:${SHA_TAG}"

log "docker login"
registry_login

digest_of() { docker buildx imagetools inspect "$1" --format '{{.Manifest.Digest}}' 2>/dev/null; }

SOURCE_DIGEST=$(digest_of "${SOURCE}") \
    || { echo "FATAL: ${SOURCE} is not published — was this commit built on main?"; exit 1; }
ALREADY=0
if EXISTING=$(digest_of "${IMAGE}:${RELEASE_VERSION}"); then
    [ "${EXISTING}" = "${SOURCE_DIGEST}" ] \
        || { echo "FATAL: ${IMAGE}:${RELEASE_VERSION} exists at ${EXISTING} — release tags are immutable"; exit 1; }
    log "${IMAGE}:${RELEASE_VERSION} already promoted; refreshing the mobile tags only"
    ALREADY=1
fi

TAG_FLAGS="-t ${IMAGE}:${RELEASE_VERSION} -t ${IMAGE}:${RELEASE_VERSION%.*}"
if [ "${RELEASE_LATEST:-1}" = "1" ]; then
    TAG_FLAGS="${TAG_FLAGS} -t ${IMAGE}:latest"
fi

log "promote ${SOURCE} -> ${TAG_FLAGS}"
# shellcheck disable=SC2086
docker buildx imagetools create ${TAG_FLAGS} "${SOURCE}"
log "promoted ${IMAGE}@${SOURCE_DIGEST}"
[ "${ALREADY}" = "0" ] || exit 0

log "cosign sign (keyless OIDC)"
cosign sign -a revision="${COMMIT_SHA}" -a version="${RELEASE_VERSION}" "${IMAGE}@${SOURCE_DIGEST}"
