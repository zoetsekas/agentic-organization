"""Refreshing catalog figures from a source instead of typing them (WS-026 M6).

A `FigureSource` answers one question: *what does this source say about this
entry's figures right now?* It may answer "nothing", and that answer is as
important as a number — a source that does not cover a model must leave the
model's row exactly as it was, because a half-covered import that blanked the
rest would be a silent downgrade of the catalog.

There is no network here, so the source that ships is file-backed. The HTTP
one takes its transport as an argument rather than importing a client, so it
is testable against a stub and carries no dependency this environment lacks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from .models import CatalogEntry, CatalogKind, ModelAttributes

# The attributes a source may set. Anything outside this set is editorial —
# class tags, summaries, entitlements — and stays the operator's to decide.
REFRESHABLE_FIELDS = (
    "context_tokens",
    "max_output_tokens",
    "cost_per_million_input",
    "cost_per_million_output",
    "regions",
    "knowledge_cutoff",
)


@dataclass
class FigureQuote:
    """What a source says about one entry, and when it said it."""

    figures: dict[str, Any] = field(default_factory=dict)
    observed_at: str = ""
    source_url: str = ""
    note: str = ""

    def clean(self) -> dict[str, Any]:
        """Only the refreshable fields, and only ones actually carrying a value."""
        return {k: v for k, v in self.figures.items()
                if k in REFRESHABLE_FIELDS and v not in (None, "", [], {})}


@runtime_checkable
class FigureSource(Protocol):
    """Somewhere authoritative figures come from."""

    name: str

    def quote(self, entry: CatalogEntry) -> Optional[FigureQuote]:
        """Figures for this entry, or None when the source does not cover it."""


def _key(entry: CatalogEntry) -> tuple[str, str]:
    attributes = ModelAttributes.model_validate(entry.attributes)
    return attributes.provider, attributes.model_id


class MappingFigureSource:
    """A source backed by an in-memory payload, keyed `provider/model_id`.

    The file and HTTP sources both reduce to this, so the matching rule lives
    in one place.
    """

    def __init__(self, name: str, payload: dict[str, Any], *,
                 observed_at: str = "", source_url: str = "") -> None:
        self.name = name
        self.observed_at = observed_at or str(payload.get("observed_at", ""))
        self.source_url = source_url or str(payload.get("source_url", ""))
        self._models: dict[str, dict[str, Any]] = {
            str(k): dict(v) for k, v in (payload.get("models") or {}).items()
        }

    def quote(self, entry: CatalogEntry) -> Optional[FigureQuote]:
        if entry.kind is not CatalogKind.MODEL:
            return None
        provider, model_id = _key(entry)
        if not model_id:
            return None
        for candidate in (f"{provider}/{model_id}", model_id):
            row = self._models.get(candidate)
            if row is not None:
                return FigureQuote(
                    figures=row,
                    observed_at=str(row.get("observed_at", self.observed_at)),
                    source_url=str(row.get("source_url", self.source_url)),
                )
        return None


class FileFigureSource(MappingFigureSource):
    """Figures an operator exported from a provider and committed to a file.

    JSON, because it is the format both a hand-maintained file and a scraped
    price page can be written to without another dependency.
    """

    def __init__(self, path: str | Path, *, name: str = "") -> None:
        p = Path(path)
        payload = json.loads(p.read_text(encoding="utf-8"))
        super().__init__(name or str(payload.get("source", p.name)), payload)
        self.path = p


class HttpFigureSource(MappingFigureSource):
    """A provider's published figures, over a transport supplied by the caller.

    The fetch callable is injected so nothing here imports an HTTP client: in
    production it is a one-line `requests`/`httpx` wrapper the operator passes,
    and in tests it is a stub. Constructing it performs the single fetch, so a
    refresh of 200 entries is one request, not 200.
    """

    def __init__(self, url: str, fetch: Callable[[str], dict[str, Any]], *,
                 name: str = "") -> None:
        payload = fetch(url)
        super().__init__(name or url, payload, source_url=url)
        self.url = url
