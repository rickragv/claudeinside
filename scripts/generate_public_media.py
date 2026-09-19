"""Regenerate README screenshots and a silent MP4 from fabricated session data.

Only the rows built in this file are passed to the report renderer. This script
does not discover or read local Claude transcripts or existing report artifacts.
Requires Playwright Chromium and ffmpeg on PATH.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from claude_insight.graph import graph_data, render_graph  # noqa: E402


MEDIA = ROOT / "docs" / "media"
VIEWPORT = {"width": 1600, "height": 900}
START = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
PROMPTS = (
    "Map the Northstar Notes sample app",
    "Find the note search entry points",
    "Trace the sample import flow",
    "Check how labels are indexed",
    "Review the fictional sync settings",
    "Add a test for empty search",
    "Run the sample test suite",
    "Inspect the failed search assertion",
    "Fix the sample search result order",
    "Run the search tests again",
    "Measure import progress reporting",
    "Review note creation errors",
    "Add a test for duplicate labels",
    "Check the sample storage adapter",
    "Improve the empty state copy",
    "Verify keyboard shortcuts",
    "Inspect the demo accessibility report",
    "Adjust focus order in the editor",
    "Run the browser checks",
    "Review the fictional findings",
    "Test a failed sync retry",
    "Inspect the retry error response",
    "Correct the sample retry delay",
    "Check the retry test again",
)
TOOLS = ("Read", "Grep", "Edit", "Bash", "Read", "Agent")


def canonical_graph_bytes(value: dict) -> bytes:
    """Hash the synthetic fixture across Python floating-sum implementations.

    Only provenance uses this 9-decimal normalization; rendered analytics keep
    their original values. A nanodollar cost difference cannot change the hash.
    """
    def normalize(item):
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("synthetic graph has a nonfinite number")
            return round(item, 9) or 0.0
        if isinstance(item, dict):
            return {key: normalize(entry) for key, entry in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(entry) for entry in item]
        return item

    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def stamp(seconds: int) -> str:
    return (START + timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")


def fabricated_graph() -> dict:
    """Create a deterministic, fictional 72-turn session without filesystem input."""
    rows = []
    parent = None
    for turn in range(72):
        number = turn + 1
        base = turn * 46
        prompt = PROMPTS[turn % len(PROMPTS)]
        tool_name = TOOLS[turn % len(TOOLS)]
        user_id, assistant_id, result_id = f"u-{number}", f"a-{number}", f"r-{number}"
        tool_id = f"sample-tool-{number}"
        error = turn in (7, 20, 34, 53, 67)
        input_data = {
            "Read": {"file_path": "src/northstar/search.py"},
            "Grep": {"query": "sample_index"},
            "Edit": {"file_path": "tests/test_sample_search.py"},
            "Bash": {"command": "python -m pytest tests/test_sample_search.py -q"},
            "Agent": {"description": "Review fictional sample cases"},
        }[tool_name]
        result_text = (
            "Synthetic test failure: expected two sample notes, received one."
            if error else "Synthetic check completed for the Northstar Notes example."
        )
        rows.append({
            "type": "user", "uuid": user_id, "parentUuid": parent,
            "sessionId": "northstar-notes-synthetic-demo",
            "cwd": "demo/northstar-notes", "timestamp": stamp(base),
            "origin": {"kind": "human"}, "message": {"content": prompt},
        })
        rows.append({
            "type": "assistant", "uuid": assistant_id, "parentUuid": user_id,
            "sessionId": "northstar-notes-synthetic-demo", "timestamp": stamp(base + 3),
            "message": {
                "id": f"sample-message-{number}",
                "model": "claude-sonnet-5" if turn < 48 else "claude-haiku-4-5-20251001",
                "content": [
                    {"type": "text", "text": f"I will inspect the fictional app for: {prompt.lower()}."},
                    {"type": "tool_use", "id": tool_id, "name": tool_name, "input": input_data},
                ],
                "usage": {
                    "input_tokens": 190 + (turn % 8) * 37,
                    "output_tokens": 42 + (turn % 6) * 14,
                    "cache_creation_input_tokens": 50 + (turn % 4) * 12,
                    "cache_read_input_tokens": 400 + turn * 31,
                    "cache_creation": {"ephemeral_5m_input_tokens": 50 + (turn % 4) * 12},
                },
            },
        })
        rows.append({
            "type": "user", "uuid": result_id, "parentUuid": assistant_id,
            "sessionId": "northstar-notes-synthetic-demo", "timestamp": stamp(base + 10 + turn % 9),
            "message": {"content": [{
                "type": "tool_result", "tool_use_id": tool_id,
                "is_error": error, "content": result_text,
            }]},
        })
        rows.append({
            "type": "assistant", "uuid": f"summary-{number}", "parentUuid": result_id,
            "sessionId": "northstar-notes-synthetic-demo", "timestamp": stamp(base + 26 + turn % 9),
            "message": {
                "id": f"sample-summary-{number}",
                "model": "claude-sonnet-5" if turn < 48 else "claude-haiku-4-5-20251001",
                "content": [{"type": "text", "text": result_text}],
                "usage": {"input_tokens": 140 + turn % 40, "output_tokens": 25 + turn % 11},
            },
        })
        parent = f"summary-{number}"
    transcript = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows)
    return graph_data("demo/northstar-notes.jsonl", lambda: io.StringIO(transcript))


def select_map_card(page, index=0) -> None:
    card = page.evaluate("""index => {
      const state = document.getElementById('claude-insight').__claudeInsight.getViewState();
      const b = state.map.nodes[index].bounds;
      return {x:(b.x1+b.x2)/2,y:(b.y1+b.y2)/2};
    }""", index)
    graph = page.locator('[data-role="graph"]').bounding_box()
    page.mouse.click(graph["x"] + card["x"], graph["y"] + card["y"])


def wait(page, seconds: float) -> None:
    page.wait_for_timeout(int(seconds * 1000))


def record(page, data: dict) -> None:
    page.locator('[data-metric="turns"]').wait_for()
    assert page.locator('[data-metric="turns"]').inner_text() == "72"
    page.screenshot(path=str(MEDIA / "overview.png"))
    wait(page, 4.5)

    page.locator('[data-view="flow"]').click()
    wait(page, 1)
    state = page.evaluate("document.getElementById('claude-insight').__claudeInsight.getViewState()")
    assert state["map"]["totalTurns"] == 72 and state["map"]["level"] == 0
    page.screenshot(path=str(MEDIA / "session-map.png"))
    wait(page, 3.5)

    select_map_card(page)
    wait(page, 3.2)
    select_map_card(page, 2)
    wait(page, 3.2)
    state = page.evaluate("document.getElementById('claude-insight').__claudeInsight.getViewState()")
    if state["flowMode"] == "map":
        select_map_card(page, 0)
        wait(page, 2.8)
    assert page.evaluate("document.getElementById('claude-insight').__claudeInsight.getViewState().flowMode") == "detail"

    error_node = next(n["id"] for n in data["nodes"] if n["type"] == "tool" and n["is_error"])
    page.evaluate("id => document.getElementById('claude-insight').__claudeInsight.select(id)", error_node)
    wait(page, 2.2)
    assert "Synthetic test failure" in page.locator('[data-role="inspect"]').inner_text()
    page.screenshot(path=str(MEDIA / "event-inspector.png"))
    wait(page, 4.5)

    page.locator('[data-filter="errors"]').click()
    wait(page, 3)
    page.locator('[data-action="session-map"]').click()
    wait(page, 3)
    page.locator('[data-view="overview"]').click()
    wait(page, 4)


def main() -> None:
    from playwright.sync_api import sync_playwright

    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required on PATH")
    MEDIA.mkdir(parents=True, exist_ok=True)
    data = fabricated_graph()
    assert data["turn_count"] == 72 and data["project"] == "demo/northstar-notes"
    html = render_graph(data)
    with TemporaryDirectory(prefix="synthetic-report-") as scratch:
        scratch_path = Path(scratch)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport=VIEWPORT, device_scale_factor=1,
                record_video_dir=str(scratch_path), record_video_size=VIEWPORT,
            )
            page = context.new_page()
            errors = []
            external_requests = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: external_requests.append(request.url)
                    if not request.url.startswith(("file:", "blob:")) else None)
            context.route("**/*", lambda route: route.continue_()
                          if route.request.url.startswith(("file:", "blob:")) else route.abort())
            page.set_content(html, wait_until="load")
            page.evaluate("""() => {
              const badge = document.createElement('div');
              badge.textContent = 'SYNTHETIC DEMO · FICTIONAL DATA';
              badge.setAttribute('aria-label', 'Synthetic demo, fictional data');
              badge.style.cssText = 'position:fixed;top:20px;left:50%;transform:translateX(-50%);'
                + 'z-index:9999;pointer-events:none;background:#eaf4f0;color:#376452;'
                + 'border:1px solid #b9dbca;border-radius:999px;padding:5px 11px;'
                + 'font:600 10px Arial,sans-serif;letter-spacing:.08em';
              document.body.appendChild(badge);
            }""")
            record(page, data)
            video_path = Path(page.video.path())
            context.close()
            browser.close()
        assert not errors, errors
        assert not external_requests, external_requests
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path),
            "-map", "0:v:0", "-an", "-map_metadata", "-1", "-map_chapters", "-1",
            "-vf", "fps=20", "-c:v", "libx264", "-preset", "medium", "-crf", "24",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(MEDIA / "demo.mp4"),
        ], check=True)
    asset_names = ("overview.png", "session-map.png", "event-inspector.png", "demo.mp4")
    manifest = {
        "provenance": "synthetic",
        "hash_algorithm": "sha256",
        "input_canonicalization": "lf",
        "fixture_float_decimals": 9,
        "assets": [{
            "path": "docs/media/" + name,
            "sha256": hashlib.sha256((MEDIA / name).read_bytes()).hexdigest(),
        } for name in asset_names],
        "inputs": [{
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        } for path in (
            Path(__file__).resolve(),
            ROOT / "src" / "claude_insight" / "graph.py",
            ROOT / "src" / "claude_insight" / "graph_view.html",
        )] + [{
            "path": "synthetic-graph-json",
            "sha256": hashlib.sha256(canonical_graph_bytes(data)).hexdigest(),
        }],
    }
    (MEDIA / "manifest.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    print("Generated synthetic public media: " + ", ".join(asset_names))


if __name__ == "__main__":
    main()
