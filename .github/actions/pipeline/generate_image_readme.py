#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pyyaml==6.0.2",
# ]
# ///
"""Render one documentation section per image into the GitHub Actions
job summary ($GITHUB_STEP_SUMMARY), or stdout outside CI.

Never committed: regenerated from the manifests every run, so it cannot
drift. The durable usage contract lives in CONTRACT.md/README.md, which
is what org.opencontainers.image.documentation points at.

VNC is documented unconditionally: it is the only remote-access protocol
any image serves, whatever the manifest's smoke: block probes.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_pipeline as gp  # noqa: E402

WAAS_URL = "https://github.com/XoRHub/waas"


def render(v: dict, *, heading: str = "#") -> str:
    title = v["description"] or v["name"]
    url = gp.project_url()
    lines = [
        f"{heading} {title}",
        "",
        f"Image `{v['name']}` — layer `{v['layer']}`, OS `{v['os']}`.",
        "",
        f"Built by [{url.rsplit('/', 1)[-1]}]({url}), deployed by the "
        f"[WaaS platform]({WAAS_URL}).",
        "",
        f"{heading}# Protocols",
        "",
        "- **VNC** — port `5901`. Required env: `WAAS_DESKTOP_PASSWORD` "
        "(session password; refuses to start without it). Optional: "
        "`WAAS_VNC_RESOLUTION` (default `1920x1080`), "
        "`WAAS_VNC_COL_DEPTH` (default `24`).",
        "",
    ]
    return "\n".join(lines)


def render_summary(variants: dict[str, dict]) -> str:
    lines = [
        "# Image documentation",
        "",
        "Generated for this run by `generate_image_readme.py` — not committed. "
        f"See [README.md]({gp.project_url()}/blob/main/README.md) for the "
        "durable usage contract.",
        "",
    ]
    for _name, v in sorted(variants.items()):
        lines.append(render(v, heading="##"))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    cfg = gp.load_config(root)
    variants = gp.flatten_variants(gp.load_manifests(root, cfg["layers"]), cfg)
    summary = render_summary(variants)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(summary)
        print(f"appended {len(variants)} image doc section(s) to GITHUB_STEP_SUMMARY")
    else:
        print(summary)


if __name__ == "__main__":
    main()
