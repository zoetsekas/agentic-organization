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
spec = yaml.safe_load((ROOT / "examples" / "northwind" / "northwind.finance.system.yaml").read_text())
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
ALLOWED_CONSOLE = (
    "fonts.googleapis",
    "ERR_CERT_AUTHORITY_INVALID",
    # The review routes (`/gate`, `/diff`) answer 422 while a design does not
    # compile, and `consequence()` turns that into an empty rail rather than
    # an error to dismiss. This script leaves two deliberately unlinked teams
    # behind, so a design that does not compile is the expected state.
    "status of 422",
)

problems: list[str] = []
results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**({"executable_path": CHROME} if CHROME else {}))
        page = await browser.new_page(viewport={"width": 1680, "height": 1050})
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
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
          const nodes = Object.values(d.diagram().nodes);
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
            "(id) => window.designer.diagram().nodes[id]", nid)
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

        # -- 4b. Linking: explicit, and only where the model allows --------
        # A drop used to nest whatever you dropped near whatever was nearest,
        # so two teams dropped close together came out linked and the only
        # way to see it was to read the YAML.
        async def drop(label, x, y):
            await page.locator(".drag-item", has_text=label).first.drag_to(
                page.locator("#canvas"), target_position={"x": x, "y": y})
            await page.wait_for_timeout(600)

        # Empty ground first: a drop onto an existing box now *links* (checked
        # below), so a test about proximity has to aim at nothing.
        await page.evaluate("""() => {
          const d = window.designer;
          const nodes = Object.values(d.diagram().nodes);
          const bottom = Math.max(...nodes.map((n) => n.y + (n.height || 80)));
          document.querySelector("#canvas").scrollTo({ left: 0, top: bottom + 40 });
        }""")
        await page.wait_for_timeout(500)
        await drop("Team", 500, 300)
        # Close enough that the old proximity rule would have nested it, and
        # outside the first team's box — which is now the distinction that
        # matters: *near* links nothing, *inside* links (checked below).
        await drop("Team", 560, 430)
        siblings = await page.evaluate("""() => {
          const root = window.designer.spec().organization;
          const dropped = (root.teams || [])
            .filter((t) => /^team_\\d+$/.test(t.id));
          return { atRoot: dropped.map((t) => t.id),
                   nested: dropped.some((t) => (t.teams || []).length) };
        }""")
        check("two teams dropped near but not inside are not linked",
              len(siblings["atRoot"]) == 2 and not siblings["nested"],
              json.dumps(siblings))

        newest = siblings["atRoot"][-1]
        await page.click(f'#canvas-nodes [data-id="{newest}"]', button="right")
        await page.wait_for_timeout(400)
        await page.get_by_role("button", name="Link from here").first.click()
        await page.wait_for_timeout(400)
        marks = await page.evaluate("""() => ({
          source: document.querySelectorAll(".node.link-source").length,
          targets: document.querySelectorAll(".node.link-target").length,
        })""")
        check("starting a link marks the source and the legal targets",
              marks["source"] == 1 and marks["targets"] > 0, json.dumps(marks))

        await page.click('#canvas-nodes [data-kind="agent"]')
        await page.wait_for_timeout(700)
        took = await page.evaluate("""(id) => {
          const find = (t) => t.id === id ? t
            : (t.teams || []).map(find).find(Boolean);
          const team = find(window.designer.spec().organization);
          return (team?.members || []).length;
        }""", newest)
        check("linking a team to an agent re-parents the agent", took == 1,
              f"{newest} now has {took} member(s)")

        # From an agent, a team is not a legal target: two agents are related
        # by a declared flow or by belonging to the same organisation, never
        # by a line between them.
        agents = await page.evaluate("""() => Object.values(
          window.designer.diagram().nodes)
          .filter((n) => n.kind === "agent").map((n) => n.id)""")
        await page.click(f'#canvas-nodes [data-id="{agents[0]}"]', button="right")
        await page.wait_for_timeout(400)
        await page.get_by_role("button", name="Link from here").first.click()
        await page.wait_for_timeout(400)
        kinds = await page.evaluate("""() => ({
          legal: [...new Set([...document.querySelectorAll(".node.link-target")]
                    .map((n) => n.dataset.kind))],
          illegal: [...new Set([...document.querySelectorAll(".node.link-no")]
                    .map((n) => n.dataset.kind))],
        })""")
        check("from an agent, a team is not a target",
              "team" in kinds["illegal"] and "agent" in kinds["legal"],
              json.dumps(kinds))
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        check("escape cancels a link in progress",
              await page.evaluate("() => window.designer.state.linking") is None)

        # -- 5. It saves ---------------------------------------------------
        await page.click("#btn-save")
        await page.wait_for_timeout(2000)
        status = (await page.text_content("#status") or "").strip()
        check("the design saves", "fail" not in status.lower()
              and "error" not in status.lower(), f"status={status!r}")
        persisted = await page.evaluate("""async () => {
          const d = window.designer;
          const rec = await d.dapi(`/systems/${d.state.systemId}`);
          const layout = rec.record.layout;
          const nodes = layout.diagrams[layout.active].nodes;
          const g = (rec.record.spec.guardrails || [])
            .find((x) => (x.description || "").includes("Edited in a browser"));
          return { kinds: [...new Set(Object.values(nodes).map((n) => n.kind))],
                   edited: !!g };
        }""")
        check("the edit survives a round trip to the server",
              persisted["edited"], json.dumps(persisted["kinds"]))

        # -- 6. An inspector edit reaches the canvas -----------------------
        #
        # Reported from a browser: "changing the details of a component
        # doesn't sync to the canvas". Renaming is the case that shows it,
        # and renaming the *id* is the case that breaks it — the layout node
        # is keyed by id, so the picture and the spec come apart.
        await page.click('#canvas-nodes [data-id="payables"]')
        await page.wait_for_timeout(400)
        name_field = page.locator("#inspector input[name='name']").first
        await name_field.fill("accounts-payable-renamed")
        await name_field.dispatch_event("change")
        await page.wait_for_timeout(500)
        title = await page.text_content('#canvas-nodes [data-id="payables"] .n-title')
        check("renaming a component updates its node on the canvas",
              (title or "").strip() == "accounts-payable-renamed", repr(title))

        id_field = page.locator("#inspector input[name='id']").first
        await id_field.fill("payables_renamed")
        await id_field.dispatch_event("change")
        await page.wait_for_timeout(600)
        after_rename = await page.evaluate("""() => {
          const d = window.designer;
          const nodes = d.diagram().nodes;
          const ids = Object.keys(nodes);
          const walk = (t, out) => {
            (t.members || []).forEach((m) => out.push(m.id));
            (t.teams || []).forEach((c) => walk(c, out));
            return out;
          };
          return { layoutHasNew: ids.includes("payables_renamed"),
                   layoutHasOld: ids.includes("payables"),
                   specIds: walk(d.spec().organization, []),
                   selected: d.state.selected };
        }""")
        check("renaming an id moves its layout node with it",
              after_rename["layoutHasNew"] and not after_rename["layoutHasOld"],
              json.dumps(after_rename))
        check("the renamed component is still on the canvas",
              await page.locator('#canvas-nodes [data-id="payables_renamed"]').count() == 1)

        # -- 7. Ids are unique -------------------------------------------
        #
        # Reported from a browser: "each component must have a unique id and
        # name". Two components sharing an id is not a cosmetic problem — the
        # layout is keyed by id and `findComponent` returns the first match,
        # so the second one is invisible and un-editable.
        dropped = []
        for _ in range(3):
            await page.locator(".drag-item", has_text="Data class").first.drag_to(
                page.locator("#canvas"), target_position={"x": 980, "y": 240})
            await page.wait_for_timeout(500)
        dropped = await page.evaluate("""() => {
          const d = window.designer;
          const classes = d.spec().data_classes || [];
          return { ids: classes.map((c) => c.id),
                   names: classes.map((c) => c.name || c.id) };
        }""")
        check("dropping the same kind three times gives three distinct ids",
              len(set(dropped["ids"])) == len(dropped["ids"]),
              json.dumps(dropped["ids"]))
        check("dropping the same kind three times gives three distinct names",
              len(set(dropped["names"])) == len(dropped["names"]),
              json.dumps(dropped["names"]))

        clash = await page.evaluate("""() => {
          const d = window.designer;
          const classes = d.spec().data_classes || [];
          if (classes.length < 2) return { refused: null };
          const target = classes[1];
          const before = target.id;
          // What a person does in the form: type an id another component has.
          try { d.renameComponent("data_class", before, classes[0].id); }
          catch (e) { return { refused: true, why: e.message }; }
          return { refused: false, now: target.id };
        }""")
        check("an id another component already holds is refused",
              clash.get("refused") is True, json.dumps(clash))

        # -- 8. The four views a modelling tool needs ----------------------
        #
        # Explorer (what the model contains), Palette (what may be added),
        # Outline (where you are), Properties (what the selection says). The
        # Explorer is the one that earns its place: a component declared in
        # the spec but never laid out is invisible on the canvas, and there
        # was no other way to reach it.
        await page.click('#left-tabs button[data-left="explorer"]')
        await page.wait_for_timeout(500)
        shell = await page.evaluate("""() => ({
          explorerRows: document.querySelectorAll('#explorer-tree .ex-row').length,
          offCanvas: document.querySelectorAll('#explorer-tree .ex-row.off-canvas').length,
          outlineNodes: document.querySelectorAll('#outline-svg .o-node').length,
          viewport: getComputedStyle(
            document.querySelector('#outline-viewport')).display,
        })""")
        check("the explorer lists the model", shell["explorerRows"] > 10,
              json.dumps(shell))
        check("a component not on the canvas is still in the explorer",
              shell["offCanvas"] > 0, f"{shell['offCanvas']} off-canvas row(s)")
        check("the outline draws every node",
              shell["outlineNodes"] == await page.locator("#canvas-nodes > *").count(),
              f"{shell['outlineNodes']} outlined")
        check("the outline shows the viewport", shell["viewport"] != "none")

        await page.fill("#explorer-filter", "treasur")
        await page.wait_for_timeout(400)
        filtered = await page.evaluate(
            "() => [...document.querySelectorAll('#explorer-tree .ex-row')]"
            ".map((r) => r.textContent)")
        check("filtering the explorer narrows it",
              0 < len(filtered) < shell["explorerRows"], f"{len(filtered)} row(s)")
        await page.fill("#explorer-filter", "")
        await page.wait_for_timeout(300)

        # Selecting in the explorer opens the properties, which is the whole
        # reason the two are next to each other.
        await page.locator('#explorer-tree .ex-row:not(.group)').nth(1).click()
        await page.wait_for_timeout(400)
        check("selecting in the explorer opens the properties",
              await page.locator("#inspector .form").count() == 1,
              (await page.text_content("#inspector-title") or "").strip())

        await page.click('#left-tabs button[data-left="palette"]')
        await page.wait_for_timeout(300)
        check("the palette is still one tab away",
              await page.locator("#left-palette .drag-item").count() > 10)

        # -- 9. Dropping inside a box links it there ----------------------
        #
        # "Drop items inside other items to create a link between them", and
        # a Tool drawn inside the Agent that holds it — or as an edge once a
        # second agent holds it too.
        await page.click('#left-tabs button[data-left="palette"]')
        await page.wait_for_timeout(300)
        # The worked example is wider than the window, so bring the target
        # into view before aiming at it: a drop onto a node under the
        # inspector lands on the inspector.
        await page.evaluate("""() => {
          const d = window.designer;
          const n = d.diagram().nodes["controller"];
          const surface = document.querySelector("#canvas");
          surface.scrollTo({ left: Math.max(0, n.x - 200),
                             top: Math.max(0, n.y - 200) });
        }""")
        await page.wait_for_timeout(500)
        agent_box = await page.locator(
            '#canvas-nodes [data-id="controller"]').bounding_box()
        canvas_box = await page.locator("#canvas").bounding_box()
        await page.locator(".drag-item", has_text="Tool").first.drag_to(
            page.locator("#canvas"),
            target_position={
                "x": agent_box["x"] - canvas_box["x"] + agent_box["width"] / 2,
                "y": agent_box["y"] - canvas_box["y"] + agent_box["height"] / 2,
            })
        await page.wait_for_timeout(700)
        held = await page.evaluate("""() => {
          const d = window.designer;
          const controller = d.find("agent", "controller");
          return { tools: controller?.tools || [],
                   toolsDeclared: (d.spec().tools || []).map((t) => t.id) };
        }""")
        check("a component dropped inside an agent is held by it",
              len(held["tools"]) == 1, json.dumps(held))

        tool_id = held["tools"][0] if held["tools"] else "tool_1"
        check("a tool held by one agent is drawn inside it, not beside it",
              await page.locator(f'#canvas-nodes [data-id="{tool_id}"]').count() == 0
              and await page.locator(
                  '#canvas-nodes [data-id="controller"] .n-held').count() == 1)

        # Share it with a second agent: now it is a node with edges.
        await page.evaluate("""(id) => {
          const d = window.designer;
          d.find("agent", "payables_renamed").tools = [id];
          d.markDirty("shared");
          d.renderCanvas();
        }""", tool_id)
        await page.wait_for_timeout(500)
        shared = await page.evaluate("""(id) => ({
          node: document.querySelectorAll(
            `#canvas-nodes [data-id="${id}"]`).length,
          chips: document.querySelectorAll('#canvas-nodes .n-held').length,
        })""", tool_id)
        check("a tool two agents hold becomes a node of its own",
              shared["node"] == 1 and shared["chips"] == 0,
              json.dumps(shared))

        # Dropping inside something that cannot hold it says so.
        await page.evaluate("""() => {
          const d = window.designer;
          const n = d.diagram().nodes["treasury"];
          const surface = document.querySelector("#canvas");
          surface.scrollTo({ left: Math.max(0, n.x - 200),
                             top: Math.max(0, n.y - 200) });
        }""")
        await page.wait_for_timeout(500)
        canvas_box = await page.locator("#canvas").bounding_box()
        team_box = await page.locator(
            '#canvas-nodes [data-id="treasury"]').bounding_box()
        await page.locator(".drag-item", has_text="Data class").first.drag_to(
            page.locator("#canvas"),
            target_position={
                "x": team_box["x"] - canvas_box["x"] + team_box["width"] / 2,
                "y": team_box["y"] - canvas_box["y"] + team_box["height"] / 2,
            })
        await page.wait_for_timeout(600)
        said = (await page.text_content("#status") or "")
        check("dropping inside something that cannot hold it says so",
              "does not hold" in said, said.strip()[:90])

        # -- 10. One model, many diagrams --------------------------------
        #
        # A design used to have exactly one picture of itself, so an
        # organisation of any size was one canvas holding every team, agent,
        # capability, policy and endpoint. A diagram of a unit is the
        # drill-down that makes it readable.
        tabs_before = await page.locator("#diagram-bar .dia-tab").count()
        check("the design opens on a named diagram", tabs_before == 1,
              (await page.text_content("#diagram-bar .dia-tab") or "").strip())

        made = await page.evaluate("""() => {
          const d = window.designer;
          const dia = d.addDiagram("treasury");
          return { name: dia.name, root: dia.root,
                   nodes: Object.keys(dia.nodes),
                   active: d.state.record.layout.active === dia.id };
        }""")
        check("a diagram of a team holds that team and what it holds",
              made["root"] == "treasury" and "treasury" in made["nodes"]
              and len(made["nodes"]) > 1, json.dumps(made))
        check("opening a new diagram makes it the one you are on",
              made["active"])
        check("the canvas draws the new diagram and not the old one",
              await page.locator("#canvas-nodes > *").count() == len(made["nodes"]),
              f"{await page.locator('#canvas-nodes > *').count()} node(s)")
        check("both diagrams are offered",
              await page.locator("#diagram-bar .dia-tab").count() == tabs_before + 1)

        # A node placed on one diagram does not appear on the other: that is
        # what makes them different views rather than one canvas twice.
        await page.locator(".drag-item", has_text="Guardrail").first.drag_to(
            page.locator("#canvas"), target_position={"x": 820, "y": 160})
        await page.wait_for_timeout(600)
        split = await page.evaluate("""() => {
          const d = window.designer;
          const layout = d.state.record.layout;
          const counts = Object.fromEntries(Object.values(layout.diagrams)
            .map((x) => [x.name, Object.keys(x.nodes).length]));
          return { counts, guardrails: (d.spec().guardrails || []).length };
        }""")
        check("a node placed on one diagram stays on it",
              len(set(split["counts"].values())) > 1, json.dumps(split["counts"]))

        # …but the *model* is one, so the component is declared once and the
        # explorer lists it whichever diagram is open.
        await page.click('#left-tabs button[data-left="explorer"]')
        await page.fill("#explorer-filter", "guardrail")
        await page.wait_for_timeout(400)
        check("one model behind both diagrams",
              await page.locator("#explorer-tree .ex-row:not(.group)").count() > 0,
              f"{split['guardrails']} guardrail(s) declared")
        await page.fill("#explorer-filter", "")
        await page.click('#left-tabs button[data-left="palette"]')

        # Diagrams survive the round trip, which is the whole point of them
        # living on the layout rather than in the page.
        await page.click("#btn-save")
        await page.wait_for_timeout(1500)
        persisted = await page.evaluate("""async () => {
          const d = window.designer;
          const rec = await d.dapi(`/systems/${d.state.systemId}`);
          const layout = rec.record.layout;
          return { names: Object.values(layout.diagrams).map((x) => x.name),
                   roots: Object.values(layout.diagrams).map((x) => x.root) };
        }""")
        check("diagrams survive a round trip to the server",
              len(persisted["names"]) == 2 and "treasury" in persisted["roots"],
              json.dumps(persisted))

        # -- 11. Undo, and the one place it must refuse ------------------
        #
        # Local, bounded, and thrown away the moment somebody else's change is
        # merged in — an undo that crosses a merge restores a state that was
        # never true for anybody, which is worse than having no undo.
        before = await page.evaluate("""() => {
          const d = window.designer;
          return Object.keys(d.diagram().nodes).length;
        }""")
        await page.locator(".drag-item", has_text="Channel").first.drag_to(
            page.locator("#canvas"), target_position={"x": 640, "y": 300})
        await page.wait_for_timeout(600)
        added = await page.evaluate(
            "() => Object.keys(window.designer.diagram().nodes).length")
        check("a drop is one undoable step", added == before + 1,
              f"{before} -> {added}")
        check("undo is offered once there is something to undo",
              not await page.locator("#btn-undo").is_disabled())

        await page.click("#btn-undo")
        await page.wait_for_timeout(500)
        undone = await page.evaluate("""() => {
          const d = window.designer;
          return { nodes: Object.keys(d.diagram().nodes).length,
                   channels: (d.spec().channels || []).length };
        }""")
        check("undo puts back both the picture and the model",
              undone["nodes"] == before, json.dumps(undone))

        await page.click("#btn-redo")
        await page.wait_for_timeout(500)
        redone = await page.evaluate(
            "() => Object.keys(window.designer.diagram().nodes).length")
        check("redo puts it back again", redone == before + 1,
              f"{before} -> {redone}")

        # Typing is not one undo per keystroke.
        await page.click(f'#canvas-nodes [data-id="treasury"]')
        await page.wait_for_timeout(400)
        depth_before = await page.evaluate(
            "() => window.designer.state.history.past.length")
        field = page.locator("#inspector input[name='name']").first
        await field.click()
        await field.press_sequentially("Group Treasury", delay=30)
        await page.wait_for_timeout(900)
        depth_after = await page.evaluate(
            "() => window.designer.state.history.past.length")
        typed = await page.evaluate("""() => ({
          name: window.designer.find("team", "treasury")?.name,
          dirty: window.designer.state.dirty,
        })""")
        steps = depth_after - depth_before
        check("typing in a form reaches the model",
              typed["name"] != "Treasury", json.dumps(typed))
        check("a run of typing is one undo, not one per keystroke",
              1 <= steps <= 3,
              f"{steps} step(s) for 14 characters, name={typed['name']!r}")

        # And the bug the loose version of this check hid: coalescing keyed
        # on time alone merged two *unrelated* actions that happened close
        # together, so undoing a rename also removed the component dropped a
        # moment earlier.
        quick = await page.evaluate("""() => {
          const d = window.designer;
          const depth = () => d.state.history.past.length;
          const before = depth();
          d.add("channel", "coalesce_probe");
          d.markDirty();                       // a drop-shaped edit
          const team = d.find("team", "treasury");
          team.description = "typed right after";
          d.markDirty("edited treasury.description", true);
          return { steps: depth() - before };
        }""")
        check("two unrelated edits close together are two undo steps",
              quick["steps"] == 2, json.dumps(quick))

        # And the refusal that matters: a merge clears the stack.
        await page.evaluate("""() => {
          window.designer.state.history.past.push(
            { state: "{}", reason: "pretend" });
        }""")
        await page.evaluate("""() => window.designer.handleSaveOutcome({
          status: "merged", current_version: 99,
          record: window.designer.state.record,
        })""")
        await page.wait_for_timeout(400)
        after_merge = await page.evaluate(
            "() => window.designer.state.history.past.length")
        check("a merge throws the undo stack away", after_merge == 0,
              f"{after_merge} step(s) left")
        check("and undo says so rather than offering a lie",
              await page.locator("#btn-undo").is_disabled())

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
