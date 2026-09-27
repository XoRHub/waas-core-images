#!/bin/sh
# scan_image.sh — trivy vulnerability/secret scan of the per-arch tag
# build_image.sh already pushed. Its own job: a finding must stay
# visible (red job) without blocking the push/merge, which never depend
# on it. TRIVY_SEVERITY/TRIVY_IGNORE_UNFIXED come from images.yaml's
# scan: block through the pipeline outputs.
set -eu
. "$(dirname "$0")/../lib.sh"

: "${IMG_NAME:?}" "${IMG_CONTEXT:?}" "${IMG_ARCH:?}"
IMAGE="${REGISTRY_IMAGE}/${IMG_NAME}:${SHA_TAG}-${IMG_ARCH#linux/}"

# Per-image exceptions: <context>/.trivyignore, each entry an
# investigated false positive documented inline.
IGNORE_FLAGS=""
if [ -f "${IMG_CONTEXT}/.trivyignore" ]; then
    IGNORE_FLAGS="-v $(pwd)/${IMG_CONTEXT}/.trivyignore:/.trivyignore:ro"
    IGNOREFILE_FLAG="--ignorefile /.trivyignore"
fi

log "trivy scan (gate: ${TRIVY_SEVERITY:-HIGH,CRITICAL}) ${IMAGE}"
# shellcheck disable=SC2086
trivy ${IGNORE_FLAGS} "${TRIVY_IMAGE}" image \
    --username "${REGISTRY_USER}" --password "${REGISTRY_PASSWORD}" \
    --severity "${TRIVY_SEVERITY:-HIGH,CRITICAL}" \
    --ignore-unfixed="${TRIVY_IGNORE_UNFIXED:-true}" \
    --exit-code "${TRIVY_EXIT_CODE:-1}" \
    --scanners vuln,secret \
    ${IGNOREFILE_FLAG:-} \
    "${IMAGE}"
