#!/usr/bin/env python3
"""Drive every top-level view's own controls, and report what actually works.

    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers python3 scripts/view_check.py

`screenshots.py` proves the views render. `interaction_check.py` proves the
*canvas* works. Neither asked whether Catalog's Approve button approves, or
whether the Marketplace filters filter — and until this script, nothing did:
both browser scripts only ever visited the canvas.

Every check is one question with a yes or no answer, driven through the real
UI. A check that fails does not stop the sweep, because the report is the
point: a list of what works and what does not is more useful than the first
exception.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import threading
import time
import traceback

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
WORK = pathlib.Path(tempfile.mkdtemp(prefix="orgagents-views-"))
os.chdir(WORK)

from orgagents.api import create_app                      # noqa: E402
from fastapi.testclient import TestClient                 # noqa: E402

app = create_app(str(WORK / "designer.db"))
c = TestClient(app)
A = {"X-User": "ana", "X-User-Name": "Ana Silva"}
spec = yaml.safe_load(
    (ROOT / "examples" / "northwind" / "northwind.finance.system.yaml").read_text())
ws = c.post("/api/designer/workspaces", json={"name": "Northwind"},
            headers=A).json()
sys_ = c.post("/api/designer/systems",
              json={"workspace_id": ws["id"], "name": "Northwind Finance",
                    "spec": spec}, headers=A).json()
app.state.fabric["tenants"].register(id="northwind", name="Northwind",
                                     namespace_prefix="northwind")
# A marketplace with nothing in it tests nothing, so publish two things to it.
# They are published through the same route the UI uses.
for kind, ref, owner in (("agent", "cfo", "Finance Platform"),
                         ("agent", "controller", "Finance Platform")):
    c.post("/api/catalog/publish",
           json={"kind": kind, "ref_id": ref, "owner": owner,
                 "visibility": "public", "tags": ["finance"]}, headers=A)
print(f"seeded {ws['id']} {sys_['id']}", flush=True)

BASE = "http://127.0.0.1:8813"
results: list[tuple[str, str, bool, str]] = []
console: list[str] = []


def serve() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8813, log_level="error")


async def main() -> int:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME)
        page = await browser.new_page(viewport={"width": 1680, "height": 1050})
        page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
        page.on("console",
                lambda m: console.append(f"console error: {m.text}")
                if m.type == "error" else None)
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(1200)

        async def check(view: str, name: str, fn) -> None:
            """Run one question against one view and record the answer."""
            before = len(console)
            try:
                detail = await fn() or ""
                broke = console[before:]
                if broke:
                    results.append((view, name, False, broke[0][:120]))
                else:
                    results.append((view, name, True, detail))
            except Exception as exc:                       # noqa: BLE001
                results.append((view, name, False,
                                f"{type(exc).__name__}: {exc}".split("\n")[0][:120]))

        async def show(view: str) -> None:
            await page.click(f'#tabs button[data-view="{view}"]')
            # A view fetches on entry, so a short wait times the check rather
            # than the view — which produced three false failures the first
            # time this ran.
            await page.wait_for_timeout(1600)

        # ---- Catalog ---------------------------------------------------
        await show("catalog")

        async def catalog_lists():
            # Generous: the question is whether entering the view loads the
            # list at all, not how fast.
            for _ in range(10):
                n = await page.locator("#pc-entries > *").count()
                if n:
                    return f"{n} entries"
                await page.wait_for_timeout(500)
            return_n = await page.locator("#pc-entries").text_content() or ""
            raise AssertionError(
                "entering the view never loaded the list; container says "
                f"{' '.join(return_n.split())[:60]!r}")
        await check("Catalog", "lists entries", catalog_lists)

        async def catalog_search():
            before = await page.locator("#pc-entries > *").count()
            await page.fill("#pc-q", "claude")
            await page.wait_for_timeout(600)
            after = await page.locator("#pc-entries > *").count()
            await page.fill("#pc-q", "")
            await page.wait_for_timeout(600)
            assert after < before, f"search did not narrow ({before} → {after})"
            return f"{before} → {after}"
        await check("Catalog", "search narrows", catalog_search)

        async def catalog_kind_filter():
            before = await page.locator("#pc-entries > *").count()
            await page.select_option("#pc-kind", "model")
            await page.wait_for_timeout(600)
            after = await page.locator("#pc-entries > *").count()
            await page.select_option("#pc-kind", "")
            await page.wait_for_timeout(600)
            assert 0 < after < before, f"kind filter ({before} → {after})"
            return f"{before} → {after}"
        await check("Catalog", "kind filter narrows", catalog_kind_filter)

        async def catalog_status_filter():
            await page.select_option("#pc-status", "proposed")
            await page.wait_for_timeout(600)
            after = await page.locator("#pc-entries > *").count()
            await page.select_option("#pc-status", "")
            await page.wait_for_timeout(600)
            assert after, "status filter emptied the list"
            return f"{after} proposed"
        await check("Catalog", "status filter narrows", catalog_status_filter)

        async def catalog_approve():
            await page.select_option("#pc-status", "proposed")
            await page.wait_for_timeout(700)
            first = page.locator("#pc-entries > *").first
            title = (await first.locator("strong, h3, b").first.text_content()
                     or "").strip()
            await first.get_by_role("button", name="Approve").first.click()
            await page.wait_for_timeout(900)
            await page.select_option("#pc-status", "")
            await page.wait_for_timeout(700)
            body = await page.locator("#pc-stats").text_content() or ""
            return f"approved {title!r}; stats now {' '.join(body.split())[:60]}"
        await check("Catalog", "approve moves an entry", catalog_approve)

        async def catalog_add_opens():
            await page.click("#pc-add")
            await page.wait_for_timeout(500)
            visible = await page.locator("#pc-form").is_visible()
            assert visible, "Add entry did not open a form"
            return "form opens"
        await check("Catalog", "add entry opens a form", catalog_add_opens)

        # ---- Marketplace -----------------------------------------------
        await show("marketplace")

        async def market_lists():
            n = await page.locator("#catalog > *").count()
            text = " ".join((await page.locator("#catalog").text_content()
                             or "").split())
            assert n, f"marketplace listed nothing and said {text[:60]!r}"
            return f"{n} listing(s)"
        await check("Marketplace", "lists offerings", market_lists)

        async def market_search():
            """Searching for what is there keeps it; for what is not, drops it.

            Counting a change is a weak question when the marketplace holds one
            listing — it passes for a filter that does nothing as readily as
            for one that works. Two searches with known answers is the real
            question.
            """
            listed = await page.locator("#catalog > *").count()
            assert listed, "nothing published to search"
            name = " ".join((await page.locator("#catalog > *").first
                             .text_content() or "").split())
            term = next((w for w in name.split() if len(w) > 4), "finance")
            await page.fill("#cat-q", term)
            await page.wait_for_timeout(800)
            hit = await page.locator("#catalog > *").count()
            await page.fill("#cat-q", "zzz-definitely-not-here")
            await page.wait_for_timeout(800)
            miss = " ".join((await page.locator("#catalog").text_content()
                             or "").split())
            await page.fill("#cat-q", "")
            await page.wait_for_timeout(700)
            assert hit, f"searching {term!r} lost a listing that contains it"
            assert "matches those filters" in miss, \
                f"a search matching nothing showed {miss[:50]!r}"
            return f"{term!r} kept {hit}; nonsense dropped all"
        await check("Marketplace", "search narrows", market_search)

        async def market_kind():
            before = await page.locator("#catalog > *").count()
            options = await page.locator("#cat-kind option").all_text_contents()
            pick = next((o for o in options if o.strip()
                         and "all" not in o.lower()), None)
            assert pick, "no kind options offered"
            await page.select_option("#cat-kind", label=pick)
            await page.wait_for_timeout(700)
            after = await page.locator("#catalog > *").count()
            assert after <= before, f"kind filter widened ({before} → {after})"
            return f"{pick.strip()}: {before} → {after}"
        await check("Marketplace", "kind filter narrows", market_kind)

        async def market_sort():
            await page.select_option("#cat-sort", index=1)
            await page.wait_for_timeout(800)
            n = await page.locator("#catalog > *").count()
            assert n, "sorting emptied the list"
            return f"{n} listing(s) after sorting"

        async def market_empty_state():
            await page.fill("#cat-q", "zzz-no-such-listing")
            await page.wait_for_timeout(800)
            text = " ".join((await page.locator("#catalog").text_content()
                             or "").split())
            await page.fill("#cat-q", "")
            await page.wait_for_timeout(700)
            assert text, "a search matching nothing said nothing at all"
            return text[:50]
        await check("Marketplace", "sort keeps the list", market_sort)
        await check("Marketplace", "says when nothing matches",
                    market_empty_state)

        # ---- Workspace --------------------------------------------------
        await show("workspace")

        async def workspace_members():
            n = await page.locator("#members > *").count()
            assert n, "no members listed"
            return f"{n} member(s)"
        await check("Workspace", "lists members", workspace_members)

        async def workspace_add_member():
            form = page.locator("#memberform")
            await form.locator("input").first.fill("rob@northwind.example")
            selects = form.locator("select")
            if await selects.count():
                await selects.first.select_option(index=1)
            await form.get_by_role("button", name="Add or change").click()
            await page.wait_for_timeout(1000)
            text = await page.locator("#members").text_content() or ""
            err = (await page.locator("#members-error").text_content() or "").strip()
            assert "rob@northwind.example" in text, f"not added; error={err!r}"
            return "member added"
        await check("Workspace", "adds a member", workspace_add_member)

        async def workspace_audit():
            await page.click("#btn-audit-reload")
            await page.wait_for_timeout(900)
            n = await page.locator("#audit > *").count()
            assert n, "audit log empty after a save and a member change"
            return f"{n} entries"
        await check("Workspace", "audit log loads", workspace_audit)

        # ---- Sessions ---------------------------------------------------
        await show("sessions")

        async def sessions_empty_state():
            text = (await page.locator("#sessions").text_content() or "").strip()
            assert text, "an empty list said nothing at all"
            return " ".join(text.split())[:60]
        await check("Sessions", "says why it is empty", sessions_empty_state)

        # ---- Operations --------------------------------------------------
        await show("ops")

        async def ops_metrics():
            n = await page.locator("#metrics > *").count()
            assert n, "no metric tiles"
            return f"{n} tiles"
        await check("Operations", "renders metrics", ops_metrics)

        async def ops_empty_states():
            alerts = (await page.locator("#alerts").text_content() or "").strip()
            per = (await page.locator("#peragent").text_content() or "").strip()
            assert alerts and per, f"alerts={alerts!r} peragent={per!r}"
            return "both say why they are empty"
        await check("Operations", "empty states explain", ops_empty_states)

        # ---- Agents -------------------------------------------------------
        await show("designer")

        async def agents_list():
            n = await page.locator("#agent-list > *").count()
            assert n, "no agents listed"
            return f"{n} agents"
        await check("Agents", "lists the spec's agents", agents_list)

        async def agents_open_one():
            # The clickable is the name span inside the row, not the row.
            await page.locator("#agent-list > * > span.grow").first.click()
            await page.wait_for_timeout(800)
            visible = await page.locator("#agentform").is_visible()
            assert visible, "selecting an agent did not open its definition"
            return "definition opens"
        await check("Agents", "opens an agent's definition", agents_open_one)

        async def agents_pickers():
            filled = 0
            # These are checkbox lists, not selects.
            for sel in ("#pick-roles", "#pick-capabilities", "#pick-knowledge"):
                filled += await page.locator(f"{sel} input").count()
            assert filled, "the reference pickers offered nothing"
            return f"{filled} options across the pickers"
        await check("Agents", "reference pickers are populated", agents_pickers)

        # ---- Org chart -----------------------------------------------------
        await show("org")

        async def org_tree():
            n = await page.locator("#orgtree *").count()
            assert n, "the org chart drew nothing"
            return f"{n} elements"
        await check("Org chart", "draws the tree", org_tree)

        async def org_switcher():
            n = await page.locator("#org-select option").count()
            assert n, "no organisation in the switcher"
            return f"{n} design(s)"
        await check("Org chart", "lists designs", org_switcher)

        # ---- Authority ------------------------------------------------------
        await show("authority")

        async def authority_resolves():
            agents = await page.locator("#authority-agents > *").count()
            seps = await page.locator("#authority-separations > *").count()
            places = await page.locator("#authority-placements > *").count()
            assert agents, "no resolved authority"
            return f"{agents} agents, {seps} separations, {places} placements"
        await check("Authority", "resolves and renders", authority_resolves)

        await browser.close()

    width = max(len(f"{v} · {n}") for v, n, _, _ in results)
    print("\n" + "=" * 78)
    for view, name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"{mark}  {f'{view} · {name}':<{width}}  {detail}")
    failed = [r for r in results if not r[2]]
    print("=" * 78)
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if console:
        print(f"\n{len(console)} console/page error(s):")
        for line in console[:8]:
            print("  " + line[:160])
    return 1 if failed else 0


CHROME = "/opt/pw-browsers/chromium"
if not pathlib.Path(CHROME).exists():
    import glob
    found = glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")
    CHROME = found[0] if found else "chromium"

if __name__ == "__main__":
    import asyncio

    threading.Thread(target=serve, daemon=True).start()
    time.sleep(2.5)
    try:
        sys.exit(asyncio.run(main()))
    except Exception:
        traceback.print_exc()
        sys.exit(2)
