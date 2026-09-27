#!/bin/sh
# merge_image.sh — assemble the per-arch tags pushed by build_image.sh
# into the manifest list g<sha>; with PUBLISH=1 (default branch) also
# move `edge` to it, cosign-sign the digest and attest a CycloneDX SBOM
# to it. Signatures and attestations hang off the digest, so the release
# tags promote_image.sh adds later inherit them.
#
# IMG_CARRY=1 (variant not rebuilt this run): retag the current `edge`
# digest to g<sha> instead, zero build — every commit carries the full
# image set. The digest is unchanged, so nothing is re-signed.
set -eu
. "$(dirname "$0")/../lib.sh"

: "${IMG_NAME:?}"
IMAGE="${REGISTRY_IMAGE}/${IMG_NAME}"

log "docker login"
registry_login

ANNOTATION_FLAGS=""
if [ "${IMG_CARRY:-0}" = "1" ]; then
    SOURCES="${IMAGE}:edge"
else
    : "${IMG_ARCHS:?}"
    SOURCES=""
    for arch in $(echo "${IMG_ARCHS}" | tr ',' ' '); do
        SOURCES="${SOURCES} ${IMAGE}:${SHA_TAG}-${arch#linux/}"
    done
    # Per-arch config labels do not propagate to the manifest list:
    # re-assert the same keys as index annotations (buildx >= 0.12).
    annotate() {
        [ -n "$2" ] || return 0
        ANNOTATION_FLAGS="${ANNOTATION_FLAGS} --annotation \"index:$1=$2\""
    }
    annotate org.opencontainers.image.title         "${IMG_NAME}"
    annotate org.opencontainers.image.description   "${IMG_DESCRIPTION:-}"
    annotate org.opencontainers.image.revision      "${COMMIT_SHA}"
    annotate org.opencontainers.image.created       "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    annotate org.opencontainers.image.source        "${PROJECT_URL}"
    annotate org.opencontainers.image.documentation "${IMG_DOCUMENTATION:-}"
    annotate org.opencontainers.image.licenses      "Apache-2.0"
    annotate org.opencontainers.image.vendor        "XorHub"
    annotate io.xorhub.waas.os                      "${IMG_OS:-}"
    annotate io.xorhub.waas.layer                   "${IMG_LAYER:-}"
    annotate io.xorhub.waas.profile                 "${IMG_PROFILE:-standard}"
    annotate io.xorhub.waas.parent                  "$(parent_ref)"
fi

TAG_FLAGS="-t ${IMAGE}:${SHA_TAG}"
if [ "${PUBLISH:-0}" = "1" ]; then
    TAG_FLAGS="${TAG_FLAGS} -t ${IMAGE}:edge"
fi

log "manifest list ${IMAGE}:${SHA_TAG} <-${SOURCES}"
# eval: ANNOTATION_FLAGS carries values with spaces (description).
eval "docker buildx imagetools create ${TAG_FLAGS} ${ANNOTATION_FLAGS} ${SOURCES}"
DIGEST=$(docker buildx imagetools inspect "${IMAGE}:${SHA_TAG}" --format '{{.Manifest.Digest}}')
log "published ${IMAGE}@${DIGEST}"

if [ "${PUBLISH:-0}" != "1" ] || [ "${IMG_CARRY:-0}" = "1" ]; then
    exit 0
fi

log "cosign sign (keyless OIDC)"
cosign sign -a revision="${COMMIT_SHA}" "${IMAGE}@${DIGEST}"

# One representative platform (amd64): package sets barely differ by
# arch. Best-effort: a Sigstore hiccup must not undo the publish above.
attach_sbom() {
    log "trivy sbom (cyclonedx) ${IMAGE}@${DIGEST}"
    trivy "${TRIVY_IMAGE}" image \
        --username "${REGISTRY_USER}" --password "${REGISTRY_PASSWORD}" \
        --platform linux/amd64 \
        --format cyclonedx \
        --output /out/sbom.cdx.json \
        "${IMAGE}@${DIGEST}"
    log "cosign attest (cyclonedx sbom)"
    cosign attest --predicate sbom.cdx.json --type cyclonedx "${IMAGE}@${DIGEST}"
}
attach_sbom || log "WARNING: SBOM attestation failed (non-fatal) for ${IMAGE}@${DIGEST}"
