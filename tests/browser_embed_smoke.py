"""Run manually after installing playwright: python tests/browser_embed_smoke.py."""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright
from claude_insight import graph_data, session_files, write_viewer_assets


ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "artifacts" / "viewer"
write_viewer_assets(str(out))
path, opener = next(session_files(str(ROOT / "src" / "claude_insight" / "demo_session.jsonl")))
data = graph_data(path, opener)
safe = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
page = out / "embed-smoke.html"
page.write_text("""<!doctype html><link rel="stylesheet" href="viewer.css">
<div id="host"></div><script src="viewer.js"></script>
<script>window.view = ClaudeInsight.mount(document.getElementById('host'), DATA);</script>""".replace("DATA", safe), encoding="utf-8")
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    tab = browser.new_page(viewport={"width": 1280, "height": 850})
    errors = []
    requests = []
    tab.on("pageerror", lambda error: errors.append(str(error)))
    tab.on("request", lambda request: requests.append(request.url)
           if not request.url.startswith(("file:", "blob:")) else None)
    tab.goto(page.as_uri())
    tab.locator('[data-metric="turns"]').wait_for()
    assert tab.evaluate("window.view.getData().turn_count") == 10
    assert tab.locator('[data-role="chart"] canvas').count() > 0
    tab.evaluate("window.view.select('demo-u2')")
    assert tab.locator(".ci .inspect-title").count() == 1
    assert tab.locator('[data-page="flow"].active').count() == 1
    tab.evaluate("""() => {
      const host=document.createElement('section');host.id='other';document.body.append(host);
      window.other=ClaudeInsight.mount(host,{title:'Other graph',nodes:[{id:'x',type:'user',turn:'case',label:'Other'}],edges:[]});
      if(!host.querySelector('.ci'))throw Error('second mount missing');
      window.other.setData({title:'Changed',nodes:[{id:'y',type:'tool',turn:'case',label:'Changed'}],edges:[]});
      if(window.other.getData().title!=='Changed')throw Error('second setData failed');
      if(window.view.getData().turn_count!==10)throw Error('first mount changed');
      window.other.destroy();if(host.children.length)throw Error('destroy did not clear host');host.remove();
    }""")
    assert not errors, errors
    assert not requests, requests
    browser.close()
print("External viewer.css/viewer.js embed passed")
