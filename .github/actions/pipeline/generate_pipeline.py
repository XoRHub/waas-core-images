#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pyyaml==6.0.2",
# ]
# ///
"""Generate the GitHub Actions build matrices from images.yaml + manifests.

Discovery: every <layer>/*/manifest.yaml (layers: in images.yaml)
becomes, per variant, one native build job per architecture plus one
merge job assembling the arch tags into a manifest list. Jobs are
grouped by dependency depth: a variant whose `from` names another
variant of this pipeline builds one depth later, FROM the parent's
manifest list of the same commit. A `from` naming an entry of the
optional `cores:` map is an external parent (registry/ref:tag@digest,
passed straight as BASE_IMAGE, no build job).

Selective builds (--since <rev>): a variant is rebuilt iff its context
directory (or one of its `buildContexts:`) changed, its in-pipeline
parent is rebuilt, its `cores:`
entry changed, or a shared input changed (images.yaml outside `cores:`,
anything under .github/) — in which case everything is rebuilt. No
--since (or --all) rebuilds everything. Variants not rebuilt are listed
under `carry` so the workflow retags their `edge` digest to the current
commit: every commit carries the full image set.

Output (github-matrices.json): layer<d>/merge<d> matrices for d in
0..GH_MAX_DEPTH, `carry`, `variants` (all), `scan`.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recipe_compiler  # noqa: E402

GH_RUNNERS = {"linux/amd64": "ubuntu-24.04", "linux/arm64": "ubuntu-24.04-arm"}
# Matrices are always emitted for depths 0..GH_MAX_DEPTH; --max-depth
# says how many the calling workflow actually wires.
GH_MAX_DEPTH = 2
SMOKE_KEYS = {"vnc", "audio", "env"}
# A change here means the toolkit or the workflow changed: rebuild all.
SHARED_PATHS = (".github/",)


def project_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "XoRHub/waas-core-images")
    return f"{server}/{repo}"


def load_config(root: Path) -> dict:
    cfg = yaml.safe_load((root / "images.yaml").read_text()) or {}
    if not cfg.get("layers"):
        sys.exit("images.yaml: `layers:` must list the directories to scan")
    return cfg


def load_manifests(root: Path, layers: list[str]) -> list[dict]:
    manifests = []
    for layer in layers:
        for mf in sorted(root.glob(f"{layer}/*/manifest.yaml")):
            m = yaml.safe_load(mf.read_text())
            m["context"] = str(mf.parent.relative_to(root))
            m["dockerfile"] = recipe_compiler.compile_recipe(m, mf.parent, root)
            manifests.append(m)
    if not manifests:
        sys.exit("no manifests found — nothing to build")
    return manifests


def flatten_variants(manifests: list[dict], cfg: dict) -> dict[str, dict]:
    """One entry per publishable image, keyed by variant name."""
    defaults = cfg.get("defaults", {})
    variants: dict[str, dict] = {}
    for m in manifests:
        for v in m.get("variants", []):
            name = v["name"]
            if name in variants:
                sys.exit(f"duplicate variant name {name!r}")
            os_key = v.get("os", m.get("os", defaults.get("os")))
            if os_key not in cfg.get("os", {}):
                sys.exit(f"{name}: unknown os {os_key!r} (not in images.yaml)")
            build_args = {
                **defaults.get("buildArgs", {}),
                **cfg["os"][os_key].get("buildArgs", {}),
                **m.get("buildArgs", {}),
                **v.get("buildArgs", {}),
            }
            # Reduced-hardening variants must be impossible to ship under
            # an innocuous name: the -dev suffix, profile: dev and the
            # relaxed smoke are tied together here.
            profile = v.get("profile", "standard")
            if profile not in ("standard", "dev"):
                sys.exit(f"{name}: profile must be standard|dev, got {profile!r}")
            if str(build_args.get("INSTALL_SUDO", "0")) == "1":
                if not name.endswith("-dev"):
                    sys.exit(f"{name}: INSTALL_SUDO=1 requires the -dev name suffix")
                if profile != "dev":
                    sys.exit(f"{name}: INSTALL_SUDO=1 requires profile: dev")
            if profile == "dev":
                build_args.setdefault("WAAS_PROFILE", "dev")
            smoke = v.get("smoke", {})
            unknown = set(smoke) - SMOKE_KEYS
            if unknown:
                sys.exit(f"{name}: unknown smoke key(s) {sorted(unknown)} "
                         f"— allowed: {sorted(SMOKE_KEYS)}")
            variants[name] = {
                "name": name,
                "context": m["context"],
                "dockerfile": m["dockerfile"],
                "os": os_key,
                "layer": m.get("layer", m["context"].split("/", 1)[0]),
                "description": " ".join(str(m.get("description", "")).split()),
                "profile": profile,
                "from": v.get("from", m.get("from")),
                "parent": None,       # in-pipeline parent, set by resolve_parents
                "base_image": "",     # external parent ref, set by resolve_parents
                "archs": v.get("archs", m.get("archs", defaults.get("archs", []))),
                "build_args": build_args,
                # Extra buildx named contexts (root-relative paths), for
                # files shared between image directories.
                "build_contexts": v.get("buildContexts", m.get("buildContexts", {})),
                "smoke": smoke,
                # Catalog-only passthrough (never baked into the image).
                "icon": v.get("icon", m.get("icon", "")),
                "displayName": v.get("displayName", m.get("displayName", "")),
                "env": v.get("env", m.get("env", [])),
            }
    return variants


def resolve_parents(variants: dict[str, dict], cores: dict[str, str]) -> None:
    """`from` names either a variant of this pipeline or a `cores:` entry."""
    for name, v in variants.items():
        parent = v["from"]
        if not parent:
            continue
        if parent in variants and parent in cores:
            sys.exit(f"{name}: parent {parent!r} is both a variant and a cores: entry")
        if parent in variants:
            v["parent"] = parent
        elif parent in cores:
            v["base_image"] = str(cores[parent])
        else:
            sys.exit(f"{name}: unknown parent image {parent!r}")


def stage_of(variants: dict[str, dict], name: str) -> int:
    parent = variants[name]["parent"]
    return stage_of(variants, parent) + 1 if parent else 0


def validate_archs(variants: dict[str, dict]) -> None:
    for name, v in variants.items():
        for arch in v["archs"]:
            if arch not in GH_RUNNERS:
                sys.exit(f"{name}: no runner mapped for arch {arch!r}")
        if v["parent"]:
            missing = set(v["archs"]) - set(variants[v["parent"]]["archs"])
            if missing:
                sys.exit(f"{name}: parent {v['parent']!r} does not build {sorted(missing)}")


def changes_since(root: Path, since: str) -> tuple[set[str], dict] | None:
    """Paths changed between <since> and HEAD, plus images.yaml as of
    <since>. None when <since> is unknown to this checkout."""
    def git(*args: str) -> str | None:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    diff = git("diff", "--name-only", since, "HEAD")
    old = git("show", f"{since}:images.yaml")
    if diff is None or old is None:
        return None
    return set(diff.split()), (yaml.safe_load(old) or {})


def select_rebuilds(variants: dict[str, dict], cfg: dict,
                    changes: tuple[set[str], dict] | None) -> set[str]:
    everything = set(variants)
    if changes is None:
        return everything
    changed, old_cfg = changes
    shared = {k: v for k, v in cfg.items() if k != "cores"}
    old_shared = {k: v for k, v in old_cfg.items() if k != "cores"}
    if shared != old_shared or any(p.startswith(SHARED_PATHS) for p in changed):
        return everything
    cores, old_cores = cfg.get("cores", {}), old_cfg.get("cores", {})

    def touched(v: dict) -> bool:
        dirs = [v["context"], *v["build_contexts"].values()]
        return any(p.startswith(d.rstrip("/") + "/") for p in changed for d in dirs)

    rebuild = {
        name for name, v in variants.items()
        if touched(v)
        or (v["base_image"] and cores[v["from"]] != old_cores.get(v["from"]))
    }
    grown = True
    while grown:
        children = {n for n, v in variants.items() if v["parent"] in rebuild}
        grown = not children <= rebuild
        rebuild |= children
    return rebuild


def image_vars(v: dict) -> dict:
    """The IMG_*/SMOKE_* env contract of the build/merge/scan/sbom scripts."""
    out = {
        "IMG_NAME": v["name"],
        "IMG_CONTEXT": v["context"],
        "IMG_ARCHS": ",".join(v["archs"]),
        "IMG_OS": v["os"],
        "IMG_LAYER": v["layer"],
        "IMG_DESCRIPTION": v["description"],
        "IMG_PROFILE": v["profile"],
        "IMG_PARENT": v["parent"] or "",
        "IMG_BASE_IMAGE": v["base_image"],
        "IMG_BUILD_ARGS": " ".join(
            f"{k}={val}" for k, val in sorted(v["build_args"].items())),
        "IMG_BUILD_CONTEXTS": " ".join(
            f"{k}={val}" for k, val in sorted(v["build_contexts"].items())),
        "IMG_DOCUMENTATION": f"{project_url()}/blob/main/README.md",
        "SMOKE_PROFILE": v["profile"],
        "SMOKE_VNC": "1" if v["smoke"].get("vnc") else "0",
        "SMOKE_AUDIO": "1" if v["smoke"].get("audio") else "0",
        "SMOKE_ENV": " ".join(
            f"{k}={val}" for k, val in sorted(v["smoke"].get("env", {}).items())),
    }
    if v["dockerfile"]:
        out["IMG_DOCKERFILE"] = v["dockerfile"]
    return out


def emit(variants: dict[str, dict], rebuild: set[str], strategy: str,
         scan_cfg: dict, max_depth: int) -> dict:
    depths = {name: stage_of(variants, name) for name in variants}
    deepest = max(depths.values(), default=0)
    if deepest > max_depth:
        sys.exit(f"layer depth {deepest} exceeds the {max_depth} wired into the "
                 "workflow — add a layer-N/merge-N job pair and raise max-depth")
    out: dict[str, object] = {}
    for d in range(GH_MAX_DEPTH + 1):
        out[f"layer{d}"] = []
        out[f"merge{d}"] = []
    out["carry"] = []
    for name, v in sorted(variants.items(), key=lambda kv: (depths[kv[0]], kv[0])):
        if name not in rebuild:
            out["carry"].append({"IMG_NAME": name})
            continue
        common = image_vars(v)
        for arch in v["archs"]:
            runner = GH_RUNNERS["linux/amd64"] if strategy == "qemu" else GH_RUNNERS[arch]
            out[f"layer{depths[name]}"].append({**common, "IMG_ARCH": arch, "runner": runner})
        out[f"merge{depths[name]}"].append(common)
    out["variants"] = [{"IMG_NAME": name} for name in sorted(variants)]
    out["scan"] = {
        "severity": scan_cfg.get("severity", "HIGH,CRITICAL"),
        "ignoreUnfixed": scan_cfg.get("ignoreUnfixed", True),
    }
    return out


def build_flags(v: dict) -> str:
    """`docker build` arguments for a local build of one variant (the
    Makefile); build_image.sh derives the same from the matrix env."""
    flags = [f"--build-arg {k}={val}" for k, val in sorted(v["build_args"].items())]
    flags += [f"--build-context {k}={val}" for k, val in sorted(v["build_contexts"].items())]
    if v["dockerfile"]:
        flags.append(f"-f {v['context']}/{v['dockerfile']}")
    return " ".join([*flags, v["context"]])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--since", default="", help="base git revision for selective builds")
    parser.add_argument("--all", action="store_true", help="rebuild every variant")
    parser.add_argument("--max-depth", type=int, default=GH_MAX_DEPTH)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--build-flags", metavar="VARIANT",
                        help="print the docker build arguments of one variant and exit")
    args = parser.parse_args()

    # WAAS_IMAGES_BUILD_STRATEGY=qemu routes every build to the amd64
    # fleet under emulation (arm fleet down). Same gates, just slower.
    strategy = os.environ.get("WAAS_IMAGES_BUILD_STRATEGY", "native")
    if strategy not in ("native", "qemu"):
        sys.exit(f"WAAS_IMAGES_BUILD_STRATEGY must be native|qemu, got {strategy!r}")
    root = args.root.resolve()
    cfg = load_config(root)
    variants = flatten_variants(load_manifests(root, cfg["layers"]), cfg)
    resolve_parents(variants, cfg.get("cores", {}))
    validate_archs(variants)

    if args.build_flags:
        if args.build_flags not in variants:
            sys.exit(f"unknown variant {args.build_flags!r}: {', '.join(sorted(variants))}")
        print(build_flags(variants[args.build_flags]))
        return

    changes = None
    if args.since and not args.all:
        changes = changes_since(root, args.since)
        if changes is None:
            print(f"warning: {args.since!r} not in this checkout, rebuilding everything",
                  file=sys.stderr)
    rebuild = select_rebuilds(variants, cfg, changes)

    out = args.output or root / "github-matrices.json"
    out.write_text(json.dumps(emit(variants, rebuild, strategy, cfg.get("scan", {}),
                                   args.max_depth)))
    print(f"generated {out.name}: {len(rebuild)} of {len(variants)} image(s) to build, "
          f"{len(variants) - len(rebuild)} carried forward, strategy={strategy}")


if __name__ == "__main__":
    main()
