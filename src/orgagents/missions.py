"""Mission windows at runtime (ADR-0039 v1.1.0).

A mission is deliberately short-lived, and the lateral reach it opens between
its members is only good for as long as the mission runs. The compiler records
the window; this module is what decides, on a given day, whether a window is
still open — and sweeps the ones that have closed.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Optional

OPEN_STATUSES = ("proposed", "active")
CLOSED_STATUSES = ("completed", "disbanded")


def _today(on: Optional[date] = None) -> date:
    return on or date.today()


def _parse(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def window_is_open(
    status: str,
    starts_on: Any = None,
    ends_on: Any = None,
    on: Optional[date] = None,
) -> bool:
    """True when a mission in this state still confers anything today.

    A missing end date does not mean "forever": the spec validator refuses a
    mission without one, so an absent date here is a partially-built record and
    the window is treated as open only while the status says so.
    """
    if status in CLOSED_STATUSES:
        return False
    if status not in OPEN_STATUSES:
        return False
    today = _today(on)
    start = _parse(starts_on)
    if start and today < start:
        return False
    end = _parse(ends_on)
    if end and today > end:
        return False
    return True


def grant_is_open(grant: dict[str, Any], on: Optional[date] = None) -> bool:
    """`window_is_open` for a compiled `MissionGrantIR`, as a dict."""
    return window_is_open(
        str(grant.get("status", "proposed")),
        grant.get("starts_on"),
        grant.get("ends_on"),
        on,
    )


def open_peers(
    grants: Iterable[dict[str, Any]], on: Optional[date] = None
) -> list[str]:
    """Everyone still reachable through a mission on the given day."""
    peers: list[str] = []
    for grant in grants:
        if grant_is_open(grant, on):
            peers += list(grant.get("peers", []))
    return sorted(dict.fromkeys(peers))


def sweep(spec: Any, on: Optional[date] = None) -> list[str]:
    """Mark every mission past its end date completed; return the ids swept.

    Mutates the spec in place. This is the maintenance half of expiry: the
    delegation gate refuses an expired window whether or not anybody sweeps,
    so a missed sweep is untidy rather than unsafe.
    """
    from .spec.model import MissionStatus

    today = _today(on)
    swept: list[str] = []
    for mission in getattr(spec, "missions", []):
        if mission.status.value not in OPEN_STATUSES:
            continue
        end = _parse(mission.ends_on)
        if end and today > end:
            mission.status = MissionStatus.COMPLETED
            swept.append(mission.id)
    return swept
