# Local developer loop over the same scripts CI runs.
#
#   make build IMAGE=core-ubuntu-noble   # any variant of the manifests
#   make run   IMAGE=core-ubuntu-noble   # VNC on localhost:15901, password devpassword
#   make smoke IMAGE=core-ubuntu-noble
#   make lint                            # hadolint + shellcheck + actionlint (docker)
#   make test                            # toolkit unit tests (uv)

IMAGE    ?= core-ubuntu-noble
REGISTRY ?= waas-local
TAG      := $(REGISTRY)/$(IMAGE):dev
PIPELINE := uv run .github/actions/pipeline/generate_pipeline.py

.PHONY: build run smoke lint test clean

# Build args, contexts and Dockerfile come from the manifests, exactly
# as CI resolves them.
build:
	docker build $$($(PIPELINE) --build-flags $(IMAGE)) -t $(TAG)

run: build
	docker run --rm -it \
		--read-only --cap-drop ALL --security-opt no-new-privileges \
		--tmpfs /tmp --tmpfs /home/waas_user:mode=1777 \
		-p 15901:5901 \
		-e WAAS_DESKTOP_PASSWORD=devpassword \
		$(TAG)

smoke: build
	SMOKE_IMAGE=$(TAG) SMOKE_VNC=1 SMOKE_AUDIO=1 sh .github/actions/build/smoke_test.sh

lint:
	docker run --rm -v "$(CURDIR):/mnt" -w /mnt ghcr.io/hadolint/hadolint:v2.15.1-alpine \
		hadolint --failure-threshold warning $$(find . -name Dockerfile -o -name Dockerfile.generated)
	docker run --rm -v "$(CURDIR):/mnt" -w /mnt koalaman/shellcheck:stable -S warning \
		.github/actions/lib.sh .github/actions/*/*.sh base/rootfs/usr/local/bin/*
	docker run --rm -v "$(CURDIR):/repo" -w /repo rhysd/actionlint:1.7.12 -color

test:
	uv run --with pyyaml==6.0.2 python -m unittest discover -s ci/tests -v

clean:
	docker rmi -f $(TAG) 2>/dev/null || true
