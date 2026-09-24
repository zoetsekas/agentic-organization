"""Drive the two-person flows: locks, and the three-way merge.

Run from the repository root:

    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python3 scripts/concurrency_check.py

`interaction_check.py` drives one person. This drives two, in two browser
contexts, because locking and conflict resolution have no meaning with one and
had never been exercised by anything — the conflict bar is markup that no
person or script had ever seen.

Ana owns the workspace and may break a lock. Ben may edit and may not. They
open the same design and then get in each other's way on purpose.
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

# A second editor, so the lock and merge flows have two people to run
# between. Ana owns the workspace; Ben may edit and may not break a lock.
c.post(f"/api/designer/workspaces/{ws['id']}/members",
       json={"user_id": "ben", "role": "editor"}, headers=A)
print("MEMBER ben=editor", flush=True)

app.state.fabric["tenants"].register(id="northwind", name="Northwind",
                                     namespace_prefix="northwind")
print("SEEDED", ws["id"], sys_["id"], flush=True)



# ------------------------------------------------------------ concurrency

import asyncio
import json

from playwright.async_api import async_playwright

PORT = int(os.environ.get("ORGAGENTS_SHOT_PORT", "8866"))
BASE = f"http://127.0.0.1:{PORT}/ui/"
CHROME = os.environ.get(
    "ORGAGENTS_CHROME",
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
)
ALLOWED_CONSOLE = (
    "fonts.googleapis",
    "ERR_CERT_AUTHORITY_INVALID",
    # The 409 this script provokes on purpose to prove the server refuses a
    # locked save. A browser logs every non-2xx; that one is the check passing.
    "status of 409",
)

problems: list[str] = []
results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name, detail))


async def open_as(browser, who: str):
    """A browser context acting as one person, with the canvas open."""
    context = await browser.new_context(viewport={"width": 1500, "height": 950})
    page = await context.new_page()
    page.on("pageerror", lambda e: problems.append(f"[{who}] pageerror: {e}"))
    page.on("console", lambda m: problems.append(f"[{who}] console: {m.text}")
            if m.type == "error" else None)
    # Dialogs are the UI's confirm/alert; accept them and record what they said.
    page.on("dialog", lambda d: asyncio.ensure_future(
        _remember_and_accept(who, d)))
    await page.goto(BASE, wait_until="networkidle")
    await page.fill("#user-input", who)
    await page.dispatch_event("#user-input", "change")
    await page.wait_for_timeout(1600)
    await page.click('#tabs button[data-view="canvas"]')
    await page.wait_for_timeout(900)
    return page


dialogs: list[str] = []


async def _remember_and_accept(who: str, dialog) -> None:
    dialogs.append(f"[{who}] {dialog.message}")
    await dialog.accept()


async def become(page, who: str) -> None:
    """Re-assert who this page is, after a reload.

    `#user-input` is markup with a default value, so a reload resets it — a
    page reloaded without this is silently acting as somebody else, which is
    exactly how a first run of this script "proved" that a lock does not lock.
    """
    await page.fill("#user-input", who)
    await page.dispatch_event("#user-input", "change")
    await page.wait_for_timeout(1400)
    seen = await page.evaluate("() => window.designer.state.user")
    if seen != who:
        raise SystemExit(f"the page is acting as {seen!r}, not {who!r}")
    await page.click('#tabs button[data-view="canvas"]')
    await page.wait_for_timeout(800)


async def edit_description(page, agent_id: str, text: str) -> None:
    """Type into an agent's description the way a person does."""
    await page.click(f'#canvas-nodes [data-id="{agent_id}"]')
    await page.wait_for_timeout(500)
    box = page.locator("#inspector label", has_text="description").first
    field = box.locator("textarea, input").first
    await field.fill(text)
    await field.dispatch_event("input")
    await page.wait_for_timeout(300)


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME)
        ana = await open_as(browser, "ana")
        ben = await open_as(browser, "ben")

        roles = [
            (await ana.text_content("#role-badge") or "").strip(),
            (await ben.text_content("#role-badge") or "").strip(),
        ]
        check("two people open the same design", all(roles), json.dumps(roles))

        # -- 1. The lock ----------------------------------------------------
        await ana.click("#btn-lock")
        await ana.wait_for_timeout(1200)
        ana_badge = (await ana.text_content("#lock-badge") or "").strip()
        check("acquiring a lock shows it to the holder", "lock" in ana_badge.lower()
              or "you" in ana_badge.lower(), repr(ana_badge))

        # Ben reopens to see the world as it now is, then tries to save.
        await ben.reload(wait_until="networkidle")
        await ben.wait_for_timeout(1200)
        await become(ben, "ben")
        ben_badge = (await ben.text_content("#lock-badge") or "").strip()
        check("the lock is visible to the other person", bool(ben_badge.strip()),
              repr(ben_badge))

        # The lock is enforced *before* the save, not after: the other
        # person's inspector is read-only, so they cannot do work they are
        # about to lose. That is better than refusing at save time, and it is
        # what the UI actually does.
        await ben.click('#canvas-nodes [data-id="controller"]')
        await ben.wait_for_timeout(600)
        field = ben.locator("#inspector label", has_text="description").first \
                   .locator("textarea, input").first
        check("the other person's form is read-only while it is locked",
              await field.is_disabled(), "inspector description")
        disabled = await ben.evaluate("""() => {
          const all = document.querySelectorAll(
            "#inspector input, #inspector textarea, #inspector select");
          return { total: all.length,
                   enabled: [...all].filter((e) => !e.disabled).length };
        }""")
        check("every control in the form is, not just one",
              disabled["enabled"] == 0, json.dumps(disabled))

        # And the server refuses too, whatever a page believes.
        refusal = await ben.evaluate("""async () => {
          const d = window.designer;
          try {
            await d.dapi(`/systems/${d.state.systemId}`, {
              method: "PUT",
              body: JSON.stringify({ spec: d.spec(),
                                     version: d.state.record.version }),
            });
            return { refused: false };
          } catch (err) {
            return { refused: true, status: err.status,
                     detail: JSON.stringify(err.detail).slice(0, 160) };
          }
        }""")
        check("the server refuses the save as well as the form",
              refusal["refused"] and refusal.get("status") == 409,
              json.dumps(refusal))
        check("the refusal names the holder",
              "ana" in json.dumps(refusal).lower(), json.dumps(refusal)[:140])

        # Ben is an editor: he may not break the lock.
        may_break = await ben.evaluate(
            '() => window.designer.state.permissions.includes("lock.break")')
        check("an editor may not break a lock", may_break is False, str(may_break))
        may_break_ana = await ana.evaluate(
            '() => window.designer.state.permissions.includes("lock.break")')
        check("an owner may", may_break_ana is True, str(may_break_ana))

        # Ana releases it.
        await ana.click("#btn-lock")
        await ana.wait_for_timeout(1200)
        released = (await ana.text_content("#lock-badge") or "").strip()
        check("releasing a lock clears it", "unlock" in released.lower()
              or not released, repr(released))

        # -- 1b. An abandoned lock expires ---------------------------------
        # DESIGNER.md claims "an abandoned tab unfreezes itself". Nothing had
        # ever checked it, and there is no heartbeat, so the TTL is the only
        # thing standing between a closed laptop and a design nobody can edit.
        await ana.evaluate("""async () => {
          await window.designer.dapi("/settings", {
            method: "PUT", body: JSON.stringify({ lock_ttl_seconds: 1 }),
          });
        }""")
        await ana.click("#btn-lock")
        await ana.wait_for_timeout(900)
        held = (await ana.text_content("#lock-badge") or "").strip()
        await ben.wait_for_timeout(2500)          # outlive a 1s TTL
        took = await ben.evaluate("""async () => {
          const d = window.designer;
          try {
            await d.dapi(`/systems/${d.state.systemId}/lock`, {
              method: "POST", body: JSON.stringify({ target: "*" }),
            });
            return { got: true };
          } catch (err) { return { got: false, detail: String(err.message) }; }
        }""")
        check("an expired lock stops freezing the design",
              took["got"] is True, f"ana had {held!r}; ben: {json.dumps(took)}")
        # Ben releases what he took; only Ana may put the TTL back, because
        # `designer.settings` is an admin permission and an editor is not one.
        await ben.evaluate("""async () => {
          const d = window.designer;
          await d.dapi(`/systems/${d.state.systemId}/lock?target=*`,
                       { method: "DELETE" });
        }""")
        await ana.evaluate("""async () => {
          await window.designer.dapi("/settings", {
            method: "PUT", body: JSON.stringify({ lock_ttl_seconds: 900 }),
          });
        }""")

        # -- 2. Independent edits merge ------------------------------------
        await ana.reload(wait_until="networkidle"); await ana.wait_for_timeout(1200)
        await become(ana, "ana")
        await ben.reload(wait_until="networkidle"); await ben.wait_for_timeout(1200)
        await become(ben, "ben")
        await ana.evaluate("() => window.designer.state.record.version")

        await edit_description(ana, "controller", "Ana edits the controller")
        await ana.click("#btn-save")
        await ana.wait_for_timeout(1800)
        # Ben is now a version behind and edits a *different* agent.
        await edit_description(ben, "payables", "Ben edits payables")
        await ben.click("#btn-save")
        await ben.wait_for_timeout(2000)
        ben_status = (await ben.text_content("#status") or "").strip()
        conflict_shown = await ben.is_visible("#conflict-bar")
        check("independent edits merge without a conflict",
              not conflict_shown and "fail" not in ben_status.lower(),
              f"status={ben_status!r} bar={conflict_shown}")
        kept = await ben.evaluate("""() => {
          const agents = [];
          const walk = (t) => { (t.members||[]).forEach(a => agents.push(a));
                                (t.teams||[]).forEach(walk); };
          walk(window.designer.spec().organization);
          const f = (id) => (agents.find((a) => a.id === id) || {}).description;
          return { controller: f("controller"), payables: f("payables") };
        }""")
        check("the merge kept both edits",
              "Ana edits" in (kept["controller"] or "")
              and "Ben edits" in (kept["payables"] or ""), json.dumps(kept))

        # -- 3. The same field twice: a real conflict -----------------------
        await ana.reload(wait_until="networkidle"); await ana.wait_for_timeout(1200)
        await become(ana, "ana")
        await ben.reload(wait_until="networkidle"); await ben.wait_for_timeout(1200)
        await become(ben, "ben")

        await edit_description(ana, "controller", "ANA'S VERSION")
        await ana.click("#btn-save")
        await ana.wait_for_timeout(1800)
        await edit_description(ben, "controller", "BEN'S VERSION")
        await ben.click("#btn-save")
        await ben.wait_for_timeout(2200)

        bar = await ben.is_visible("#conflict-bar")
        rows = await ben.locator("#conflict-list .conflict-row").count()
        summary = (await ben.text_content("#conflict-summary") or "").strip()
        check("the same field edited twice raises a conflict", bar and rows > 0,
              f"bar={bar} rows={rows} summary={summary[:70]!r}")
        both = (await ben.text_content("#conflict-list") or "")
        check("the conflict shows both values",
              "ANA'S VERSION" in both and "BEN'S VERSION" in both,
              repr(both[:120]))

        # -- 4. Resolving it -------------------------------------------------
        await ben.click("#btn-resolve-theirs")
        await ben.wait_for_timeout(400)
        await ben.click("#btn-resolve-apply")
        await ben.wait_for_timeout(2200)
        after_status = (await ben.text_content("#status") or "").strip()
        still = await ben.is_visible("#conflict-bar")
        check("taking theirs and applying saves", not still
              and "fail" not in after_status.lower(),
              f"status={after_status!r} bar={still}")
        final = await ben.evaluate("""async () => {
          const d = window.designer;
          const r = await d.dapi(`/systems/${d.state.systemId}`);
          const agents = [];
          const walk = (t) => { (t.members||[]).forEach(a => agents.push(a));
                                (t.teams||[]).forEach(walk); };
          walk(r.record.spec.organization);
          return (agents.find((a) => a.id === "controller") || {}).description;
        }""")
        check("the chosen value is what the server kept",
              final == "ANA'S VERSION", repr(final))

        # -- 5. Breaking a lock ---------------------------------------------
        # The permission was checked above; this drives the flow. Ben takes
        # the lock, Ana takes it off him, and then the question nobody had
        # asked: what does Ben see?
        await ana.reload(wait_until="networkidle"); await ana.wait_for_timeout(1200)
        await become(ana, "ana")
        await ben.reload(wait_until="networkidle"); await ben.wait_for_timeout(1200)
        await become(ben, "ben")

        await ben.click("#btn-lock")
        await ben.wait_for_timeout(1200)
        check("the editor can take a lock",
              "you hold" in (await ben.text_content("#lock-badge") or "").lower(),
              repr((await ben.text_content("#lock-badge") or "").strip()))

        said = len(dialogs)
        await ana.click("#btn-lock")          # refused, then offered the break
        await ana.wait_for_timeout(1800)
        offer = " ".join(dialogs[said:])
        check("the owner is offered the break, and it names the holder",
              "break" in offer.lower() and "ben" in offer.lower(), repr(offer[:120]))
        # The prompt is for a person, so it must read like one. A 409's detail
        # is `{error, lock}`, and stringifying the object showed raw JSON.
        check("the prompt is a sentence, not a JSON blob",
              '{"error"' not in offer and "lck_" not in offer, repr(offer[:160]))

        broken = await ana.evaluate("""async () => {
          const d = window.designer;
          const r = await d.dapi(`/systems/${d.state.systemId}`);
          return { locks: r.locks.map((l) => l.holder) };
        }""")
        check("breaking it releases the holder's lock",
              "ben" not in broken["locks"], json.dumps(broken))
        # The person clicked **Lock**. Breaking alone left the design unlocked
        # and them holding nothing, so they had to click again — and in that
        # gap the holder could take it straight back.
        check("breaking a lock hands it to the person who asked for it",
              broken["locks"] == ["ana"], json.dumps(broken))

        # The audit is the only record that it happened at all.
        events = await ana.evaluate("""async () => {
          const rows = await window.designer.dapi("/audit?limit=50");
          return rows.filter((e) => e.action === "lock.break")
                     .map((e) => ({ actor: e.actor, from: e.lock_holder,
                                    reason: e.reason }));
        }""")
        check("the break is written down, with who lost it",
              bool(events) and events[0]["from"] == "ben"
              and events[0]["actor"] == "ana", json.dumps(events[:2]))

        # -- 6. What the person who lost it sees -----------------------------
        stale = await ben.evaluate("""() => ({
          believes_mine: window.designer.state.locks
            .some((l) => l.holder === window.designer.state.user),
          badge: document.querySelector("#lock-badge").textContent.trim(),
        })""")
        check("the loser is not told — their page still believes it holds it",
              stale["believes_mine"] is True, json.dumps(stale)
              + "  (there is no heartbeat and no push; this is the cost)")

        # Ben keeps working on a design he no longer holds. Now that breaking
        # also takes, his save is refused — which is the honest outcome, and
        # the cost of there being no heartbeat: he did the work first.
        await edit_description(ben, "payables", "Ben after losing the lock")
        await ben.click("#btn-save")
        await ben.wait_for_timeout(2000)
        ben_after = (await ben.text_content("#status") or "").strip()
        check("their save is refused, naming the person who now holds it",
              "lock" in ben_after.lower() and "ana" in ben_after.lower(),
              f"status={ben_after!r}")
        check("and the refusal reads as a sentence",
              '{"error"' not in ben_after, f"status={ben_after!r}")

        await ben.reload(wait_until="networkidle"); await ben.wait_for_timeout(1200)
        await become(ben, "ben")
        truth = (await ben.text_content("#lock-badge") or "").strip()
        check("a reload tells the truth", "ana" in truth.lower(), repr(truth))

        await ben.screenshot(path="/tmp/conflict-bar.png")
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
    print("\nall concurrency checks passed")
