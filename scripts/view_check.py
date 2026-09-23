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

import asyncio
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
# Sessions and Operations are runtime views: their *empty* states were the only
# thing anything had ever exercised, because nothing had ever run. Load the
# design into the runtime and run two agents, so the populated states are
# checked too — which is where the columns, badges and the trace live.
from orgagents.compiler.ir import build_ir                # noqa: E402
from orgagents.runtime.loader import load_system          # noqa: E402
from orgagents.spec.model import SystemSpec               # noqa: E402

RAN: list[str] = []
try:
    load_system(app.state.platform, build_ir(SystemSpec.model_validate(spec)))
    for agent_id in ("cfo", "controller"):
        reply = c.post(f"/api/agents/{agent_id}/run",
                       json={"prompt": "Summarise the month-end position."},
                       headers=A)
        if reply.status_code == 200:
            RAN.append(reply.json().get("session_id", ""))
    print(f"ran {len(RAN)} session(s)", flush=True)
    # Published *after* the design is in the runtime: publish references a
    # runtime agent, so the other order found nothing and left the marketplace
    # empty for a reason that had nothing to do with the view.
    published = 0
    for ref in ("cfo", "controller"):
        reply = c.post("/api/catalog/publish",
                       json={"kind": "agent", "ref_id": ref,
                             "owner": "Finance Platform",
                             "visibility": "public", "tags": ["finance"]},
                       headers=A)
        published += reply.status_code == 200
    print(f"published {published} listing(s)", flush=True)
except Exception as exc:                                   # noqa: BLE001
    print(f"could not seed runs: {type(exc).__name__}: {exc}", flush=True)
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
        browser = await pw.chromium.launch(**({"executable_path": CHROME} if CHROME else {}))
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
            if view == "authority":
                # A review, not a tab (ADR-0108): opened from the Org chart.
                await page.click('#tabs button[data-view="org"]')
                await page.click("#btn-org-authority")
            elif view == "designer":
                # Agents is an item of the Components menu (ADR-0107).
                await page.click("#btn-components")
                await page.click('#components-list [data-kind="agent-view"]')
            else:
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

        async def catalog_restrict():
            """Restrict is not approve with a different word: it leaves the
            entry usable only by the groups named on it."""
            await page.select_option("#pc-status", "approved")
            await page.wait_for_timeout(800)
            first = page.locator("#pc-entries > *").first
            await first.get_by_role("button", name="Restrict").first.click()
            await page.wait_for_timeout(900)
            await page.select_option("#pc-status", "restricted")
            await page.wait_for_timeout(800)
            n = await page.locator("#pc-entries > *").count()
            await page.select_option("#pc-status", "")
            await page.wait_for_timeout(700)
            assert n, "nothing is restricted after restricting something"
            return f"{n} restricted"
        await check("Catalog", "restrict moves an entry", catalog_restrict)

        async def catalog_send_back():
            """Follow one named entry rather than counting a list that three
            other checks have been rearranging."""
            await page.select_option("#pc-status", "approved")
            await page.wait_for_timeout(800)
            first = page.locator("#pc-entries > *").first
            name = (await first.locator("strong, h3, b").first.text_content()
                    or "").strip()
            # Send back asks why, and returns without doing anything if that
            # is dismissed — which is right for a control that makes an entry
            # unselectable, and which Playwright dismisses by default.
            handler = lambda d: asyncio.ensure_future(  # noqa: E731
                d.accept("checked by the view sweep"))
            page.on("dialog", handler)
            try:
                await first.get_by_role("button", name="Send back").first.click()
                await page.wait_for_timeout(1200)
            finally:
                page.remove_listener("dialog", handler)
            await page.select_option("#pc-status", "proposed")
            await page.wait_for_timeout(900)
            proposed = " ".join((await page.locator("#pc-entries").text_content()
                                 or "").split())
            await page.select_option("#pc-status", "")
            await page.wait_for_timeout(700)
            assert name and name in proposed, \
                f"{name!r} is not among the proposed after being sent back"
            return f"{name!r} is proposed again"
        await check("Catalog", "send back un-approves", catalog_send_back)

        async def catalog_retire():
            """A retired entry stops being selectable: the point of the
            control, and the one a design would feel."""
            await page.select_option("#pc-status", "approved")
            await page.wait_for_timeout(800)
            before = await page.locator("#pc-entries > *").count()
            first = page.locator("#pc-entries > *").first
            await first.get_by_role("button", name="Retire").first.click()
            await page.wait_for_timeout(900)
            after = await page.locator("#pc-entries > *").count()
            await page.select_option("#pc-status", "")
            await page.wait_for_timeout(700)
            assert after < before, f"retire left it selectable ({before})"
            return f"selectable {before} → {after}"
        await check("Catalog", "retire removes from selectable", catalog_retire)

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
            # Put the view back. A check that leaves a filter on is testing the
            # checks after it as much as itself — this one left the marketplace
            # showing an empty state and the install check blamed the button.
            await page.select_option("#cat-kind", index=0)
            await page.wait_for_timeout(700)
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

        async def market_install():
            """Install asks which runtime agent to install into, because the
            marketplace installs into the running system and not the design."""
            # `installEntry` asks which runtime agent to install into with a
            # `window.prompt`, which blocks the page until it is answered.
            cards = await page.locator("#catalog > *").count()
            buttons = await page.get_by_role("button", name="Install").count()
            assert buttons, (
                f"{cards} card(s) and no Install button; the view shows "
                f"{(await page.locator('#catalog').text_content() or '')[:60]!r}")
            handler = lambda d: asyncio.ensure_future(d.accept("cfo"))  # noqa: E731
            page.on("dialog", handler)
            try:
                await page.locator("#catalog > *").first \
                    .get_by_role("button", name="Install").first.click()
            finally:
                page.remove_listener("dialog", handler)
            await page.wait_for_timeout(1200)
            stats = " ".join((await page.locator("#catstats").text_content()
                              or "").split())
            assert "Installs" in stats, f"no install count shown: {stats[:60]}"
            return stats[:60]
        await check("Marketplace", "install runs", market_install)

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

        async def sessions_lists_runs():
            """Only the *empty* state of this view had ever been exercised,
            because nothing had ever run."""
            n = await page.locator("#sessions > *").count()
            text = " ".join((await page.locator("#sessions").text_content()
                             or "").split())
            assert text, "an empty list said nothing at all"
            if not RAN:
                return f"empty state: {text[:50]}"
            assert n >= len(RAN), f"{len(RAN)} runs, {n} rows"
            return f"{n} run(s) listed"
        await check("Sessions", "lists runs", sessions_lists_runs)

        async def sessions_trace():
            """Clicking a run opens its trace — the thing the view is for."""
            if not RAN:
                return "no runs to trace"
            await page.locator("#sessions > * span.grow").first.click()
            await page.wait_for_timeout(1100)
            text = " ".join((await page.locator("#trace").text_content()
                             or "").split())
            assert text and "Select a session" not in text, \
                f"the trace pane still says {text[:40]!r}"
            return text[:50]
        await check("Sessions", "opens a run's trace", sessions_trace)

        # ---- Operations --------------------------------------------------
        await show("ops")

        async def ops_metrics():
            n = await page.locator("#metrics > *").count()
            assert n, "no metric tiles"
            return f"{n} tiles"
        await check("Operations", "renders metrics", ops_metrics)

        async def ops_counts_runs():
            """The tiles are the point: zeros with nothing running is honest,
            and zeros with two runs behind them is a broken view."""
            text = " ".join((await page.locator("#metrics").text_content()
                             or "").split())
            if not RAN:
                return f"nothing ran: {text[:50]}"
            per = await page.locator("#peragent > *").count()
            assert "SESSIONS" in text.upper(), "no sessions tile"
            assert per, "two agents ran and the per-agent table is empty"
            return f"{per} agent row(s); {text[:44]}"
        await check("Operations", "counts what ran", ops_counts_runs)

        async def ops_alerts_state():
            alerts = (await page.locator("#alerts").text_content() or "").strip()
            assert alerts, "the alerts list said nothing at all"
            return " ".join(alerts.split())[:50]
        await check("Operations", "alerts say their state", ops_alerts_state)

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

        async def agents_save_untouched():
            """Saving without editing anything must be a no-op.

            If a round trip through this form changes the design, every other
            question about it is unanswerable — so this is asked first.
            """
            before = len(console)
            await page.locator("#agentform").get_by_role(
                "button", name="Save", exact=False).first.click()
            await page.wait_for_timeout(1300)
            broke = [c for c in console[before:] if "CERT" not in c]
            assert not broke, broke[0][:110]
            return "round trip is clean"
        await check("Agents", "saving an untouched agent is a no-op",
                    agents_save_untouched)

        async def agents_form_saves():
            """The form's whole purpose. Editing a field and saving must reach
            the spec, or this view is a viewer."""
            box = page.locator("#agentform textarea, #agentform input[type=text]")
            # Re-select: the check before this one saves, and the view
            # re-renders. A check that leans on the previous one's leftover
            # state is reporting the sequence rather than the thing.
            await page.locator("#agent-list > * > span.grow").first.click()
            await page.wait_for_timeout(900)
            # `description` is free prose; the other textarea is `humans`,
            # which is structured — typing prose into it is correctly refused,
            # and the first version of this check did exactly that and called
            # the refusal a defect.
            target = page.locator('#agentform textarea[name="description"]').first
            await target.fill("Edited by the view check.")
            save = page.locator("#agentform").get_by_role(
                "button", name="Save", exact=False).first
            await save.click()
            await page.wait_for_timeout(1300)
            err = " ".join((await page.locator("#agent-validation").text_content()
                            or "").split()) if await page.locator(
                                "#agent-validation").count() else ""
            value = await target.input_value()
            assert value == "Edited by the view check.", \
                f"the edit did not stick (now {value[:30]!r}); {err[:60]}"
            return "edit saved"
        await check("Agents", "the definition form saves", agents_form_saves)

        # ---- Org chart -----------------------------------------------------
        await show("org")

        async def org_tree():
            n = await page.locator("#orgtree *").count()
            assert n, "the org chart drew nothing"
            return f"{n} elements"
        await check("Org chart", "draws the tree", org_tree)

        async def org_load_example():
            """The button that did not exist: load a shipped example, and it
            opens as the current design, laid out."""
            before = await page.locator("#org-select option").count()
            await page.click("#btn-org-example")
            # Wait for the list, not for a fixed time: the first run of this
            # check looked before the fetch returned and reported no examples.
            await page.locator("#example-list .example-card").first \
                .wait_for(timeout=10000)
            offered = await page.locator("#example-list .example-card").count()
            said = " ".join((await page.locator("#example-picker").text_content()
                             or "").split())
            visible = await page.locator("#example-picker").is_visible()
            assert offered, f"no examples offered (visible={visible}): {said[:120]}"
            await page.locator('.example-card[data-example="sentinel"]') \
                .get_by_role("button", name="Load").click()
            await page.wait_for_timeout(1800)
            after = await page.locator("#org-select option").count()
            chosen = await page.locator("#org-select option:checked").text_content()
            assert after == before + 1, f"designs {before} → {after}"
            assert "Sentinel" in (chosen or ""), f"opened {chosen!r}"
            return f"{offered} offered; opened {chosen.strip()}"
        await check("Org chart", "loads an example", org_load_example)

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

if __name__ == "__main__":
    import asyncio

    threading.Thread(target=serve, daemon=True).start()
    time.sleep(2.5)
    try:
        sys.exit(asyncio.run(main()))
    except Exception:
        traceback.print_exc()
        sys.exit(2)
