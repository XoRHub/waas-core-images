#!/bin/sh
# build_image.sh — build, smoke-test and push ONE image for ONE
# architecture, natively on a runner of that arch. Flow: build (--load)
# -> smoke test against a real container -> push the arch tag
# g<sha>-<arch> (OCI mediatypes). merge_image.sh assembles the arch
# tags into the manifest list; trivy/SBOM run as separate jobs against
# the pushed tag so a finding stays visible without blocking the push.
#
# Driven by the IMG_*/SMOKE_* variables of one pipeline matrix entry.
set -eu
. "$(dirname "$0")/../lib.sh"

: "${IMG_NAME:?}" "${IMG_CONTEXT:?}" "${IMG_ARCH:?}"
ARCH="${IMG_ARCH#linux/}"
ARCH_TAG="${SHA_TAG}-${ARCH}"
IMAGE="${REGISTRY_IMAGE}/${IMG_NAME}"

log "docker login + buildx builder"
registry_login
# Container driver: required for the GHA cache export. Reused if a local
# run pre-provisioned one.
docker buildx inspect waas >/dev/null 2>&1 || \
    docker buildx create --name waas \
        --driver-opt image=moby/buildkit:v0.34.1 >/dev/null
docker buildx use waas

# QEMU fallback when the job lands on a runner of another arch
# (WAAS_IMAGES_BUILD_STRATEGY=qemu); a no-op in the nominal native case.
RUNNER_ARCH=$(uname -m)
case "${RUNNER_ARCH}" in
    x86_64) RUNNER_ARCH=amd64 ;;
    aarch64) RUNNER_ARCH=arm64 ;;
esac
if [ "${ARCH}" != "${RUNNER_ARCH}" ]; then
    log "non-native build (${RUNNER_ARCH} runner -> ${ARCH}): installing QEMU binfmt"
    docker run --privileged --rm tonistiigi/binfmt:qemu-v9.2.2 --install all >/dev/null
fi

BUILD_ARG_FLAGS=""
for kv in ${IMG_BUILD_ARGS:-}; do
    BUILD_ARG_FLAGS="${BUILD_ARG_FLAGS} --build-arg ${kv}"
done
PARENT=$(parent_ref)
if [ -n "${PARENT}" ]; then
    BUILD_ARG_FLAGS="${BUILD_ARG_FLAGS} --build-arg BASE_IMAGE=${PARENT}"
fi
for kv in ${IMG_BUILD_CONTEXTS:-}; do
    BUILD_ARG_FLAGS="${BUILD_ARG_FLAGS} --build-context ${kv}"
done
DOCKERFILE_FLAG=""
if [ -n "${IMG_DOCKERFILE:-}" ]; then
    [ -f "${IMG_CONTEXT}/${IMG_DOCKERFILE}" ] \
        || { echo "FATAL: ${IMG_CONTEXT}/${IMG_DOCKERFILE} missing — recipe not materialised?"; exit 1; }
    DOCKERFILE_FLAG="-f ${IMG_CONTEXT}/${IMG_DOCKERFILE}"
fi

# NO_CACHE=1 rebuilds every layer from scratch: the only way to pick up
# a security update in a package whose RUN text did not change (the
# layer cache would keep serving the layer built before the fix).
NO_CACHE_FLAG=""
if [ "${NO_CACHE:-0}" = "1" ]; then
    log "NO_CACHE=1: ignoring the layer cache for this build"
    NO_CACHE_FLAG="--no-cache"
fi

CREATED=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# One flag set for both invocations: the artifact pushed after the gate
# must be byte-identical (same config, cache-hot layers) to the one
# that was smoke-tested. OCI labels are set here, not per Dockerfile,
# so hand-written and recipe-generated images cannot drift.
buildx_build() {
    # shellcheck disable=SC2086
    docker buildx build \
        --platform "${IMG_ARCH}" \
        --provenance=false \
        --cache-from "type=gha,scope=${IMG_NAME}-${ARCH}" \
        ${BUILD_ARG_FLAGS} \
        ${DOCKERFILE_FLAG} \
        --label "org.opencontainers.image.title=${IMG_NAME}" \
        --label "org.opencontainers.image.description=${IMG_DESCRIPTION:-}" \
        --label "org.opencontainers.image.revision=${COMMIT_SHA}" \
        --label "org.opencontainers.image.created=${CREATED}" \
        --label "org.opencontainers.image.source=${PROJECT_URL}" \
        --label "org.opencontainers.image.documentation=${IMG_DOCUMENTATION:-}" \
        --label "org.opencontainers.image.licenses=Apache-2.0" \
        --label "org.opencontainers.image.vendor=XorHub" \
        --label "io.xorhub.waas.os=${IMG_OS:-}" \
        --label "io.xorhub.waas.layer=${IMG_LAYER:-}" \
        --label "io.xorhub.waas.profile=${IMG_PROFILE:-standard}" \
        --label "io.xorhub.waas.parent=${PARENT}" \
        -t "${IMAGE}:${ARCH_TAG}" \
        "$@" \
        "${IMG_CONTEXT}"
}

# PRs read the cache but don't write it: per-PR-ref caches blew the
# 10 GB repo quota. Unset (local runs) keeps writing.
CACHE_TO_FLAG="--cache-to type=gha,scope=${IMG_NAME}-${ARCH},mode=max"
if [ "${GITHUB_EVENT_NAME:-}" = "pull_request" ]; then
    CACHE_TO_FLAG=""
fi

log "build (${IMG_ARCH}) ${IMAGE}:${ARCH_TAG}"
# --no-cache on this invocation only (NO_CACHE is never set on PRs, only
# by workflow_dispatch / the weekly cron on main). Where it --cache-to's,
# the push build below hits the fresh cache; on PRs it hits this job's
# local BuildKit cache instead. Either way the pushed artifact stays
# identical to what was smoke-tested.
# shellcheck disable=SC2086
buildx_build --load ${NO_CACHE_FLAG} ${CACHE_TO_FLAG}

log "smoke test"
SMOKE_IMAGE="${IMAGE}:${ARCH_TAG}" sh "$(dirname "$0")/smoke_test.sh"

# Same build with a registry output in OCI mediatypes rather than
# `docker push`: a Docker manifest list cannot carry the index
# annotations merge_image.sh sets.
log "push ${IMAGE}:${ARCH_TAG} (OCI mediatypes)"
buildx_build --output type=registry,oci-mediatypes=true
