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


async def palette_item(page, label: str):
    """The palette entry for `label`, made visible the way a person would.

    The palette is grouped by UML profile (ADR-0111 1.1.0 / ADR-0112 M7):
    kinds whose profiles no diagram draws yet sit in a closed "Other
    profiles" group on the Organisation diagram. A person opens that group
    before dragging, so this does too -- by clicking its summary, not by
    forcing the drag onto an invisible item. If the entry is still hidden
    afterwards the drag fails, which is the point: a kind the palette
    cannot show is a kind nobody can place.
    """
    item = page.locator(".drag-item", has_text=label).first
    if not await item.is_visible():
        closed = page.locator("details:not([open])").filter(
            has=page.locator(".drag-item", has_text=label))
        if await closed.count():
            await closed.first.locator(":scope > summary").click()
            await page.wait_for_timeout(200)
    return item


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**({"executable_path": CHROME} if CHROME else {}))
        page = await browser.new_page(viewport={"width": 1680, "height": 1050})
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
        page.on("pageerror", lambda e: print("PAGEERROR", e))
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
        await (await palette_item(page, "Guardrail")).drag_to(
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
                   guardrails: (d.spec().organization.guardrails || []).length };
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
          const g = (window.designer.spec().organization.guardrails || [])
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
          const caps = new Set((s.organization.capabilities || []).map((c) => c.id));
          const roles = new Map((s.organization.role_definitions || []).map((r) => [r.id, r]));
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
            await (await palette_item(page, label)).drag_to(
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
        await drop("Team", 500, 170)
        # Close enough that the old proximity rule would have nested it, and
        # outside the first team's box — which is now the distinction that
        # matters: *near* links nothing, *inside* links (checked below).
        await drop("Team", 560, 300)
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

        # -- 4c. What a link sets is what the inspector says ---------------
        # Reported by a user: link a sub-agent to an agent on the canvas, see
        # the line, and find the sub-agent's "parent" blank in Properties.
        # The link moved it into that agent's list; the inspector read a
        # `parent` key nothing ever set. Checked against the spec, not the
        # picture, because the picture was always right.
        await drop("Sub-agent", 520, 560)
        sub = await page.evaluate("""() => {
          const subs = window.designer.agents().flatMap(({ agent }) =>
            (agent.subagents || []).map((s) => ({ sub: s.id, of: agent.id })));
          return subs[subs.length - 1];
        }""")
        other = next(a for a in agents if a != sub["of"])
        await page.click(f'#canvas-nodes [data-id="{other}"]', button="right")
        await page.wait_for_timeout(400)
        await page.get_by_role("button", name="Link from here").first.click()
        await page.wait_for_timeout(400)
        await page.click(f'#canvas-nodes [data-id="{sub["sub"]}"]')
        await page.wait_for_timeout(700)
        owner = await page.evaluate("""(id) => window.designer.agents().find(
          ({ agent }) => (agent.subagents || []).some((s) => s.id === id)
        )?.agent.id""", sub["sub"])
        check("linking a sub-agent to an agent moves it under that agent",
              owner == other, f"{sub['sub']} is under {owner}, linked from {other}")

        await page.click(f'#canvas-nodes [data-id="{sub["sub"]}"]')
        await page.wait_for_timeout(700)
        shown = await page.locator('#inspector select[name="parent"]').input_value()
        check("the inspector shows the parent the link set",
              shown == other, f"parent shows {shown!r}, spec says {owner!r}")

        third = next(a for a in agents if a not in (other, sub["of"]))
        await page.locator('#inspector select[name="parent"]').select_option(third)
        await page.wait_for_timeout(700)
        moved = await page.evaluate("""(id) => window.designer.agents().find(
          ({ agent }) => (agent.subagents || []).some((s) => s.id === id)
        )?.agent.id""", sub["sub"])
        stray = await page.evaluate("""(id) => window.designer.agents()
          .flatMap(({ agent }) => agent.subagents || [])
          .find((s) => s.id === id)?.parent ?? null""", sub["sub"])
        check("choosing a parent in the inspector moves the sub-agent",
              moved == third and stray is None,
              f"now under {moved}; stray parent key {stray!r}")

        # Reported by a user: an agent's leadership could not be set from its
        # Properties at all. It is the team's `leader`, so ticking it on the
        # agent has to set that.
        follower = await page.evaluate("""() => {
          for (const t of window.designer.teams()) {
            const m = (t.members || []).find((x) => x.id !== t.leader);
            if (m && t.leader) return { agent: m.id, team: t.id, was: t.leader };
          }
          return null;
        }""")
        await page.click(f'#canvas-nodes [data-id="{follower["agent"]}"]')
        await page.wait_for_timeout(700)
        await page.locator('#inspector input[name="leads_team"]').check()
        await page.wait_for_timeout(700)
        leader = await page.evaluate("""(id) => window.designer.teams()
          .find((t) => t.id === id)?.leader""", follower["team"])
        check("ticking 'leads team' on an agent makes it the team's leader",
              leader == follower["agent"],
              f"{follower['team']} leader {follower['was']} → {leader}")

        # -- 4d. An association, drawn from either end ---------------------
        # Asked for by a user: associate a knowledge source with one or more
        # agents. The line starts at the knowledge — the reverse of how the
        # model stores it (on the agent) — and the model is what writes it
        # (ADR-0101, ADR-0103).
        await drop("Knowledge", 760, 660)
        source = await page.evaluate("""() => {
          const k = window.designer.spec().organization.knowledge || [];
          return k.length ? k[k.length - 1].id : null;
        }""")
        readers = [a for a in agents if a != source][:2]
        for reader in readers:
            await page.click(f'#canvas-nodes [data-id="{source}"]', button="right")
            await page.wait_for_timeout(400)
            await page.get_by_role("button", name="Link from here").first.click()
            await page.wait_for_timeout(400)
            await page.click(f'#canvas-nodes [data-id="{reader}"]')
            await page.wait_for_timeout(900)
        consulted = await page.evaluate("""(id) => window.designer.agents()
          .filter(({ agent }) => (agent.knowledge || []).includes(id))
          .map(({ agent }) => agent.id)""", source)
        check("a knowledge source linked from its own end reaches two agents",
              sorted(consulted) == sorted(readers),
              f"{source} consulted by {consulted}, linked to {readers}")
        copies = await page.evaluate("""(id) => (window.designer.spec()
          .organization.knowledge || []).filter((k) => k.id === id).length""",
                                     source)
        check("the knowledge source is still one element", copies == 1,
              f"{copies} copies of {source}")

        # -- 4e. Where a box is let go is a gesture (ADR-0103) ---------------
        # Asked for by a user: an environment box that can be resized, and an
        # agent dropped into it is deployed there. Dragged out, undeployed;
        # dragged onto another team, moved. The model decides each.
        async def centre_of(node_id):
            box = await page.locator(f'#canvas-nodes [data-id="{node_id}"]').bounding_box()
            return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

        async def drag(node_id, x, y):
            sx, sy = await centre_of(node_id)
            await page.mouse.move(sx, sy)
            await page.mouse.down()
            await page.mouse.move((sx + x) / 2, (sy + y) / 2, steps=5)
            await page.mouse.move(x, y, steps=5)
            await page.mouse.up()
            await page.wait_for_timeout(1200)

        await drop("Environment", 900, 480)
        env = await page.evaluate("""() => {
          const e = window.designer.spec().organization.environments || [];
          return e[e.length - 1].id;
        }""")
        mover = agents[-1]
        # Mouse events land only inside the window, and the canvas is larger
        # than it: set the agent and the environment side by side, in view.
        await page.evaluate("""([a, e]) => {
          const d = window.designer;
          const nodes = d.diagram().nodes;
          const surface = document.querySelector("#canvas");
          nodes[e].x = nodes[a].x + (nodes[a].width || 200) + 60;
          nodes[e].y = nodes[a].y;
          d.renderCanvas();
          surface.scrollTo({ left: Math.max(0, nodes[a].x - 60),
                             top: Math.max(0, nodes[a].y - 60) });
        }""", [mover, env])
        await page.wait_for_timeout(400)
        before = await page.evaluate("""(id) =>
          ({ ...window.designer.diagram().nodes[id] })""", env)
        handle = page.locator(f'#canvas-nodes [data-id="{env}"] .n-resize')
        hb = await handle.bounding_box()
        await page.mouse.move(hb["x"] + 5, hb["y"] + 5)
        await page.mouse.down()
        await page.mouse.move(hb["x"] + 125, hb["y"] + 85, steps=6)
        await page.mouse.up()
        await page.wait_for_timeout(500)
        after = await page.evaluate("""(id) =>
          ({ ...window.designer.diagram().nodes[id] })""", env)
        check("an environment box can be resized, and the size is kept",
              after["width"] > before["width"]
              and after["height"] > before["height"],
              f"{before['width']}x{before['height']} → "
              f"{after['width']}x{after['height']}")

        home = await centre_of(mover)
        ex, ey = await centre_of(env)
        await drag(mover, ex, ey)
        deployed = await page.evaluate("""([a, e]) => (window.designer.agents()
          .find(({ agent }) => agent.id === a)?.agent.environments || [])
          .some((x) => x.environment === e)""", [mover, env])
        check("an agent dropped into an environment box is deployed there",
              deployed, f"{mover} in {env}: {deployed}")

        await drag(mover, home[0], home[1])
        still = await page.evaluate("""([a, e]) => (window.designer.agents()
          .find(({ agent }) => agent.id === a)?.agent.environments || [])
          .some((x) => x.environment === e)""", [mover, env])
        check("dragged back out, it is no longer deployed there", not still,
              f"{mover} in {env}: {still}")

        target_team = await page.evaluate("""(a) => {
          const d = window.designer;
          const mine = d.teams().find((t) => (t.members || [])
            .some((m) => m.id === a))?.id;
          return d.teams().map((t) => t.id)
            .find((id) => id !== mine && d.diagram().nodes[id]) || null;
        }""", mover)
        if target_team:
            await page.evaluate("""([a, t]) => {
              const nodes = window.designer.diagram().nodes;
              nodes[t].x = nodes[a].x; nodes[t].y = nodes[a].y + 180;
              window.designer.renderCanvas();
            }""", [mover, target_team])
            await page.wait_for_timeout(300)
            tx, ty = await centre_of(target_team)
            await drag(mover, tx, ty)
            now_in = await page.evaluate("""(a) => window.designer.teams()
              .find((t) => (t.members || []).some((m) => m.id === a))?.id""",
                                         mover)
            check("an agent dragged onto another team becomes its member",
                  now_in == target_team, f"{mover} is in {now_in}, "
                  f"dragged onto {target_team}")

        # A Properties value the model refuses is put back, with the reason.
        await page.click(f'#canvas-nodes [data-id="{mover}"]')
        await page.wait_for_timeout(500)
        field = page.locator('#inspector [name="successor"]')
        if await field.count():
            # Whatever the control offers, set the agent as its own successor
            # the way a person's edit would arrive: a value, then its event.
            await field.evaluate("""(e, a) => {
              if (e.tagName === "SELECT" && ![...e.options].some((o) => o.value === a)) {
                e.add(new Option(a, a));
              }
              e.value = a;
              e.dispatchEvent(new Event("input", { bubbles: true }));
              e.dispatchEvent(new Event("change", { bubbles: true }));
            }""", mover)
            await page.wait_for_timeout(1800)
            kept = await page.evaluate("""(a) => window.designer.agents()
              .find(({ agent }) => agent.id === a)?.agent.successor || ""
            """, mover)
            status = (await page.text_content("#status") or "")
            check("a value the model refuses in Properties is put back",
                  kept != mover and "put back" in status,
                  f"successor={kept!r}; status={status[:90]!r}")

        # -- 4f. Filtering the canvas by relationship (ADR-0105) -----------
        await page.evaluate("() => window.designer.renderCanvas()")
        await page.wait_for_timeout(300)
        drawn = lambda uml: page.evaluate(
            "(u) => document.querySelectorAll(`#canvas-edges path[data-uml='${u}']`).length",
            uml)
        before = await drawn("composition")
        check("the canvas draws the model's relationships tagged by UML kind",
              before > 0, f"{before} composition edge(s)")
        consults = await page.evaluate("""() => document.querySelectorAll(
          "#canvas-edges path[data-rel='consults']").length""")
        check("a knowledge source is drawn as an edge to each agent that "
              "consults it", consults >= 2, f"{consults} 'consults' edge(s)")
        await page.locator('#edge-filter label.ef-uml[data-uml="composition"] input').uncheck()
        await page.wait_for_timeout(300)
        after = await drawn("composition")
        check("unticking a UML kind hides its edges", after == 0,
              f"{before} → {after}")
        await page.locator('#edge-filter label.ef-uml[data-uml="composition"] input').check()
        await page.wait_for_timeout(300)
        await page.locator('#edge-filter details[data-uml="association"] > summary').click()
        await page.locator('#edge-filter input[data-rel="consults"]').uncheck()
        await page.wait_for_timeout(300)
        hidden = await page.evaluate("""() => document.querySelectorAll(
          "#canvas-edges path[data-rel='consults']").length""")
        others = await drawn("association")
        check("unticking one relationship hides only that one",
              hidden == 0 and others > 0 and await drawn("composition") == before,
              f"consults {hidden}, other associations {others}")
        await page.locator('#edge-filter input[data-rel="consults"]').check()

        # -- 4g. Side panels: resize, minimise, restore, maximise ----------
        layout = page.locator("#view-canvas .canvas-layout")
        left = page.locator("#view-canvas .canvas-layout > .panel-side[data-side='left']")
        right = page.locator("#view-canvas .canvas-layout > .panel-side[data-side='right']")
        w0 = (await left.bounding_box())["width"]
        grip = left.locator(":scope > .panel-resizer")
        gb = await grip.bounding_box()
        await page.mouse.move(gb["x"] + 3, gb["y"] + 200)
        await page.mouse.down()
        await page.mouse.move(gb["x"] + 83, gb["y"] + 200, steps=6)
        await page.mouse.up()
        await page.wait_for_timeout(300)
        w1 = (await left.bounding_box())["width"]
        check("the left panel resizes from its edge", w1 > w0 + 40,
              f"{w0:.0f}px → {w1:.0f}px")
        await left.locator(":scope > .panel-ctl .pc-min").click()
        await page.wait_for_timeout(300)
        wmin = (await left.bounding_box())["width"]
        check("the left panel minimises to its controls", wmin < 60,
              f"{wmin:.0f}px")
        await left.locator(":scope > .panel-ctl .pc-restore").click()
        await page.wait_for_timeout(300)
        wr = (await left.bounding_box())["width"]
        check("restored, it returns to the width it had", abs(wr - w1) < 4,
              f"{wr:.0f}px, was {w1:.0f}px")
        await right.locator(":scope > .panel-ctl .pc-max").click()
        await page.wait_for_timeout(300)
        total = (await layout.bounding_box())["width"]
        rw = (await right.bounding_box())["width"]
        canvas_seen = await page.locator("#view-canvas .canvas-wrap").is_visible()
        check("the right panel maximises over the screen",
              rw > total * 0.9 and not canvas_seen,
              f"{rw:.0f}px of {total:.0f}px; canvas visible {canvas_seen}")
        await right.locator(":scope > .panel-ctl .pc-restore").click()
        await page.wait_for_timeout(300)
        check("restored, the canvas is back",
              await page.locator("#view-canvas .canvas-wrap").is_visible())
        await left.locator(":scope > .panel-resizer").dblclick()
        await page.wait_for_timeout(300)

        # And on a two-column screen: the same controls, the same behaviour.
        await page.click('#tabs button[data-view="org"]')
        await page.wait_for_timeout(600)
        org_right = page.locator("#view-org .split > .panel-side[data-side='right']")
        await org_right.locator(":scope > .panel-ctl .pc-min").click()
        await page.wait_for_timeout(300)
        ow = (await org_right.bounding_box())["width"]
        check("the org chart's side panel minimises too", ow < 60, f"{ow:.0f}px")
        await org_right.locator(":scope > .panel-ctl .pc-restore").click()
        await page.click('#tabs button[data-view="canvas"]')
        await page.wait_for_timeout(600)

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
          const g = (rec.record.spec.organization.guardrails || [])
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
        #
        # A Policy rather than the Data class this used: data classes are
        # drawn on the Data diagram now (ADR-0111 1.1.0), and this section is
        # about the Organisation canvas, whose Authority profile has Policy.
        dropped = []
        for _ in range(3):
            await (await palette_item(page, "Policy")).drag_to(
                page.locator("#canvas"), target_position={"x": 980, "y": 240})
            await page.wait_for_timeout(500)
        dropped = await page.evaluate("""() => {
          const d = window.designer;
          const classes = d.spec().organization.policies || [];
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
          const classes = d.spec().organization.policies || [];
          if (classes.length < 2) return { refused: null };
          const target = classes[1];
          const before = target.id;
          // What a person does in the form: type an id another component has.
          try { d.renameComponent("policy", before, classes[0].id); }
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
        await (await palette_item(page, "Tool")).drag_to(
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
                   toolsDeclared: (d.spec().organization.tools || []).map((t) => t.id) };
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
        # A team holds agents, roles and teams; a Policy is none of those.
        await (await palette_item(page, "Policy")).drag_to(
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
        await (await palette_item(page, "Guardrail")).drag_to(
            page.locator("#canvas"), target_position={"x": 820, "y": 160})
        await page.wait_for_timeout(600)
        split = await page.evaluate("""() => {
          const d = window.designer;
          const layout = d.state.record.layout;
          const counts = Object.fromEntries(Object.values(layout.diagrams)
            .map((x) => [x.name, Object.keys(x.nodes).length]));
          return { counts, guardrails: (d.spec().organization.guardrails || []).length };
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
        await (await palette_item(page, "Channel")).drag_to(
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
                   channels: (d.spec().organization.channels || []).length };
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

        # -- 12. What a person reported (ADR-0106) --------------------------
        # "I am not able to drag and drop a team and agent into an
        # environment": a team dropped in deploys its agents; an agent dropped
        # from the palette into the box is deployed there.
        # Back on the organisation's own diagram, scrolled to open ground.
        await page.evaluate("""() => {
          const d = window.designer;
          const main = Object.keys(d.state.record.layout.diagrams)[0];
          d.openDiagram(main);
        }""")
        await page.wait_for_timeout(600)
        await page.evaluate("""() => document.querySelector("#canvas")
          .scrollTo({ left: 0, top: 0 })""")
        await page.wait_for_timeout(300)
        await drop("Environment", 900, 300)
        env2 = await page.evaluate("""() => {
          const e = window.designer.spec().organization.environments || [];
          return e[e.length - 1].id;
        }""")
        team = await page.evaluate("""() => window.designer.teams()
          .find((t) => (t.members || []).length
                && window.designer.diagram().nodes[t.id])?.id""")
        await page.evaluate("""([t, e]) => {
          const d = window.designer, nodes = d.diagram().nodes;
          nodes[e].x = nodes[t].x + (nodes[t].width || 200) + 80;
          nodes[e].y = nodes[t].y; nodes[e].width = 420; nodes[e].height = 260;
          d.renderCanvas();
          document.querySelector("#canvas").scrollTo(
            { left: Math.max(0, nodes[t].x - 60), top: Math.max(0, nodes[t].y - 60) });
        }""", [team, env2])
        await page.wait_for_timeout(400)
        ex, ey = await centre_of(env2)
        await drag(team, ex, ey)
        members = await page.evaluate("""([t, e]) => {
          const team = window.designer.teams().find((x) => x.id === t);
          return (team.members || []).map((m) => ({ id: m.id,
            in: (m.environments || []).some((x) => x.environment === e) }));
        }""", [team, env2])
        check("a team dropped into an environment deploys its agents",
              members and all(m["in"] for m in members), json.dumps(members))

        known = await page.evaluate("""() => window.designer.agents()
          .map(({ agent }) => agent.id)""")
        box = await page.locator(f'#canvas-nodes [data-id="{env2}"]').bounding_box()
        surface = await page.locator("#canvas").bounding_box()
        await (await palette_item(page, "Agent")).drag_to(
            page.locator("#canvas"), target_position={
                "x": box["x"] - surface["x"] + box["width"] - 120,
                "y": box["y"] - surface["y"] + box["height"] - 60})
        await page.wait_for_timeout(1200)
        fresh = await page.evaluate("""([e, known]) => {
          const a = window.designer.agents().map(({ agent }) => agent)
            .find((x) => !known.includes(x.id));
          return a ? { id: a.id, in: (a.environments || [])
            .some((x) => x.environment === e) } : { id: null, in: false };
        }""", [env2, known])
        check("an agent dropped from the palette into an environment is "
              "deployed there", fresh["in"], json.dumps(fresh))

        # "the comma separated must be replaced with selectors": a reference
        # field is a searchable dropdown of what the design has.
        await page.click(f'#canvas-nodes [data-id="{fresh["id"]}"]')
        await page.wait_for_timeout(600)
        knowledge = page.locator('#inspector [data-field-name="knowledge"]')
        search = knowledge.locator(".reflist-search")
        check("a reference field is a searchable picker, not comma text",
              await search.count() == 1
              and await page.locator('#inspector input[placeholder="comma separated"]').count() == 0,
              f"{await search.count()} picker(s)")
        choice = await page.evaluate("""() =>
          (window.designer.spec().organization.knowledge || [])[0]?.id""")
        if choice:
            await search.fill(choice)
            await page.wait_for_timeout(900)
            chips = await knowledge.locator(f'.ref-pill[data-id="{choice}"]').count()
            check("choosing from the picker adds it", chips == 1, choice)

        # "clearly define the mandatory fields and highlight any errors":
        # every form marks what it needs and says what is wrong under it.
        await page.click("#btn-ws-new")
        await page.wait_for_timeout(300)
        wsform = page.locator("#wsform")
        check("a required field is marked", await wsform.locator("label .req").count() >= 1)
        await wsform.locator("button[type=submit]").click()
        await page.wait_for_timeout(300)
        under = await wsform.locator("input[name=name] ~ .field-error").text_content() \
            if await wsform.locator("input[name=name] ~ .field-error").count() else ""
        check("submitting without it says so under the field",
              "required" in under.lower()
              and await wsform.locator("input[name=name].invalid").count() == 1,
              repr(under))
        await wsform.locator("input[name=name]").fill("Second workspace")
        check("typing clears the error",
              await wsform.locator(".field-error").count() == 0)
        await wsform.locator("button[type=submit]").click()
        await page.wait_for_timeout(1500)
        names = await page.evaluate("""() => [...document.querySelectorAll(
          "#ws-select option")].map((o) => o.textContent)""")
        check("a workspace is created", "Second workspace" in names, str(names))

        await page.click("#btn-ws-edit")
        await page.wait_for_timeout(300)
        await wsform.locator("input[name=name]").fill("Renamed workspace")
        await wsform.locator("button[type=submit]").click()
        await page.wait_for_timeout(1200)
        names = await page.evaluate("""() => [...document.querySelectorAll(
          "#ws-select option")].map((o) => o.textContent)""")
        check("a workspace is renamed", "Renamed workspace" in names, str(names))

        # Import a design from a file on this computer, into this workspace.
        await page.click('#tabs button[data-view="org"]')
        await page.wait_for_timeout(500)
        await page.click("#btn-org-import")
        await page.wait_for_timeout(300)
        importform = page.locator("#importform")
        await importform.locator("button[type=submit]").click()
        await page.wait_for_timeout(300)
        check("importing without a file says so under the file field",
              await importform.locator("input[name=file] ~ .field-error").count() == 1)
        await importform.locator("input[name=file]").set_input_files(
            str(ROOT / "examples" / "sentinel" / "sentinel.secops.system.yaml"))
        await importform.locator("button[type=submit]").click()
        # Wait for the import, not for a fixed time: on a slow runner 2.5 s
        # was sometimes not enough, and the delete below then found an empty
        # workspace, hid its cascade box and timed out ticking it.
        try:
            await page.wait_for_function("""() => [...document.querySelectorAll(
              "#sys-select option")].some((o) => o.textContent.includes("Sentinel"))""",
                                         timeout=15000)
        except Exception:                                   # noqa: BLE001
            pass                                            # the check says so
        orgs = await page.evaluate("""() => [...document.querySelectorAll(
          "#sys-select option")].map((o) => o.textContent)""")
        check("a design is imported from a file on disk",
              any("Sentinel" in o for o in orgs), str(orgs))

        # And deleted with its organisations, only when asked in words.
        await page.click("#btn-ws-delete")
        await page.wait_for_timeout(300)
        delform = page.locator("#wsdeleteform")
        await delform.locator("button[type=submit]").click()
        await page.wait_for_timeout(300)
        check("deleting asks for the name, under the field",
              await delform.locator("input[name=confirm] ~ .field-error").count() == 1)
        await delform.locator("input[name=confirm]").fill("Renamed workspace")
        await delform.locator("input[name=cascade]").check()
        await delform.locator("button[type=submit]").click()
        await page.wait_for_timeout(1500)
        names = await page.evaluate("""() => [...document.querySelectorAll(
          "#ws-select option")].map((o) => o.textContent)""")
        check("the workspace is deleted", "Renamed workspace" not in names,
              str(names))

        # "do we still need the Authority menu item?": a review, reached
        # where review happens (ADR-0108), not a tab.
        check("there is no Authority tab",
              await page.locator('#tabs button[data-view="authority"]').count() == 0)
        await page.click('#tabs button[data-view="org"]')
        await page.wait_for_timeout(400)
        await page.click("#btn-org-authority")
        await page.wait_for_timeout(1500)
        resolved = await page.locator("#authority-agents > *").count()
        check("Review authority opens the resolved review",
              await page.locator("#view-authority.active").count() == 1
              and resolved > 0, f"{resolved} agent(s)")
        await page.click("#btn-authority-back")
        await page.wait_for_timeout(400)
        check("Back returns to where it was opened",
              await page.locator("#view-org.active").count() == 1)

        await page.click('#tabs button[data-view="canvas"]')
        await page.wait_for_timeout(600)
        some_agent = await page.evaluate("""() => Object.values(
          window.designer.diagram().nodes).find((n) => n.kind === "agent")?.id""")
        await page.evaluate("(id) => document.querySelector(`#canvas-nodes [data-id='${id}']`)?.scrollIntoView()", some_agent)
        await page.click(f'#canvas-nodes [data-id="{some_agent}"]')
        await page.wait_for_timeout(1500)
        eff = (await page.locator("#inspector .effective-authority").text_content()) or ""
        check("an agent's Properties shows its effective authority",
              "resolving" not in eff and ("declared here" in eff
              or "inherited" in eff or "Decides nothing" in eff
              or "Save to see" in eff), eff[:120])

        await page.click("#btn-publish")
        await page.wait_for_timeout(2500)
        request = page.locator("#btn-publish-request")
        check("Request deployment waits for the review",
              await request.is_disabled())
        await page.locator("#publish-reviewed").check()
        await page.wait_for_timeout(200)
        verdict_ok = await page.evaluate("() => !!window.designer.state.publishVerdictOk")
        check("ticking the review enables it when the design may be published",
              (not await request.is_disabled()) == verdict_ok,
              f"verdict ok {verdict_ok}")
        await page.click("#btn-publish-close")
        await page.wait_for_timeout(300)

        # "extend the Agents view to the other components": every kind has
        # an editor under the Components menu, using the same form.
        await page.click('#tabs button[data-view="canvas"]')
        await page.wait_for_timeout(500)
        await page.click("#btn-components")
        items = await page.locator("#components-list [role=menuitem]").count()
        check("the Components menu lists every kind", items >= 20, f"{items} item(s)")
        await page.click('#components-list [data-kind="knowledge"]')
        await page.wait_for_timeout(900)
        rows = await page.locator("#comp-list .comp-row").count()
        check("a kind's editor lists its components", rows >= 1, f"{rows} row(s)")
        newform = page.locator("#comp-new form")
        await newform.locator("input[name=id]").fill("")
        await newform.locator("button[type=submit]").click()
        await page.wait_for_timeout(300)
        check("creating one without an id says so under the field",
              await newform.locator("input[name=id] ~ .field-error").count() == 1)
        await newform.locator("input[name=id]").fill("policies_handbook")
        await newform.locator("button[type=submit]").click()
        await page.wait_for_timeout(1200)
        made = await page.evaluate("""() => (window.designer.spec().organization
          .knowledge || []).some((k) => k.id === "policies_handbook")""")
        form_fields = await page.locator("#comp-form [data-field-name]").count()
        check("it is created through the model and opens in the form",
              made and form_fields > 2, f"created {made}; {form_fields} field(s)")
        await page.click('#tabs button[data-view="user-guide"]')
        await page.wait_for_timeout(500)
        toc = await page.locator("#guide-toc .guide-link").count()
        check("the user guide has its contents", toc >= 10, f"{toc} section(s)")

        # The organisation form's required name.
        await page.click('#tabs button[data-view="org"]')
        await page.wait_for_timeout(500)
        await page.click("#btn-org-new")
        await page.wait_for_timeout(300)
        orgform = page.locator("#orgform")
        await orgform.locator("input[name=name]").fill("")
        await orgform.locator("button[type=submit]").click()
        await page.wait_for_timeout(300)
        check("creating an organisation without a name says so under it",
              await orgform.locator("input[name=name] ~ .field-error").count() == 1)

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
