"""Browser acceptance for bounded whole-session range maps.

Run: python tests/browser_session_map_smoke.py
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import sync_playwright

from browser_smoke import sample_graph
from claude_insight.graph import render_graph


def state(page):
    return page.evaluate("document.getElementById('claude-insight').__claudeInsight.getViewState()")


def assert_partition(mapping, count, limit):
    assert mapping and mapping["totalTurns"] == count, mapping
    assert 0 <= mapping["start"] <= mapping["end"] <= count, mapping
    children = mapping["nodes"]
    assert len(children) <= limit, (len(children), limit)
    cursor = mapping["start"]
    for child in children:
        assert child["start"] == cursor and child["end"] > cursor, (mapping, child)
        assert child["leaf"] == (child["end"] - child["start"] == 1), child
        assert child["fontPx"] >= 12, child
        box = child["bounds"]
        assert box["x1"] >= 0 and box["y1"] >= 0, (mapping, child)
        assert box["x2"] <= mapping["width"] and box["y2"] <= mapping["height"], (mapping, child)
        cursor = child["end"]
    assert cursor == mapping["end"], (mapping, cursor)
    for index, left in enumerate(children):
        a = left["bounds"]
        for right in children[index + 1:]:
            b = right["bounds"]
            width = min(a["x2"], b["x2"]) - max(a["x1"], b["x1"])
            height = min(a["y2"], b["y2"]) - max(a["y1"], b["y1"])
            assert width <= 2 or height <= 2, (mapping, left, right)


def drill_boundary(page, count, last=False):
    page.locator('[data-action="session-map"]').click()
    depth = 0
    while True:
        current = state(page)
        assert current["flowMode"] == "map"
        mapping = current["map"]
        assert_partition(mapping, count, 8)
        index = len(mapping["nodes"]) - 1 if last else 0
        target = mapping["nodes"][index]
        page.locator('[data-role="inspect"] .inspect-section button').nth(index).click()
        depth += 1
        if target["leaf"]:
            detail = state(page)
            assert detail["flowMode"] == "detail" and detail["turn"] == target["start"], detail
            assert page.locator('[data-role="graph"] canvas').count() > 0
            page.locator('.flow-breadcrumb button').first.click()
            back = state(page)
            assert back["flowMode"] == "map" and back["map"]["start"] == 0
            assert back["map"]["end"] == count
            return depth
        assert state(page)["map"]["level"] == depth


def main():
    with TemporaryDirectory(prefix="session-map-smoke-") as scratch:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for count in (73, 376):
                graph = sample_graph(count)
                path = Path(scratch) / f"session-{count}.html"
                path.write_text(render_graph(graph), encoding="utf-8")
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(path.as_uri())
                page.locator('[data-metric="turns"]').wait_for()
                page.locator('[data-view="flow"]').click()
                mapping = state(page)["map"]
                assert_partition(mapping, count, 8)
                assert mapping["start"] == 0 and mapping["end"] == count
                assert mapping["attributedEvents"] == len(graph["nodes"])
                assert mapping["outsideTurnEvents"] == 0
                first_depth = drill_boundary(page, count)
                last_depth = drill_boundary(page, count, last=True)
                assert first_depth > 1 and last_depth > 1
                page.evaluate("id => document.getElementById('claude-insight').__claudeInsight.select(id)",
                              f"a{count - 1}")
                assert state(page)["flowMode"] == "detail" and state(page)["turn"] == count - 1
                page.locator('[data-action="session-map"]').click()
                assert state(page)["map"]["start"] == 0 and state(page)["map"]["end"] == count
                if count == 73:
                    page.locator('[data-view="overview"]').click()
                    coords = page.evaluate("""() => {
                      const el=document.querySelector('[data-role="chart"]'),chart=echarts.getInstanceByDom(el);
                      const p=chart.convertToPixel({xAxisIndex:0,yAxisIndex:0},[5,25]);
                      const r=el.getBoundingClientRect();return {x:r.x+p[0],y:r.y+p[1]};
                    }""")
                    page.mouse.click(coords["x"], coords["y"])
                    assert state(page)["flowMode"] == "detail", state(page)
                    page.locator('[data-action="session-map"]').click()
                    assert state(page)["map"]["end"] == count
                page.set_viewport_size({"width": 900, "height": 850})
                page.locator('[data-view="flow"]').click()
                assert_partition(state(page)["map"], count, 4)
                page.set_viewport_size({"width": 1440, "height": 900})
                page.evaluate("document.getElementById('claude-insight').style.width='640px'")
                page.wait_for_timeout(150)
                page.locator('[data-view="flow"]').click()
                assert_partition(state(page)["map"], count, 4)
                page.evaluate("document.getElementById('claude-insight').style.width=''")
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator('[data-view="flow"]').click()
                mobile = state(page)["map"]
                assert_partition(mobile, count, 4)
                centers = [(n["bounds"]["x1"] + n["bounds"]["x2"]) / 2 for n in mobile["nodes"]]
                assert max(centers, default=0) - min(centers, default=0) < 3, centers
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 2")
                assert not errors, errors
                page.close()

            # Arbitrary external IDs must use turn ordering for direct selection.
            graph = sample_graph(3)
            names = ["alpha", "zeta-100", "last/turn"]
            for node in graph["nodes"]:
                node["turn"] = names[node["turn"]]
            graph["turns"] = [{"id": name, "prompt": f"Prompt {index + 1}", "tools": [],
                              "assistant": [], "errors": 0} for index, name in enumerate(names)]
            path = Path(scratch) / "string-ids.html"
            path.write_text(render_graph(graph), encoding="utf-8")
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(path.as_uri())
            page.locator('[data-view="flow"]').click()
            assert_partition(state(page)["map"], 3, 8)
            page.evaluate("document.getElementById('claude-insight').__claudeInsight.select('a2')")
            assert state(page)["flowMode"] == "detail" and state(page)["turn"] == 2
            page.locator('[data-action="session-map"]').click()
            assert_partition(state(page)["map"], 3, 8)
            page.close()
            browser.close()
    print("Session map passed: 73 and 376 turn partitions, drills, direct select, mobile, string IDs")


if __name__ == "__main__":
    main()
