"""The bundled UIs: the designer at /ui and the command centre at /command."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles


class _RevalidatedStaticFiles(StaticFiles):
    """The UI bundle, served so a browser asks again before reusing it.

    The scripts are loaded without a version in their URL, so a heuristic cache
    kept serving the old canvas against a rebuilt API. `no-cache` still lets the
    browser keep a copy; it just revalidates it by ETag, which is a 304.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def mount(app: FastAPI, web_dir: Path) -> None:
    """Mount the bundles, when this installation ships them."""
    if web_dir.is_dir():
        app.mount("/ui", _RevalidatedStaticFiles(directory=str(web_dir), html=True),
                  name="ui")

        # The command centre is its own application, not a tab in the designer
        # (ADR-0051): a separate bundle at a separate path, over this API.
        if (web_dir / "command").is_dir():
            app.mount("/command",
                      _RevalidatedStaticFiles(directory=str(web_dir / "command"),
                                              html=True),
                      name="command")

        @app.get("/")
        def index() -> Any:
            return RedirectResponse("/ui/")
