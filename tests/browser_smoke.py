"""Offline browser acceptance for the exported session graph.

Run: python tests/browser_smoke.py
Requires Playwright and its Chromium browser in the local test environment.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "browser"


def sample_graph(count: int = 24) -> dict:
    nodes = []
    edges = []
    for turn in range(count):
        suffix = "<img src=x onerror=alert(1)>" if turn == 1 else f"turn {turn + 1}"
        base = turn * 3
        stamp = (datetime(2026, 9, 19, 12, tzinfo=timezone.utc) + timedelta(minutes=turn)).isoformat()
        nodes.extend(
            [
                {"id": f"u{turn}", "type": "user", "label": f"Prompt {turn + 1}",
                 "text": f"Please review {suffix}", "turn": turn,
                 "timestamp": stamp},
                {"id": f"a{turn}", "type": "assistant", "label": "Assistant analysis",
                 "text": "Review complete", "turn": turn, "model": "claude-test",
                 "usage": {"input_tokens": 50, "output_tokens": 20}},
                {"id": f"t{turn}", "type": "tool", "label": "Read source",
                 "tool_name": "Read", "text": "Read 12 lines", "turn": turn,
                 "is_error": turn == 2},
            ]
        )
        edges.extend(
            [
                {"source": f"u{turn}", "target": f"a{turn}", "type": "causal"},
                {"source": f"a{turn}", "target": f"t{turn}", "type": "causal"},
            ]
        )
        if turn:
            edges.append({"source": f"t{turn - 1}", "target": f"u{turn}", "type": "sequence"})
    return {
        "schema_version": 1,
        "title": "Browser acceptance session",
        "source": "fixture.jsonl",
        "turn_count": count,
        "tool_count": count,
        "error_count": 1,
        "nodes": nodes,
        "edges": edges,
        "cost": {
            "estimated_usd": None,
            "priced_subtotal_usd": 0.01,
            "usage_records": count,
            "priced_records": 1,
            "tokens": {"input_tokens": 1200, "output_tokens": 480},
            "warnings": {"Unknown model rate: claude-test": count - 1},
        },
        "pricing_complete": False,
        "unpriced_models": ["claude-test"],
        "insights": {
            "tool_counts": {"Read": count},
            "model_counts": {"claude-test": count},
            "errors": 1,
            "warnings": [],
        },
    }


def main() -> None:
    from claude_insight.graph import render_graph

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    graph = sample_graph()
    with TemporaryDirectory() as temp:
        page_path = Path(temp) / "graph.html"
        import_path = Path(temp) / "import.json"
        invalid_path = Path(temp) / "invalid.json"
        page_path.write_text(render_graph(graph), encoding="utf-8")
        imported = sample_graph(4)
        imported["title"] = "Imported graph"
        import_path.write_text(json.dumps(imported), encoding="utf-8")
        invalid_path.write_text("null", encoding="utf-8")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            errors = []
            external_requests = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: external_requests.append(request.url)
                    if not request.url.startswith(("file:", "blob:")) else None)
            page.goto(page_path.as_uri())
            expect(page.locator('[data-metric="turns"]')).to_have_text("24")
            assert page.locator('[data-role="chart"] canvas').count() > 0
            assert page.evaluate("typeof cytoscape === 'function' && typeof echarts === 'object'")
            assert "Partial" in page.locator('[data-role="cost-note"]').inner_text()
            assert page.locator("img").count() == 0
            page.screenshot(path=str(ARTIFACTS / "desktop.png"), full_page=True)

            # A chart bar opens the corresponding turn in the flow workspace.
            coords = page.evaluate("""() => {
              const el=document.querySelector('[data-role="chart"]');
              const chart=echarts.getInstanceByDom(el);
              const point=chart.convertToPixel({xAxisIndex:0,yAxisIndex:0},[1,25]);
              const box=el.getBoundingClientRect();return {x:box.x+point[0],y:box.y+point[1]};
            }""")
            page.mouse.click(coords["x"], coords["y"])
            expect(page.locator('[data-page="flow"]')).to_have_class(re.compile("active"))
            expect(page.locator('[data-role="timecode"]')).to_have_text("2 / 24")
            assert page.locator('[data-role="graph"] canvas').count() > 0
            page.locator('[data-role="search"]').fill("<img src=x")
            expect(page.locator('[data-role="events"] .event')).to_have_count(1)
            page.locator('[data-role="search"]').fill("")
            page.locator('[data-filter="errors"]').click()
            expect(page.locator('[data-role="events"] .event')).to_have_count(1)
            page.locator('[data-filter="tools"]').click()
            expect(page.locator('[data-role="events"] .event')).to_have_count(24)
            page.locator('[data-filter="all"]').click()
            page.locator('[data-role="events"] .event').first.click()
            expect(page.locator('[data-role="timecode"]')).to_have_text("1 / 24")
            page.evaluate("""() => {window._selected=0;document.getElementById('claude-insight')
              .addEventListener('claude-insight:select',()=>window._selected++)}""")
            page.evaluate("document.getElementById('claude-insight').__claudeInsight.select('t0')")
            assert page.evaluate("window._selected") == 1
            assert "Read source" in page.locator('[data-role="inspect"]').inner_text()
            before_zoom = page.evaluate("document.getElementById('claude-insight').__claudeInsight.getViewState().zoom")
            page.locator('[data-action="zoom-in"]').click()
            after_zoom = page.evaluate("document.getElementById('claude-insight').__claudeInsight.getViewState().zoom")
            assert after_zoom > before_zoom, (before_zoom, after_zoom)
            page.locator('[data-action="fit"]').click()

            page.locator('[data-role="speed"]').select_option("350")
            page.locator('[data-role="scrub"]').fill("0")
            before = page.locator('[data-role="timecode"]').inner_text()
            page.locator('[data-action="play"]').click()
            page.wait_for_timeout(440)
            after = page.locator('[data-role="timecode"]').inner_text()
            assert before != after, (before, after)
            page.locator('[data-action="play"]').click()
            with page.expect_download() as download:
                page.locator('[data-action="export"]').click()
            assert download.value.suggested_filename == "claude-insight-graph.json"
            page.on("dialog", lambda dialog: dialog.dismiss())
            page.locator('[data-role="file"]').set_input_files(str(invalid_path))
            assert page.evaluate("document.getElementById('claude-insight').__claudeInsight.getData().title") == graph["title"]
            page.locator('[data-role="file"]').set_input_files(str(import_path))
            expect(page.locator('[data-metric="turns"]')).to_have_text("4")
            assert page.evaluate("document.getElementById('claude-insight').__claudeInsight.getData().title") == "Imported graph"
            static_labels = page.locator('.side, .top, .transport, .footer').all_inner_texts()
            assert not any(marker in label for label in static_labels for marker in ("Â", "â†", "â–", "â—")), static_labels
            # Explicit string turn IDs and malformed nested fields normalize safely.
            page.evaluate("""() => {
              const view=document.getElementById('claude-insight').__claudeInsight;
              const old=view.getData();
              for(const bad of [null, [], 'invalid']){
                try{view.setData(bad);throw Error('bad graph accepted')}catch(e){if(e.message==='bad graph accepted')throw e}
                if(view.getData()!==old)throw Error('invalid setData replaced current session');
              }
              view.setData({title:'String IDs',nodes:[{id:'user-a',type:'user',turn:'alpha',text:'Alpha'},
                {id:'tool-a',type:'tool',turn:'alpha',label:'Read'},
                {id:'user-b',type:'user',turn:'beta',text:'Beta'}],edges:[null,{},
                {source:'user-a',target:'tool-a'}, {source:'tool-a',target:'user-a'}],
                analytics:{turns:[null,{},'x'],tools:[null,{},'x'],findings:[null,{},'x']}});
              if(view.select('user-a')?.id!=='user-a')throw Error('cyclic turn selection failed');
              if(view.select('user-b')?.id!=='user-b')throw Error('string turn selection failed');
            }""")
            expect(page.locator('[data-role="timecode"]')).to_have_text("2 / 2")
            page.evaluate("""() => document.getElementById('claude-insight').__claudeInsight.setData({
              title:'Malformed counters',turn_count:'many',tool_count:-9,error_count:{bad:1},
              nodes:[{id:'n',type:'assistant',turn:'one',label:'Malformed usage',
                usage:{input_tokens:'oops',output_tokens:-5,cache_read_input_tokens:{bad:1}}}],
              edges:[],analytics:{totals:{tokens:{input_tokens:'oops',output_tokens:-5},
                estimated_usd:null,priced_subtotal_usd:'unknown',pricing_complete:false},
                turns:[{id:'one',tokens:{input_tokens:'oops',output_tokens:-5}}],tools:[{}],findings:[{}]}
            })""")
            visible = page.locator('.ci').inner_text()
            assert "NaN" not in visible and "Infinity" not in visible, visible[:900]
            page.locator('[data-view="overview"]').click()
            chart_values = page.evaluate("""() => echarts.getInstanceByDom(
              document.querySelector('[data-role="chart"]')).getOption().series.flatMap(s=>s.data)""")
            assert all(isinstance(value, (int, float)) and value >= 0 and value != float("inf")
                       for value in chart_values), chart_values
            page.evaluate("""() => document.getElementById('claude-insight').__claudeInsight.setData({
              nodes:[{id:'valid',type:'user',turn:'x',text:'Valid prompt'}],edges:[],
              turns:[{id:'x',prompt:'Valid prompt',assistant:[null,7,{},'a'],tools:[null,'missing']}],
              tools:{bad:null,other:7},analytics:{turns:[null],tools:[null],findings:[null]}
            })""")
            page.locator('[data-view="flow"]').click()
            state = page.evaluate("document.getElementById('claude-insight').__claudeInsight.getViewState()")
            assert isinstance(state["scale"], (int, float)) and isinstance(state["offset"], dict), state
            page.evaluate("""() => {
              const view=document.getElementById('claude-insight').__claudeInsight;
              const nodes=[{id:'root',type:'user',turn:'one',label:'Root'}];
              for(let i=0;i<31;i++)nodes.push({id:'leaf'+i,type:'tool',turn:'one',label:'Leaf '+i});
              view.setData({title:'Wide graph',nodes,edges:nodes.slice(1).map(n=>({source:'root',target:n.id}))});
            }""")
            page.locator('[data-view="flow"]').click()
            page.evaluate("document.getElementById('claude-insight').__claudeInsight.select('root')")
            expect(page.locator('[data-role="graph-pager"]')).to_be_visible()
            page.locator('[aria-label="Next event group"]').click()
            assert page.locator('[data-role="graph"] canvas').count() > 0
            page.evaluate("document.getElementById('claude-insight').__claudeInsight.setData({nodes:[],edges:[]})")
            empty = page.evaluate("document.getElementById('claude-insight').__claudeInsight.getViewState()")
            assert empty["map"]["totalTurns"] == 0 and not empty["map"]["nodes"], empty
            page.evaluate("data => document.getElementById('claude-insight').__claudeInsight.setData(data)", imported)
            page.locator('[data-view="flow"]').click()
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(ARTIFACTS / "mobile.png"), full_page=True)
            assert page.locator('[data-role="graph"] canvas').count() > 0
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
            assert not errors, errors
            assert not external_requests, external_requests
            if "--html" in sys.argv:
                export_path = Path(sys.argv[sys.argv.index("--html") + 1]).resolve()
                export = browser.new_page(viewport={"width": 1440, "height": 900})
                export_errors = []
                export_requests = []
                export.on("pageerror", lambda error: export_errors.append(str(error)))
                export.on("request", lambda request: export_requests.append(request.url)
                        if not request.url.startswith(("file:", "blob:")) else None)
                export.goto(export_path.as_uri())
                expect(export.locator('[data-metric="turns"]')).not_to_have_text("0")
                assert export.locator('[data-role="chart"] canvas').count() > 0
                export.screenshot(path=str(ARTIFACTS / "export-session.png"), full_page=True)
                assert not export_errors, export_errors
                assert not export_requests, export_requests
                export.close()
            browser.close()
    print("Browser smoke passed; screenshots:", ARTIFACTS / "desktop.png", ARTIFACTS / "mobile.png")


if __name__ == "__main__":
    main()
