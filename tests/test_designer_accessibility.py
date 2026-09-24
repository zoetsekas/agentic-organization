"""The designer is usable without a mouse, says what changed, and asks
questions in one accessible dialog (ADR-0117).

Two layers. The first reads the bundle as text, like the rest of the designer
suite: it holds the structural promises — no native alert/confirm/prompt, a
roving tabindex and a key handler on every node, pointer rather than mouse
events, the ARIA roles on the tabs and the live regions, grouped navigation,
the incremental renderer not quietly going back to rebuilding everything.

The second drives a real browser (Playwright, marked `e2e`) through keyboard
navigation, a delete undone from its toast, and a dialog refusing a bad value.
It is skipped where Playwright or its browser is not installed, which is most
places this suite runs; the text layer is what always runs.
"""
import re
import socket
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "web"


@pytest.fixture(scope="module")
def app_js() -> str:
    return (BUNDLE / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def canvas_js() -> str:
    return (BUNDLE / "canvas.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ui_js() -> str:
    return (BUNDLE / "ui.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (BUNDLE / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css() -> str:
    return (BUNDLE / "styles.css").read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    """The body of a top-level `function name(`, up to the next top-level one."""
    start = re.search(rf"^(async )?function {name}\(", source, re.M)
    assert start, f"{name} is gone"
    end = re.search(r"^(async )?function \w+\(|^const \w+ =", source[start.end():], re.M)
    return source[start.start():start.end() + (end.start() if end else len(source))]


# -- dialogs -----------------------------------------------------------------

NATIVE = re.compile(r"(?<![\w.])(?:window\.)?(alert|confirm|prompt)\(")


@pytest.mark.parametrize("name", ["app.js", "canvas.js", "settings.js"])
def test_no_native_alert_confirm_or_prompt(name):
    """They cannot validate, cannot be styled, block the page and are read as
    bare text. Every question goes through ui.js's one <dialog>."""
    source = (BUNDLE / name).read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    assert not NATIVE.findall(code), f"{name} still calls a native dialog"


def test_the_dialog_traps_focus_validates_inline_and_returns_focus(ui_js):
    assert "showModal()" in ui_js
    assert 'e.key !== "Tab"' in ui_js                      # the trap
    assert 'setAttribute("aria-invalid", "true")' in ui_js  # per field
    assert "aria-describedby" in ui_js
    assert "must be a whole number" in ui_js
    assert "opener.focus" in ui_js                          # focus goes back


def test_the_rating_is_a_whole_number_from_one_to_five(app_js):
    rate = _function(app_js, "rateEntry")
    assert 'type: "integer", min: 1, max: 5' in rate


def test_the_bundle_loads_ui_before_the_views(index_html):
    assert index_html.index('src="ui.js"') < index_html.index('src="app.js"')
    assert index_html.index('src="app.js"') < index_html.index('src="canvas.js"')


# -- the canvas and the keyboard ----------------------------------------------

def test_the_canvas_is_a_named_application_region(index_html):
    tag = re.search(r'<div id="canvas"[^>]*>', index_html).group(0)
    assert 'role="application"' in tag
    assert 'aria-label="Design canvas"' in tag
    assert 'aria-describedby="canvas-help"' in tag
    assert 'id="canvas-help"' in index_html


def test_every_node_is_focusable_and_answers_the_keys(canvas_js):
    node = _function(canvas_js, "renderNode")
    assert 'tabindex: "-1"' in node and '"aria-label": nodeLabel' in node
    assert "handleNodeKey(e, node, box, readOnly)" in node
    keys = _function(canvas_js, "handleNodeKey")
    for key in ('"Enter"', '"Delete"', '"F2"', '"Escape"', '"ContextMenu"',
                'key.toLowerCase() === "l"', "e.shiftKey"):
        assert key in keys, f"{key} is not handled on a node"
    assert "ArrowLeft" in canvas_js and "nearestBox" in keys
    # A roving tabindex: exactly one node is in the Tab order.
    assert "box.tabIndex = box === pick ? 0 : -1" in canvas_js


def test_pointer_events_not_mouse_events(canvas_js, css):
    for old in ('"mousedown"', '"mousemove"', '"mouseup"'):
        assert old not in canvas_js, f"{old} is mouse-only; use pointer events"
    assert '"pointerdown"' in canvas_js and '"pointercancel"' in canvas_js
    assert "touch-action: none" in css


def test_the_page_shortcuts_stay_out_of_modals_and_other_views(canvas_js):
    key = _function(canvas_js, "handleCanvasKey")
    assert "ui?.modalOpen()" in key
    assert 'view !== "canvas"' in key


def test_selection_is_announced(canvas_js, index_html):
    assert 'id="sr-live"' in index_html and 'aria-live="polite"' in index_html
    assert "ui.announce(" in _function(canvas_js, "handleNodeKey")


# -- incremental rendering -----------------------------------------------------

def test_selecting_does_not_redraw_the_canvas(canvas_js):
    select = _function(canvas_js, "selectNode")
    assert "renderCanvas" not in select and "syncSelection()" in select


def test_nodes_are_kept_by_id_and_panels_rebuilt_only_on_change(canvas_js):
    render = re.sub(r"/\*.*?\*/", "", _function(canvas_js, "renderCanvas"), flags=re.S)
    assert "nodeEls.get(node.id)" in render
    assert "replaceChildren" not in render           # elements are kept, not swapped
    for panel in ("renderExplorer", "renderOutline", "renderDiagramBar"):
        assert panel not in render, f"{panel} runs on every canvas redraw again"
    assert "key === canvas.panelsKey" in _function(canvas_js, "renderPanels")


def test_a_drag_redraws_only_the_edges_it_moves(canvas_js):
    drag = _function(canvas_js, "startDrag")
    assert "updateEdgesFor(node.id)" in drag
    assert "renderEdges()" not in drag


# -- undo toast ----------------------------------------------------------------

def test_a_delete_is_undone_from_a_toast_not_asked_about(canvas_js):
    delete = _function(canvas_js, "deleteNode")
    assert "offerUndo(" in delete
    assert "confirmDialog" not in delete
    offer = _function(canvas_js, "offerUndo")
    assert 'action: "Undo"' in offer
    # Undoes that change only, not whatever happens to be on top by then.
    assert "top !== entry" in offer


# -- navigation, tabs, status --------------------------------------------------

def test_navigation_is_grouped_and_marks_the_current_view(index_html, app_js):
    nav = index_html[index_html.index('<nav class="tabs"'):index_html.index("</nav>")]
    groups = re.findall(r'class="nav-group-label"[^>]*>([^<]+)<', nav)
    assert groups == ["Design", "Library", "Run", "Help"]
    order = re.findall(r'data-view="([\w-]+)"', nav)
    assert order == ["org", "canvas", "catalog", "marketplace", "workspace",
                     "sessions", "ops", "user-guide"]
    assert 'setAttribute("aria-current", "page")' in app_js
    assert 'id="context-note"' in index_html and "RUNTIME_VIEWS" in app_js


def test_every_tab_set_uses_the_one_tabs_pattern(ui_js, canvas_js):
    settings = (BUNDLE / "settings.js").read_text(encoding="utf-8")
    for needle in ('"role", "tablist"', '"aria-selected"', '"aria-controls"',
                   '"tabpanel"', "ArrowRight", "Home", "End"):
        assert needle in ui_js
    assert 'ui.wireTabs($("#side-tabs")' in canvas_js
    assert 'ui.wireTabs($("#left-tabs")' in canvas_js
    assert 'label: "Diagrams"' in canvas_js
    assert "wireTabs(dialog?.querySelector(\".settings-tabs\")" in settings


def test_status_and_errors_are_live_regions(index_html):
    assert re.search(r'id="status"[^>]*role="status"[^>]*aria-live="polite"', index_html)
    assert re.search(r'id="conflict-bar"[^>]*role="alert"', index_html)
    assert re.search(r'id="offline"[^>]*role="alert"', index_html)


def test_icon_only_buttons_hide_their_glyph(index_html):
    for match in re.finditer(r'<button [^>]*class="icon-btn[^"]*"[^>]*>(.*?)</button>',
                             index_html, re.S):
        tag, inner = match.group(0), match.group(1)
        assert "aria-label" in tag, tag
        assert 'aria-hidden="true"' in inner, f"the glyph is read aloud: {tag}"


def test_focus_is_never_removed_without_a_replacement(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for match in re.finditer(r"([^{}]+)\{[^}]*outline:\s*none", css):
        selector = match.group(1).strip()
        assert selector.endswith(":focus"), f"{selector} hides focus"
        assert selector.replace(":focus", ":focus-visible") in css, (
            f"{selector} removes the ring and nothing draws one back")


# -- empty and unreachable states ---------------------------------------------

def test_empty_views_say_what_to_do_next(app_js, canvas_js):
    for loader in ("loadSessions", "loadOps", "loadCatalogView", "loadMarketplace"):
        assert "ui.emptyState(" in _function(app_js, loader), f"{loader} has no empty state"
    assert "ui.emptyState(" in _function(canvas_js, "renderValidation")


def test_an_unreachable_server_is_said_not_left_connecting(app_js, index_html):
    assert "Can't reach the designer's server" in index_html
    assert "showOffline(" in app_js and "isUnreachable(err)" in app_js


def test_the_shared_pieces_are_served(tmp_path):
    from fastapi.testclient import TestClient

    from orgagents.api import create_app

    client = TestClient(create_app(str(tmp_path / "ui.db")))
    assert client.get("/ui/ui.js").status_code == 200


# -- in a browser ---------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_designer(tmp_path_factory):
    uvicorn = pytest.importorskip("uvicorn")
    httpx = pytest.importorskip("httpx")
    from orgagents.api import create_app

    port = _free_port()
    app = create_app(str(tmp_path_factory.mktemp("e2e") / "e2e.db"))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(f"{base}/ui/", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    httpx.post(f"{base}/api/designer/examples/load", json={"example": "ayc"},
               headers={"X-User": "ana", "X-User-Name": "ana"}, timeout=30
               ).raise_for_status()
    yield base
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def page(live_designer):
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:              # no browser downloaded here
            pytest.skip(f"no Playwright browser: {exc}")
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(f"{live_designer}/ui/#/canvas")
        page.wait_for_function("document.querySelector('#status').textContent === 'ready'")
        page.wait_for_selector("#canvas-nodes .node")
        yield page
        browser.close()


@pytest.mark.e2e
def test_the_canvas_works_from_the_keyboard(page):
    page.focus('#canvas-nodes .node[tabindex="0"]')
    first = page.evaluate("document.activeElement.dataset.id")
    page.keyboard.press("ArrowDown")
    moved = page.evaluate("document.activeElement.dataset.id")
    assert moved and moved != first, "an arrow did not move focus to another box"
    page.keyboard.press("Enter")
    assert page.evaluate("canvas.selected.id") == moved
    assert page.evaluate("document.querySelector('#canvas-nodes .node.selected').dataset.id") == moved

    # One the model lets go of: some agents are still named by a workflow
    # step, and removing those is refused in a dialog instead.
    moved = "marketing_agent"
    page.focus(f'#canvas-nodes .node[data-id="{moved}"]')
    page.keyboard.press("Delete")
    page.wait_for_selector(".toast .toast-action")
    assert not page.query_selector(f'#canvas-nodes .node[data-id="{moved}"]')
    # Focus went to a neighbour rather than being dropped on <body>.
    assert page.evaluate("!!document.activeElement.closest('.node')")
    page.click(".toast .toast-action")
    page.wait_for_selector(f'#canvas-nodes .node[data-id="{moved}"]')


@pytest.mark.e2e
def test_a_dialog_refuses_a_bad_value_under_the_field(page):
    page.evaluate("window.__answer = ui.promptDialog('Rate', "
                  "{ label: 'Stars', type: 'integer', min: 1, max: 5 })")
    page.fill(".ui-dialog input", "3.5")
    page.keyboard.press("Enter")
    assert "whole number" in page.inner_text(".ui-dialog .field-error")
    assert page.get_attribute(".ui-dialog input", "aria-invalid") == "true"
    page.fill(".ui-dialog input", "4")
    page.keyboard.press("Enter")
    assert page.evaluate("window.__answer") == 4
    # Nothing modal is left behind, and Delete reaches the canvas again.
    assert page.evaluate("ui.modalOpen()") is False
