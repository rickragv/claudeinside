"""Capture export overview and focused flow checkpoints.

Run: python tests/browser_visual_checkpoint.py PATH_TO_EXPORT.html
"""

import sys
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(path):
    source = Path(path).resolve()
    output = Path(__file__).resolve().parents[1] / "artifacts" / "browser"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(source.as_uri())
        page.locator('[data-metric="turns"]').wait_for()
        page.screenshot(path=str(output / "overview.png"), full_page=True)
        page.locator('[data-view="flow"]').click()
        page.screenshot(path=str(output / "session-map.png"), full_page=True)
        root_map = page.evaluate("document.getElementById('claude-insight').__claudeInsight.getViewState().map")
        assert root_map and root_map["start"] == 0 and root_map["end"] == root_map["totalTurns"]
        total_nodes = page.evaluate("document.getElementById('claude-insight').__claudeInsight.getData().nodes.length")
        assert root_map["attributedEvents"] + root_map["outsideTurnEvents"] == total_nodes
        assert len(root_map["nodes"]) <= 8
        cursor = 0
        for node in root_map["nodes"]:
            assert node["start"] == cursor and node["fontPx"] >= 12
            box = node["bounds"]
            assert box["x1"] >= 0 and box["y1"] >= 0
            assert box["x2"] <= root_map["width"] and box["y2"] <= root_map["height"]
            cursor = node["end"]
        assert cursor == root_map["totalTurns"]
        if root_map["outsideTurnEvents"]:
            actual_first_turn = page.evaluate("""() => {
              const data=document.getElementById('claude-insight').__claudeInsight.getData();
              const first=String(data.turns[0].id);
              return data.nodes.filter(n=>n.turn!=null&&String(n.turn)===first).length;
            }""")
            for _ in range(10):
                current = page.evaluate("document.getElementById('claude-insight').__claudeInsight.getViewState()")
                if current["flowMode"] == "detail":
                    break
                page.locator('[data-role="inspect"] .inspect-section button').first.click()
            first_detail = page.evaluate("document.getElementById('claude-insight').__claudeInsight.getViewState()")
            assert first_detail["flowMode"] == "detail" and first_detail["turn"] == 0
            assert f"{actual_first_turn} events" in page.locator('[data-role="flow-subtitle"]').inner_text()
            page.locator('[data-action="session-map"]').click()
        selection = page.evaluate("""() => {
          const view=document.getElementById('claude-insight').__claudeInsight;
          const data=view.getData();const nodes=Array.isArray(data.nodes)?data.nodes:[];
          const counts=new Map();for(const node of nodes){const id=String(node.turn??'0');counts.set(id,(counts.get(id)||0)+1)}
          const dense=[...counts].sort((a,b)=>b[1]-a[1])[0]?.[0];
          const error=nodes.find(node=>node.is_error)?.id;
          const denseNode=nodes.find(node=>String(node.turn??'0')===dense)?.id;
          return {denseNode,error,denseCount:counts.get(dense)||0};
        }""")
        if selection["denseNode"]:
            page.evaluate("id => document.getElementById('claude-insight').__claudeInsight.select(id)",
                          selection["denseNode"])
            page.locator('[data-page="flow"].active').wait_for()
            page.screenshot(path=str(output / "dense-flow.png"), full_page=True)
            dense_state = page.evaluate("""id => {
              const graph=document.getElementById('claude-insight').__claudeInsight.getViewState().graph;
              const selected=graph.nodes.find(node=>node.id===id);
              return {width:graph.width,height:graph.height,nodeCount:graph.nodes.length,selected};
            }""", selection["denseNode"])
        if selection["error"]:
            page.evaluate("id => document.getElementById('claude-insight').__claudeInsight.select(id)",
                          selection["error"])
            page.screenshot(path=str(output / "error-flow.png"), full_page=True)
            error_state = page.evaluate("""id => {
              const graph=document.getElementById('claude-insight').__claudeInsight.getViewState().graph;
              const selected=graph.nodes.find(node=>node.id===id);
              return {width:graph.width,height:graph.height,nodeCount:graph.nodes.length,selected};
            }""", selection["error"])
        metrics = {"session_map": root_map,
                   "dense": dense_state if selection["denseNode"] else None,
                   "error": error_state if selection["error"] else None}
        (output / "graph-metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
        for label, graph in metrics.items():
            if not graph or label == "session_map":
                continue
            node = graph["selected"]
            assert node, f"{label}: selected node absent from rendered window"
            assert node["fontPx"] >= 11, f"{label}: rendered font {node['fontPx']}px below 11px"
            box = node["bounds"]
            assert box["x1"] >= 0 and box["y1"] >= 0 and box["x2"] <= graph["width"] and box["y2"] <= graph["height"], (label, graph)
        assert not errors, errors
        browser.close()
    print("Visual checkpoint:", selection)
    print(output / "overview.png")
    print(output / "session-map.png")
    print(output / "dense-flow.png")
    print(output / "error-flow.png")
    print(output / "graph-metrics.json")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python tests/browser_visual_checkpoint.py SESSION.html")
    main(sys.argv[1])
