#!/usr/bin/env python3
"""Render the designer in a real browser and capture the README screenshots.

Run from the repository root:

    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python3 scripts/screenshots.py

It seeds a workspace with the worked finance example, lays the organisation
out so the canvas and its placement regions have something real to enclose,
serves the app, drives Chromium over it and writes `docs/images/*.png`.

This exists because the screenshots are evidence, not decoration: for most of
this project's life the UI had never been rendered at all, and the first run of
this script found four defects that the whole test suite did not. Keeping it
runnable is what stops that gap reopening.

It fails loudly on a page error or a console error, so a regression that only
shows up in a browser breaks the capture rather than producing a screenshot of
a broken page.
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
WORK = pathlib.Path(tempfile.mkdtemp(prefix="orgagents-shots-"))
os.chdir(WORK)
from orgagents.api import create_app
app = create_app(str(WORK / "designer.db"))

# Seed: a workspace, the worked finance organisation, and a tenant to publish to.
from fastapi.testclient import TestClient
c = TestClient(app)
A = {"X-User": "ana", "X-User-Name": "Ana Silva"}
spec = yaml.safe_load((ROOT / "examples" / "northwind.finance.system.yaml").read_text())
ws = c.post("/api/designer/workspaces", json={"name": "Northwind"}, headers=A).json()
sys_ = c.post("/api/designer/systems",
              json={"workspace_id": ws["id"], "name": "Northwind Finance",
                    "spec": spec}, headers=A).json()
# Lay the organisation out so the canvas and its placement regions have
# something real to draw. Saved through the API, so it is the stored layout.
opened = c.get(f"/api/designer/systems/{sys_['id']}", headers=A).json()["record"]
nodes, col_of = {}, {}
def place(team, depth, slot):
    nodes[team.id if hasattr(team, "id") else team["id"]] = None
def walk(team, depth, slot):
    tid = team["id"]
    nodes[tid] = {"id": tid, "kind": "team", "x": 40 + depth * 300,
                  "y": 70 + slot[0] * 105, "width": 240, "height": 80,
                  "collapsed": False, "note": ""}
    slot[0] += 1
    for m in team.get("members", []):
        nodes[m["id"]] = {"id": m["id"], "kind": "agent",
                          "x": 40 + (depth + 1) * 300, "y": 70 + slot[0] * 105,
                          "width": 240, "height": 80, "collapsed": False,
                          "note": ""}
        slot[0] += 1
    for t in team.get("teams", []):
        walk(t, depth + 1, slot)
walk(spec["organization"], 0, [0])
c.put(f"/api/designer/systems/{sys_['id']}",
      json={"layout": {"nodes": nodes, "edges": []},
            "version": opened["version"]}, headers=A)
print("LAID OUT", len(nodes), flush=True)

app.state.fabric["tenants"].register(id="northwind", name="Northwind",
                                     namespace_prefix="northwind")
print("SEEDED", ws["id"], sys_["id"], flush=True)



# ---------------------------------------------------------------- capture

import asyncio
import json

from playwright.async_api import async_playwright

OUT = ROOT / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get("ORGAGENTS_SHOT_PORT", "8811"))
BASE = f"http://127.0.0.1:{PORT}/ui/"

#: The environment ships a Chromium that the installed Playwright may not
#: match by build number, so it is launched by path rather than downloaded.
CHROME = os.environ.get(
    "ORGAGENTS_CHROME",
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
)

#: Google Fonts is unreachable behind this environment's egress proxy, so the
#: captures use the stylesheet's declared fallbacks. That is a property of
#: where this runs, not of the page, and it is the one console error allowed.
ALLOWED_CONSOLE = ("fonts.googleapis", "ERR_CERT_AUTHORITY_INVALID")

problems: list[str] = []

async def shot(page, name, note=""):
    await page.wait_for_timeout(700)
    await page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"  captured {name}.png {note}")

async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(executable_path=CHROME)
        page = await b.new_page(viewport={"width": 1680, "height": 1050},
                                device_scale_factor=2)
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("console", lambda m: problems.append(f"console {m.type}: {m.text}")
                if m.type == "error" else None)

        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # 1. Org chart — the tree as the spec defines it.
        await shot(page, "designer-org-chart")

        # 2. Canvas — lay the org out so the placement regions have something
        #    to enclose, then capture.
        await page.click('#tabs button[data-view="canvas"]')
        await page.wait_for_timeout(900)
        print("  canvas drawn:", await page.locator("#canvas-nodes > *").count(),
              "| regions:", await page.locator("#canvas-regions > *").count())
        await shot(page, "designer-canvas")

        # 3. Authority — mandates, separations, placements, people.
        await page.click('#tabs button[data-view="authority"]')
        await page.wait_for_timeout(1400)
        print("  authority agents:", await page.locator("#authority-agents > *").count(),
              "| placements:", await page.locator("#authority-placements > *").count(),
              "| people:", await page.locator("#authority-people > *").count())
        await shot(page, "designer-authority")

        # 4. The publish path, showing a real preflight verdict.
        await page.click("#btn-publish")
        await page.wait_for_timeout(2500)
        print("  publish verdict:",
              (await page.text_content("#publish-verdict") or "").strip(),
              "|", (await page.text_content("#publish-summary") or "")[:70])
        await shot(page, "designer-publish")

        await b.close()




def serve() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    threading.Thread(target=serve, daemon=True).start()
    for _ in range(60):
        try:
            import urllib.request

            urllib.request.urlopen(BASE, timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit("the designer did not start")
    asyncio.run(main())
    real = [p for p in problems
            if not any(ok in p for ok in ALLOWED_CONSOLE)]
    if real:
        raise SystemExit("the page reported errors:\n  " + "\n  ".join(real))
    print("\nscreenshots written to", OUT)
