"""Drive the designer in a real browser: drag-and-drop, forms, save.

Run from the repository root:

    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python3 scripts/interaction_check.py

`screenshots.py` proves the views render. This proves they *work*: a component
dragged from the palette onto the canvas, an inspector form edited, a node
moved, the authority controls present on an agent that holds a capability, and
the whole thing saved and read back.

It exists because rendering and interacting fail differently. Rendering the UI
for the first time found four defects; the first interaction run found that a
palette kind added after the layout store's enum was written could be dropped,
edited, and then lost on save with a 500 — because nothing had ever dropped
one.
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



# ------------------------------------------------------------- interaction

import asyncio
import json

from playwright.async_api import async_playwright

PORT = int(os.environ.get("ORGAGENTS_SHOT_PORT", "8844"))
BASE = f"http://127.0.0.1:{PORT}/ui/"
CHROME = os.environ.get(
    "ORGAGENTS_CHROME",
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
)
ALLOWED_CONSOLE = ("fonts.googleapis", "ERR_CERT_AUTHORITY_INVALID")

problems: list[str] = []
results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME)
        page = await browser.new_page(viewport={"width": 1680, "height": 1050})
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on(
            "console",
            lambda m: problems.append(f"console {m.type}: {m.text}")
            if m.type == "error" else None,
        )
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(1500)
        await page.click('#tabs button[data-view="canvas"]')
        await page.wait_for_timeout(900)

        # -- 1. Drag from the palette onto the canvas ----------------------
        before = await page.locator("#canvas-nodes > *").count()
        await page.locator(".drag-item", has_text="Guardrail").first.drag_to(
            page.locator("#canvas"), target_position={"x": 700, "y": 520}
        )
        await page.wait_for_timeout(700)
        after = await page.locator("#canvas-nodes > *").count()
        check("a palette drag creates a node", after == before + 1,
              f"{before} -> {after}")

        made = await page.evaluate("""() => {
          const d = window.designer;
          const nodes = Object.values(d.state.record.layout.nodes);
          return { node: nodes[nodes.length - 1],
                   guardrails: (d.spec().guardrails || []).length };
        }""")
        check("the dropped component reaches the spec",
              made["guardrails"] > 0, json.dumps(made["node"]))
        check("the node is snapped to the grid",
              made["node"]["x"] % 10 == 0 and made["node"]["y"] % 10 == 0)

        # -- 2. The inspector opens and edits the spec ---------------------
        await page.click(f'#canvas-nodes [data-id="{made["node"]["id"]}"]')
        await page.wait_for_timeout(600)
        fields = page.locator("#inspector input, #inspector textarea, "
                              "#inspector select")
        check("selecting a node opens its form", await fields.count() > 0,
              f"{await fields.count()} control(s)")

        # The controls carry no `name`, so they are addressed by their label
        # the way a person would.
        box = page.locator("#inspector label", has_text="description").first
        area = box.locator("textarea, input").first
        await area.fill("Edited in a browser")
        await area.dispatch_event("input")
        await page.wait_for_timeout(500)
        wrote = await page.evaluate("""(id) => {
          const g = (window.designer.spec().guardrails || [])
            .find((x) => x.id === id);
          return g ? g.description : null;
        }""", made["node"]["id"])
        check("editing the form writes to the spec",
              wrote == "Edited in a browser", repr(wrote))

        # -- 3. Dragging a node moves it and leaves the spec alone ---------
        node = page.locator("#canvas-nodes .node").first
        nid = await node.get_attribute("data-id")
        rect = await node.bounding_box()
        spec_before = await page.evaluate(
            "() => JSON.stringify(window.designer.spec())")
        await page.mouse.move(rect["x"] + 60, rect["y"] + 20)
        await page.mouse.down()
        await page.mouse.move(rect["x"] + 260, rect["y"] + 160, steps=12)
        await page.mouse.up()
        await page.wait_for_timeout(500)
        moved = await page.evaluate(
            "(id) => window.designer.state.record.layout.nodes[id]", nid)
        spec_after = await page.evaluate(
            "() => JSON.stringify(window.designer.spec())")
        check("dragging a node moves it", moved["x"] != round(rect["x"]),
              f"{nid} -> ({moved['x']}, {moved['y']})")
        check("layout never enters the spec (ADR-0034)",
              spec_before == spec_after)

        # -- 4. The authority controls, on an agent that has capabilities --
        holder = await page.evaluate("""() => {
          const s = window.designer.spec();
          const caps = new Set((s.capabilities || []).map((c) => c.id));
          const roles = new Map((s.roles || []).map((r) => [r.id, r]));
          let found = null;
          const walk = (t) => {
            for (const a of t.members || []) {
              const held = new Set(a.capabilities || []);
              for (const x of a.roles || []) {
                const r = roles.get(typeof x === "string" ? x : x.role);
                (r?.capabilities || []).forEach((c) => held.add(c));
              }
              if (!found && [...held].some((c) => caps.has(c))) found = a.id;
            }
            (t.teams || []).forEach(walk);
          };
          walk(s.organization);
          return found;
        }""")
        check("the worked example has an agent holding a capability",
              bool(holder), str(holder))
        if holder:
            await page.click(f'#canvas-nodes [data-id="{holder}"]')
            await page.wait_for_timeout(700)
            controls = await page.evaluate("""() => ({
              mandate: document.querySelectorAll("#inspector .mandate-wrap").length,
              autonomy: document.querySelectorAll("#inspector .autonomy-wrap").length,
            })""")
            check("an agent form offers the mandate control",
                  controls["mandate"] > 0, json.dumps(controls))
            check("an agent form offers a posture per activity",
                  controls["autonomy"] > 0, json.dumps(controls))

        # -- 5. It saves ---------------------------------------------------
        await page.click("#btn-save")
        await page.wait_for_timeout(2000)
        status = (await page.text_content("#status") or "").strip()
        check("the design saves", "fail" not in status.lower()
              and "error" not in status.lower(), f"status={status!r}")
        persisted = await page.evaluate("""async () => {
          const d = window.designer;
          const rec = await d.dapi(`/systems/${d.state.systemId}`);
          const nodes = rec.record.layout.nodes;
          const g = (rec.record.spec.guardrails || [])
            .find((x) => (x.description || "").includes("Edited in a browser"));
          return { kinds: [...new Set(Object.values(nodes).map((n) => n.kind))],
                   edited: !!g };
        }""")
        check("the edit survives a round trip to the server",
              persisted["edited"], json.dumps(persisted["kinds"]))

        await page.screenshot(path="/tmp/interaction-final.png")
        await browser.close()


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
    for ok, name, detail in results:
        print(("PASS  " if ok else "FAIL  ") + name
              + (f"   [{detail}]" if detail else ""))
    real = [p for p in problems if not any(a in p for a in ALLOWED_CONSOLE)]
    if real:
        print("\nconsole/page errors:")
        for p in real[:10]:
            print("  " + p)
    if real or not all(ok for ok, _, _ in results):
        raise SystemExit(1)
    print("\nall interaction checks passed")
