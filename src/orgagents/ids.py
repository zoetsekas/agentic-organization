"""Stable, human-readable identifier helpers."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now().isoformat()


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "item"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"
