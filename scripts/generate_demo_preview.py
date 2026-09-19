"""Derive the public README GIF only from the verified synthetic demo MP4."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "docs" / "media"
MANIFEST = MEDIA / "manifest.json"
SOURCE = MEDIA / "demo.mp4"
PREVIEW = MEDIA / "demo.gif"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required on PATH")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("provenance") != "synthetic":
        raise RuntimeError("source media is not marked synthetic")
    mp4 = next((item for item in manifest.get("assets", [])
                if item.get("path") == "docs/media/demo.mp4"), None)
    if mp4 is None or sha256(SOURCE.read_bytes()) != mp4.get("sha256"):
        raise RuntimeError("demo.mp4 does not match the synthetic media manifest")

    # Compress the full 35-second browser walkthrough into about 15 seconds.
    # A single filter graph derives both palette and frames from the same input.
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(SOURCE),
        "-filter_complex",
        "[0:v]setpts=0.42*PTS,fps=6,scale=960:-1:flags=lanczos,split[a][b];"
        "[a]palettegen=stats_mode=diff[p];"
        "[b][p]paletteuse=dither=bayer:bayer_scale=5",
        "-an", "-map_metadata", "-1", "-map_chapters", "-1", "-loop", "0", str(PREVIEW),
    ], check=True)

    asset = {"path": "docs/media/demo.gif", "sha256": sha256(PREVIEW.read_bytes())}
    assets = [item for item in manifest["assets"] if item.get("path") != asset["path"]]
    assets.append(asset)
    manifest["assets"] = assets
    source = {"path": "scripts/generate_demo_preview.py",
              "sha256": sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n"))}
    inputs = [item for item in manifest["inputs"] if item.get("path") != source["path"]]
    inputs.append(source)
    generator_path = ROOT / "scripts" / "generate_public_media.py"
    spec = importlib.util.spec_from_file_location("public_media_generator", generator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("synthetic media generator cannot be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for item in inputs:
        if item["path"] == "scripts/generate_public_media.py":
            item["sha256"] = sha256(generator_path.read_bytes().replace(b"\r\n", b"\n"))
        elif item["path"] == "synthetic-graph-json":
            item["sha256"] = sha256(module.canonical_graph_bytes(module.fabricated_graph()))
    manifest["inputs"] = inputs
    manifest["fixture_float_decimals"] = 9
    manifest["preview"] = {
        "source": mp4["path"],
        "source_sha256": mp4["sha256"],
        "output": asset["path"],
        "generator": source["path"],
    }
    MANIFEST.write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    print(f"Generated {asset['path']} ({PREVIEW.stat().st_size:,} bytes) from verified synthetic MP4")


if __name__ == "__main__":
    main()
