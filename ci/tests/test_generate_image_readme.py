"""Format-contract tests for generate_image_readme.py. stdlib unittest only."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".github" / "actions" / "pipeline"))
import generate_image_readme as gir  # noqa: E402
import generate_pipeline as gp  # noqa: E402

CFG = {
    "layers": ["base"],
    "os": {"ubuntu-noble": {}},
    "defaults": {"os": "ubuntu-noble", "archs": ["linux/amd64"]},
}

MANIFEST = {
    "name": "ubuntu-core",
    "layer": "base",
    "context": "base/ubuntu",
    "dockerfile": None,
    "description": "Headless graphical session over VNC.",
    "variants": [
        {"name": "core-ubuntu-noble", "smoke": {"vnc": True, "audio": True}},
        # Not probed over VNC by CI: the protocol section must not depend on smoke.vnc.
        {"name": "core-debian-13", "smoke": {"audio": True}},
    ],
}


class RenderFormat(unittest.TestCase):
    def setUp(self):
        self.variants = gp.flatten_variants([MANIFEST], CFG)

    def test_title_falls_back_to_description(self):
        v = self.variants["core-ubuntu-noble"]
        self.assertTrue(gir.render(v).startswith(f"# {v['description']}"))

    def test_vnc_is_the_only_protocol_always_documented(self):
        for v in self.variants.values():
            out = gir.render(v)
            self.assertIn("**VNC**", out)
            self.assertIn("WAAS_DESKTOP_PASSWORD", out)
            for banned in ("**RDP**", "**SSH**", "WAAS_RDP_", "WAAS_SSH_"):
                self.assertNotIn(banned, out)

    def test_links_project_and_waas(self):
        out = gir.render(self.variants["core-ubuntu-noble"])
        self.assertIn(gp.project_url(), out)
        self.assertIn(gir.WAAS_URL, out)

    def test_heading_level_is_configurable(self):
        out = gir.render(self.variants["core-ubuntu-noble"], heading="##")
        self.assertTrue(out.startswith("## "))
        self.assertIn("### Protocols", out)


class RenderSummary(unittest.TestCase):
    def test_includes_every_variant(self):
        summary = gir.render_summary(gp.flatten_variants([MANIFEST], CFG))
        self.assertIn("core-ubuntu-noble", summary)
        self.assertIn("core-debian-13", summary)

    def test_links_readme(self):
        self.assertIn("README.md", gir.render_summary({}))


if __name__ == "__main__":
    unittest.main()
