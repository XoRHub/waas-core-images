"""Contract tests for .github/actions/pipeline/generate_pipeline.py:
manifest flattening, parent resolution, selective builds, carry-forward
and the emitted matrices. stdlib unittest only."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".github" / "actions" / "pipeline"))
import generate_pipeline as gp  # noqa: E402

CORE_REF = "ghcr.io/xorhub/waas-core-images/core-ubuntu-noble:1.0.0@sha256:" + "a" * 64

CFG = {
    "layers": ["base"],
    "os": {"ubuntu-noble": {"buildArgs": {"OS_BASE_IMAGE": "ubuntu:24.04"}}},
    "defaults": {"os": "ubuntu-noble", "archs": ["linux/amd64", "linux/arm64"]},
}

WORKSPACE_CFG = {
    "layers": ["desktop", "apps"],
    "os": {"ubuntu-noble": {}},
    "defaults": {"os": "ubuntu-noble", "archs": ["linux/amd64", "linux/arm64"]},
    "cores": {"core-ubuntu-noble": CORE_REF},
}


def manifest(name, context, variants, **extra):
    return {"name": name, "layer": context.split("/")[0], "context": context,
            "dockerfile": None, "description": f"{name} image", "variants": variants, **extra}


CORE = manifest("ubuntu-core", "base/ubuntu",
                [{"name": "core-ubuntu-noble", "smoke": {"vnc": True, "audio": True}},
                 {"name": "core-debian-13", "smoke": {"vnc": True}}],
                buildContexts={"rootfs": "base/rootfs"})
FEDORA = manifest("fedora-core", "base/fedora", [{"name": "core-fedora-43"}],
                  buildContexts={"rootfs": "base/rootfs"})

DESKTOP = manifest("ubuntu-desktop", "desktop/xfce",
                   [{"name": "ubuntu-desktop-noble", "smoke": {"vnc": True}}],
                   **{"from": "core-ubuntu-noble"})
FIREFOX = manifest("firefox", "apps/firefox",
                   [{"name": "firefox", "smoke": {"vnc": True}, "archs": ["linux/amd64"]}],
                   **{"from": "core-ubuntu-noble"})
DEVTOOLS = manifest("devtools", "apps/devtools",
                    [{"name": "devtools", "smoke": {"vnc": True}},
                     {"name": "devtools-dev", "profile": "dev",
                      "buildArgs": {"INSTALL_SUDO": "1"}, "smoke": {"vnc": True}}],
                    **{"from": "ubuntu-desktop-noble"})


def workspace_variants():
    variants = gp.flatten_variants([DESKTOP, FIREFOX, DEVTOOLS], WORKSPACE_CFG)
    gp.resolve_parents(variants, WORKSPACE_CFG["cores"])
    return variants


class FlattenVariants(unittest.TestCase):
    def test_smoke_keys_are_a_closed_set(self):
        for key in ("rdp", "ssh"):
            with self.assertRaises(SystemExit):
                gp.flatten_variants(
                    [manifest("x", "base/x", [{"name": "x", "smoke": {key: True}}])], CFG)

    def test_no_version_field_needed(self):
        variants = gp.flatten_variants([CORE], CFG)
        self.assertEqual(set(variants), {"core-ubuntu-noble", "core-debian-13"})
        self.assertNotIn("version", variants["core-ubuntu-noble"])

    def test_install_sudo_requires_dev_suffix_and_profile(self):
        bad = manifest("x", "apps/x", [{"name": "x", "buildArgs": {"INSTALL_SUDO": "1"}}])
        with self.assertRaises(SystemExit):
            gp.flatten_variants([bad], CFG)
        bad = manifest("x", "apps/x", [{"name": "x-dev", "buildArgs": {"INSTALL_SUDO": "1"}}])
        with self.assertRaises(SystemExit):
            gp.flatten_variants([bad], CFG)

    def test_dev_profile_bakes_marker(self):
        variants = workspace_variants()
        self.assertEqual(variants["devtools-dev"]["build_args"]["WAAS_PROFILE"], "dev")
        self.assertNotIn("WAAS_PROFILE", variants["devtools"]["build_args"])


class ResolveParents(unittest.TestCase):
    def test_internal_and_external(self):
        variants = workspace_variants()
        self.assertEqual(variants["ubuntu-desktop-noble"]["base_image"], CORE_REF)
        self.assertIsNone(variants["ubuntu-desktop-noble"]["parent"])
        self.assertEqual(variants["devtools"]["parent"], "ubuntu-desktop-noble")
        self.assertEqual(variants["devtools"]["base_image"], "")

    def test_unknown_parent_is_refused(self):
        variants = gp.flatten_variants([DESKTOP], CFG)
        with self.assertRaises(SystemExit):
            gp.resolve_parents(variants, {})

    def test_ambiguous_parent_is_refused(self):
        variants = gp.flatten_variants([CORE, DESKTOP], CFG)
        with self.assertRaises(SystemExit):
            gp.resolve_parents(variants, {"core-ubuntu-noble": CORE_REF})

    def test_depth_counts_in_pipeline_parents_only(self):
        variants = workspace_variants()
        self.assertEqual(gp.stage_of(variants, "ubuntu-desktop-noble"), 0)
        self.assertEqual(gp.stage_of(variants, "firefox"), 0)
        self.assertEqual(gp.stage_of(variants, "devtools"), 1)

    def test_child_cannot_exceed_internal_parent_archs(self):
        variants = workspace_variants()
        gp.validate_archs(variants)
        variants["devtools"]["archs"] = ["linux/amd64", "linux/riscv64"]
        with self.assertRaises(SystemExit):
            gp.validate_archs(variants)


class SelectRebuilds(unittest.TestCase):
    def setUp(self):
        self.variants = workspace_variants()
        self.all = set(self.variants)

    def select(self, changed, old_cfg=WORKSPACE_CFG, cfg=WORKSPACE_CFG):
        return gp.select_rebuilds(self.variants, cfg, (set(changed), old_cfg))

    def test_no_base_revision_rebuilds_everything(self):
        self.assertEqual(gp.select_rebuilds(self.variants, WORKSPACE_CFG, None), self.all)

    def test_unrelated_change_rebuilds_nothing(self):
        self.assertEqual(self.select(["README.md", "Makefile", "ci/tests/x.py"]), set())

    def test_context_change_rebuilds_variant_and_children(self):
        self.assertEqual(self.select(["apps/firefox/Dockerfile"]), {"firefox"})
        self.assertEqual(self.select(["desktop/xfce/Dockerfile"]),
                         {"ubuntu-desktop-noble", "devtools", "devtools-dev"})

    def test_context_prefix_is_a_directory(self):
        self.assertEqual(self.select(["apps/firefox-extra/x"]), set())

    def test_cores_entry_change_rebuilds_dependents(self):
        old = {**WORKSPACE_CFG, "cores": {"core-ubuntu-noble": CORE_REF.replace("a" * 64, "b" * 64)}}
        self.assertEqual(self.select(["images.yaml"], old_cfg=old), self.all)

    def test_shared_images_yaml_change_rebuilds_everything(self):
        old = {**WORKSPACE_CFG, "scan": {"severity": "CRITICAL"}}
        self.assertEqual(self.select(["images.yaml"], old_cfg=old), self.all)

    def test_toolkit_change_rebuilds_everything(self):
        self.assertEqual(self.select([".github/actions/build/build_image.sh"]), self.all)
        self.assertEqual(self.select([".github/workflows/build.yml"]), self.all)

    def test_shared_build_context_change_rebuilds_its_users(self):
        variants = gp.flatten_variants([CORE, FEDORA], CFG)
        gp.resolve_parents(variants, {})
        changed = {"base/rootfs/usr/local/bin/waas-session"}
        self.assertEqual(gp.select_rebuilds(variants, CFG, (changed, CFG)), set(variants))
        changed = {"base/fedora/Dockerfile"}
        self.assertEqual(gp.select_rebuilds(variants, CFG, (changed, CFG)), {"core-fedora-43"})


class ChangesSince(unittest.TestCase):
    """changes_since against a real git repository."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        (self.root / "images.yaml").write_text("layers: [base]\ncores:\n  core: a\n")
        (self.root / "base/x").mkdir(parents=True)
        (self.root / "base/x/Dockerfile").write_text("FROM scratch\n")
        self.git("add", "."), self.git("commit", "-q", "-m", "one")
        self.first = self.git("rev-parse", "HEAD").strip()
        (self.root / "images.yaml").write_text("layers: [base]\ncores:\n  core: b\n")
        (self.root / "base/x/Dockerfile").write_text("FROM scratch\nUSER 1000\n")
        self.git("commit", "-qam", "two")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout

    def test_returns_changed_paths_and_old_config(self):
        changed, old = gp.changes_since(self.root, self.first)
        self.assertEqual(changed, {"images.yaml", "base/x/Dockerfile"})
        self.assertEqual(old["cores"], {"core": "a"})

    def test_unknown_revision_is_none(self):
        self.assertIsNone(gp.changes_since(self.root, "0" * 40))


class Emit(unittest.TestCase):
    def setUp(self):
        self.variants = workspace_variants()

    def test_carry_forward_lists_variants_not_rebuilt(self):
        out = gp.emit(self.variants, {"firefox"}, "native", {}, gp.GH_MAX_DEPTH)
        self.assertEqual([c["IMG_NAME"] for c in out["carry"]],
                         ["ubuntu-desktop-noble", "devtools", "devtools-dev"])
        self.assertEqual([e["IMG_NAME"] for e in out["layer0"]], ["firefox"])
        self.assertEqual(out["layer1"], [])
        self.assertEqual([e["IMG_NAME"] for e in out["merge0"]], ["firefox"])
        self.assertEqual([v["IMG_NAME"] for v in out["variants"]],
                         sorted(self.variants))

    def test_matrices_by_depth_and_arch(self):
        out = gp.emit(self.variants, set(self.variants), "native", {}, gp.GH_MAX_DEPTH)
        self.assertEqual(out["carry"], [])
        self.assertEqual([(e["IMG_NAME"], e["IMG_ARCH"], e["runner"]) for e in out["layer0"]],
                         [("firefox", "linux/amd64", "ubuntu-24.04"),
                          ("ubuntu-desktop-noble", "linux/amd64", "ubuntu-24.04"),
                          ("ubuntu-desktop-noble", "linux/arm64", "ubuntu-24.04-arm")])
        self.assertEqual({e["IMG_NAME"] for e in out["layer1"]}, {"devtools", "devtools-dev"})
        self.assertEqual(out["scan"], {"severity": "HIGH,CRITICAL", "ignoreUnfixed": True})
        json.dumps(out)

    def test_qemu_strategy_routes_to_amd64_runners(self):
        out = gp.emit(self.variants, set(self.variants), "qemu", {}, gp.GH_MAX_DEPTH)
        self.assertEqual({e["runner"] for e in out["layer0"] + out["layer1"]}, {"ubuntu-24.04"})

    def test_depth_beyond_workflow_wiring_is_refused(self):
        with self.assertRaises(SystemExit):
            gp.emit(self.variants, set(self.variants), "native", {}, 0)

    def test_build_flags_match_the_matrix(self):
        variants = gp.flatten_variants([CORE], CFG)
        gp.resolve_parents(variants, {})
        debian = variants["core-debian-13"]
        debian["os"] = "debian-13"
        debian["build_args"]["OS_BASE_IMAGE"] = "debian:13-slim"
        self.assertEqual(
            gp.build_flags(debian),
            "--build-arg OS_BASE_IMAGE=debian:13-slim --build-context rootfs=base/rootfs base/ubuntu")
        self.assertEqual(gp.image_vars(debian)["IMG_BUILD_CONTEXTS"], "rootfs=base/rootfs")

    def test_image_vars_carry_parent_kind(self):
        desktop = gp.image_vars(self.variants["ubuntu-desktop-noble"])
        self.assertEqual(desktop["IMG_BASE_IMAGE"], CORE_REF)
        self.assertEqual(desktop["IMG_PARENT"], "")
        devtools = gp.image_vars(self.variants["devtools-dev"])
        self.assertEqual(devtools["IMG_PARENT"], "ubuntu-desktop-noble")
        self.assertEqual(devtools["IMG_BASE_IMAGE"], "")
        self.assertIn("INSTALL_SUDO=1", devtools["IMG_BUILD_ARGS"].split())
        self.assertEqual(devtools["SMOKE_PROFILE"], "dev")


class LoadConfig(unittest.TestCase):
    def test_layers_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "images.yaml").write_text("os: {}\n")
            with self.assertRaises(SystemExit):
                gp.load_config(Path(tmp))

    def test_this_repo_generates(self):
        root = Path(__file__).resolve().parents[2]
        cfg = gp.load_config(root)
        variants = gp.flatten_variants(gp.load_manifests(root, cfg["layers"]), cfg)
        gp.resolve_parents(variants, cfg.get("cores", {}))
        gp.validate_archs(variants)
        self.assertEqual(set(variants), {"core-ubuntu-noble", "core-debian-13", "core-fedora-43"})
        out = gp.emit(variants, set(variants), "native", cfg["scan"], 0)
        self.assertEqual(len(out["layer0"]), 6)


if __name__ == "__main__":
    unittest.main()
