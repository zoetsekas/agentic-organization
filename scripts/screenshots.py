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
spec = yaml.safe_load((ROOT / "examples" / "northwind" / "northwind.finance.system.yaml").read_text())

# The capabilities the last few ADRs added are seeded here rather than in the
# shipped design, because the screenshots exist to show what the *designer*
# offers: a process with a loop in it, an agent with a declared stand-in, and
# an agent whose scale somebody chose. Without them the captures would show
# controls with nothing in them, which is a screenshot of an empty form.
spec["organization"].setdefault("workflows", []).append({
    "id": "payment_review",
    "name": "payment-review",
    "description": ("Review a payment run, correcting what fails the match "
                    "and re-checking until nothing does."),
    "graph": {
        "entry": "match",
        "nodes": [
            {"id": "match", "kind": "tool", "tool": "ledger_query"},
            # A branch carries its own ways out as cases; it is the one step
            # that may have several, because it is the one that chooses. Left
            # without them the two steps below become unreachable, and the
            # gate refuses the design — which is how this seed was wrong the
            # first time it was written.
            {"id": "any_failures", "kind": "branch",
             "cases": [{"when": "failures > 0", "to": "correct"}],
             "default": "release"},
            {"id": "correct", "kind": "agent", "agent": "controller"},
            {"id": "release", "kind": "human"},
        ],
        # `correct → match` is the loop: having corrected something, match
        # again. It goes back to the *measurement*, not to the branch, because
        # a loop that re-tests a condition nothing has re-measured cannot
        # converge — the easiest cyclic-workflow mistake there is.
        "edges": [
            {"from": "match", "to": "any_failures"},
            {"from": "correct", "to": "match"},
            {"from": "release", "to": "END"},
        ],
    },
    "interrupt_before": ["release"],
})


def _find_agent(node, agent_id):
    for member in node.get("members", []) or []:
        if member.get("id") == agent_id:
            return member
    for team in node.get("teams", []) or []:
        found = _find_agent(team, agent_id)
        if found:
            return found
    return None


_controller = _find_agent(spec["organization"], "controller")
if _controller is not None:
    # A lateral stand-in, and a scale somebody chose rather than inherited.
    _controller["successor"] = "cfo"
    _controller["scaling"] = {"min_instances": 1, "max_instances": 6,
                              "concurrent_sessions_per_instance": 2}

ws = c.post("/api/designer/workspaces", json={"name": "Northwind"}, headers=A).json()
sys_ = c.post("/api/designer/systems",
              json={"workspace_id": ws["id"], "name": "Northwind Finance",
                    "spec": spec}, headers=A).json()
# Lay the organisation out so the canvas and its placement regions have
# something real to draw. Saved through the API, so it is the stored layout.
opened = c.get(f"/api/designer/systems/{sys_['id']}", headers=A).json()["record"]
# The product's own `tree` layout, not a hand-rolled one (ADR-0100). The
# previous version here assigned a column per depth and a slot per sibling,
# which cascaded diagonally: by twelve agents the organisation ran off the
# right edge of every capture. Using the real algorithm means the screenshots
# show what a reader actually gets when they press Arrange.
from orgagents.designer.layout import LayoutNode, arrange

kinds, parents = {}, {}
def walk(team, parent=None):
    kinds[team["id"]] = "team"
    parents[team["id"]] = parent
    for m in team.get("members", []):
        kinds[m["id"]] = "agent"
        parents[m["id"]] = team["id"]
    for t in team.get("teams", []):
        walk(t, team["id"])
walk(spec["organization"])

laid_out = arrange(
    [LayoutNode(id=i, parent=parents[i]) for i in kinds],
    kind="organisation",
)
placed = laid_out.positions
# Real coordinates, not squashed ones: a capture that scaled the layout down
# to fit would not be showing what the algorithm produces.
nodes = {
    i: {"id": i, "kind": kinds[i], "x": 40 + placed[i]["x"],
        "y": 70 + placed[i]["y"], "width": 240, "height": 80,
        "collapsed": False, "note": ""}
    for i in kinds
}
# The canvas has no zoom — `Layout.viewport.zoom` is persisted and read by
# nothing — so a 21-node organisation is navigated by scrolling and by the
# outline, and a capture shows a portion of it. Saying that here rather than
# scaling the coordinates down to make the picture fit, which would be a
# screenshot of something the algorithm does not produce.
# One model, many diagrams: the whole organisation, plus a drill-down of one
# unit, so the captures show what the diagram tabs are for.
treasury = spec["organization"]["teams"][0]["teams"][2]
finance_nodes = {}
row = 0
for item, kind in ([(treasury, "team")]
                   + [(m, "agent") for m in treasury.get("members", [])]):
    finance_nodes[item["id"]] = {
        "id": item["id"], "kind": kind, "x": 60 + (0 if kind == "team" else 300),
        "y": 70 + row * 110, "width": 220, "height": 80,
        "collapsed": False, "note": "",
    }
    row += 1
c.put(f"/api/designer/systems/{sys_['id']}",
      json={"layout": {
                "active": "main",
                "diagrams": {
                    "main": {"id": "main", "name": "Organisation", "root": "",
                             "nodes": nodes},
                    "dia_1": {"id": "dia_1", "name": treasury["name"],
                              "root": treasury["id"], "nodes": finance_nodes},
                }},
            "version": opened["version"]}, headers=A)
print("LAID OUT", len(nodes), flush=True)

app.state.fabric["tenants"].register(id="northwind", name="Northwind",
                                     namespace_prefix="northwind")
print("SEEDED", ws["id"], sys_["id"], flush=True)



# ---------------------------------------------------------------- capture

import asyncio

from playwright.async_api import async_playwright

OUT = ROOT / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get("ORGAGENTS_SHOT_PORT", "8811"))
BASE = f"http://127.0.0.1:{PORT}/ui/"

#: The environment ships a Chromium that the installed Playwright may not
#: match by build number, so it is launched by path rather than downloaded.
def _chromium() -> str | None:
    """Where Chromium is, or `None` to let Playwright decide.

    This environment pins a browser under `/opt/pw-browsers`; a CI runner
    installs its own and Playwright knows where. Hardcoding the first made the
    scripts unrunnable on the second, which is the whole reason they had never
    run in CI. An explicit path wins, then this environment's, then nothing —
    and `None` means "you know best", not "give up".
    """
    import glob
    import os
    import pathlib

    explicit = os.environ.get("ORGAGENTS_CHROME")
    if explicit:
        return explicit
    for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome",
                    "/opt/pw-browsers/chromium"):
        found = sorted(glob.glob(pattern))
        if found and pathlib.Path(found[-1]).exists():
            return found[-1]
    return None


CHROME = _chromium()

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
        b = await pw.chromium.launch(**({"executable_path": CHROME} if CHROME else {}))
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
        # Press the real Arrange button rather than trusting the seeded
        # coordinates: the capture should show what a reader gets, not what
        # this script pre-computed (ADR-0100).
        await page.click('.dia-arrange:has-text("tree")')
        await page.wait_for_timeout(900)
        print("  arranged:", (await page.text_content("#status") or "").strip()[:60])
        await shot(page, "designer-canvas")

        # 3. The issues tab: every finding, attributed to a component.
        await page.click('#side-tabs button[data-side="issues"]')
        await page.wait_for_timeout(600)
        print("  findings:", await page.locator("#validation .findings li").count(),
              "| traceable:", await page.locator("#validation button.v-where").count())
        await shot(page, "designer-issues")
        await page.click('#side-tabs button[data-side="details"]')

        # 3. Authority — mandates, separations, placements, people.
        await page.click('#tabs button[data-view="org"]')
        await page.wait_for_timeout(400)
        await page.click("#btn-org-authority")
        await page.wait_for_timeout(1400)
        print("  authority agents:", await page.locator("#authority-agents > *").count(),
              "| placements:", await page.locator("#authority-placements > *").count(),
              "| people:", await page.locator("#authority-people > *").count())
        await shot(page, "designer-authority")

        # 4. A process with a loop in it — the editor that did not exist,
        #    because until now a workflow could be named and not described.
        await page.click('#tabs button[data-view="canvas"]')
        await page.wait_for_timeout(500)
        await page.click('#side-tabs button[data-side="details"]')
        await page.click('button[data-left="explorer"]')
        await page.wait_for_timeout(400)
        await page.fill("#explorer-filter", "payment")
        await page.wait_for_timeout(400)
        await page.click("#explorer-tree >> text=payment-review")
        await page.wait_for_timeout(900)
        steps = await page.locator("#inspector .graph-preview .gp-node").count()
        loops = await page.locator("#inspector .graph-preview .gp-loop").count()
        print(f"  process editor: {steps} steps drawn, {loops} loop edge(s)")
        assert steps, "the graph editor drew no steps"
        await shot(page, "designer-process", f"{steps} steps, {loops} loop")

        # 5. Continuity and scale on one agent: who stands in, and how many
        #    of it run. Both were model fields the canvas could not edit.
        await page.fill("#explorer-filter", "financial-controller")
        await page.wait_for_timeout(400)
        await page.click("#explorer-tree >> text=financial-controller >> nth=0")
        await page.wait_for_timeout(900)
        # Both fields sit low in a long inspector, so scroll to them: a
        # screenshot of the part of the form that has not changed is not
        # evidence of anything.
        await page.locator('#inspector label:has-text("successor")').first \
            .scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        picked = await page.locator('#inspector select[name="successor"]') \
            .first.input_value()
        print(f"  continuity: successor = {picked or '(its manager)'}")
        await shot(page, "designer-continuity", f"successor={picked}")

        # 6. A process canvas: the workflow's steps as nodes, its own edges
        #    derived from the spec, laid out by `layered`.
        await page.click('#tabs button[data-view="canvas"]')
        await page.wait_for_timeout(500)
        await page.select_option(".dia-add-process", "payment_review")
        await page.wait_for_timeout(1400)
        steps = await page.locator('#canvas-nodes [data-kind="step"]').count()
        wires = await page.locator("#canvas-edges > path").count()
        print(f"  process canvas: {steps} steps, {wires} edges")
        assert steps, "the process canvas drew no steps"
        await shot(page, "designer-process-canvas", f"{steps} steps, {wires} edges")

        # 7. The views a browser had never rendered. Rendering the UI for the
        #    first time found four defects the whole suite did not; these five
        #    had never been rendered at all, which is exactly where the same
        #    class of defect hides.
        for view, name in (("designer", "designer-agents"),
                           ("catalog", "designer-catalog"),
                           ("marketplace", "designer-marketplace"),
                           ("workspace", "designer-workspace"),
                           ("sessions", "designer-sessions"),
                           ("ops", "designer-operations")):
            if view == "designer":
                await page.click("#btn-components")
                await page.click('#components-list [data-kind="agent-view"]')
            else:
                await page.click(f'#tabs button[data-view="{view}"]')
            await page.wait_for_timeout(1200)
            await shot(page, name)
        # The Components editor and the User guide (ADR-0107).
        await page.click("#btn-components")
        await page.click('#components-list [data-kind="capability"]')
        await page.wait_for_timeout(1000)
        await page.locator("#comp-list .comp-row").first.click()
        await page.wait_for_timeout(600)
        await shot(page, "designer-components")
        await page.click('#tabs button[data-view="user-guide"]')
        await page.wait_for_timeout(800)
        await shot(page, "designer-user-guide")

        # 8. The publish path, showing a real preflight verdict.
        await page.click('#tabs button[data-view="canvas"]')
        await page.wait_for_timeout(500)
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
