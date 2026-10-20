# shellcheck shell=sh
# lib.sh — shared by the action scripts (sourced, POSIX sh).
#
# Everything derives from the GitHub runner env; each value can be
# overridden for a local run. REGISTRY_PASSWORD is the only input with
# no default (the actions pass github.token). IMAGE_JSON, one pipeline
# matrix entry, is exported as the IMG_*/SMOKE_* variables. `export`,
# not a plain assignment: build_image.sh runs smoke_test.sh as a child
# `sh`, which otherwise sees no SMOKE_* at all and silently falls back
# to the standard profile with every protocol probe skipped.

if [ -n "${IMAGE_JSON:-}" ]; then
    eval "$(printf '%s' "${IMAGE_JSON}" | jq -r 'to_entries[] | "export \(.key)=\(.value | tostring | @sh)"')"
fi

: "${REGISTRY_PASSWORD:?}"
REGISTRY="${REGISTRY:-ghcr.io}"
REGISTRY_USER="${REGISTRY_USER:-${GITHUB_ACTOR:?}}"
REGISTRY_IMAGE="${REGISTRY_IMAGE:-${REGISTRY}/$(printf '%s' "${GITHUB_REPOSITORY:?}" | tr '[:upper:]' '[:lower:]')}"
COMMIT_SHA="${COMMIT_SHA:-${GITHUB_SHA:?}}"
SHORT_SHA=$(printf '%.8s' "${COMMIT_SHA}")
SHA_TAG="g${SHORT_SHA}"
PROJECT_URL="${PROJECT_URL:-${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}}"

COSIGN_IMAGE=ghcr.io/sigstore/cosign/cosign:v3.1.4
# shellcheck disable=SC2034  # used by the sourcing scripts
TRIVY_IMAGE=ghcr.io/aquasecurity/trivy:0.72.0

log() { printf '\n=== %s\n' "$*"; }

registry_login() {
    docker login -u "${REGISTRY_USER}" -p "${REGISTRY_PASSWORD}" "${REGISTRY}"
}

# The parent's ref: an external tag@digest (IMG_BASE_IMAGE) or the
# in-pipeline parent's manifest list of this very commit.
parent_ref() {
    if [ -n "${IMG_PARENT:-}" ]; then
        printf '%s/%s:%s' "${REGISTRY_IMAGE}" "${IMG_PARENT}" "${SHA_TAG}"
    else
        printf '%s' "${IMG_BASE_IMAGE:-}"
    fi
}

# Keyless OIDC signing; a no-op outside GitHub Actions (no OIDC token).
# Registry auth via flags: the job's ~/.docker is not visible inside
# the cosign container.
cosign() {
    if [ -z "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ]; then
        log "no OIDC token in the environment — skipping cosign $1"
        return 0
    fi
    docker run --rm -v "$(pwd):/work" -w /work \
        -e ACTIONS_ID_TOKEN_REQUEST_TOKEN -e ACTIONS_ID_TOKEN_REQUEST_URL \
        "${COSIGN_IMAGE}" "$@" --yes \
        --registry-username "${REGISTRY_USER}" \
        --registry-password "${REGISTRY_PASSWORD}"
}

# trivy pulls the image through its own registry client (no daemon).
trivy() {
    docker run --rm -v "$(pwd):/out" -v trivy-cache:/root/.cache/trivy "$@"
}
