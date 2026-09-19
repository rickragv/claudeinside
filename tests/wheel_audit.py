"""Build and inspect an installed wheel in a temporary environment outside the repo.

Run: python tests/wheel_audit.py
Requires Playwright and Chromium in the invoking Python environment.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import venv
from zipfile import ZipFile

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
VENDOR = {
    "cytoscape.min.js": "5f3b5b529546d5af1fc5628590af033b74511a5b6f789f5f4682845863228b91",
    "echarts.min.js": "b66b25aeb4df84e33199dc21694014d336d222cbd9deb0e5a7c14bd6aa0d0fd0",
}
NOTICES = ("cytoscape.LICENSE", "echarts.LICENSE", "echarts.NOTICE",
           "echarts.LICENSE-d3", "README.md")


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def main():
    with TemporaryDirectory(prefix="claude-insight-wheel-") as scratch:
        work = Path(scratch).resolve()
        assert work != ROOT and ROOT not in work.parents, work
        wheel_dir = work / "wheel"
        wheel_dir.mkdir()
        run(sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--wheel-dir", str(wheel_dir), cwd=ROOT)
        wheels = list(wheel_dir.glob("*.whl"))
        assert len(wheels) == 1, wheels
        with ZipFile(wheels[0]) as package:
            names = set(package.namelist())
            for name, digest in VENDOR.items():
                member = "claude_insight/vendor/" + name
                assert member in names, member
                assert hashlib.sha256(package.read(member)).hexdigest() == digest, member
            for name in NOTICES:
                assert "claude_insight/vendor/" + name in names, name
            assert "claude_insight/graph_view.html" in names
            assert "claude_insight/demo_session.jsonl" in names
            metadata = next(package.read(name).decode("utf-8") for name in names
                            if name.endswith(".dist-info/METADATA"))
            assert "Name: claude-session-insight" in metadata

        environment = work / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run(str(python), "-m", "pip", "install", "--no-deps", str(wheels[0]))
        html = work / "installed-demo.html"
        script = """from pathlib import Path
from claude_insight import graph_data, render_graph, session_files, viewer_assets
from claude_insight import graph
source=Path(graph.__file__).with_name('demo_session.jsonl')
path,opener=next(session_files(str(source)))
data=graph_data(path,opener)
assert data['turn_count']>0 and data['analytics']['turns']
assets=viewer_assets()
assert 'cytoscape' in assets['js'] and 'echarts' in assets['js']
Path(OUTPUT).write_text(render_graph(data),encoding='utf-8')
""".replace("OUTPUT", repr(str(html)))
        run(str(python), "-I", "-c", script, cwd=work)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 850})
            errors, remote = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: remote.append(request.url)
                    if not request.url.startswith(("file:", "blob:")) else None)
            page.goto(html.as_uri())
            page.locator('[data-metric="turns"]').wait_for()
            assert page.evaluate("typeof cytoscape==='function' && typeof echarts==='object'")
            assert page.locator('[data-role="chart"] canvas').count() > 0
            assert not errors, errors
            assert not remote, remote
            browser.close()
        print("Wheel audit passed:", wheels[0].name,
              "vendor hashes/notices, installed API, offline browser viewer")


if __name__ == "__main__":
    main()
