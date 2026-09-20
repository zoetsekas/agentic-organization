"""Three-way merge of design documents, with conflicts surfaced (ADR-0033).

When two people edit the same system, the second save arrives based on a
version that is no longer current. Rejecting it is safe and infuriating; last
write wins is convenient and loses work. So we merge.

The merge is **structural**, not textual: specs are trees of dicts and lists,
and the lists that matter (agents, teams, roles, capabilities) are keyed by
`id`. That lets two people add different agents to the same team, or edit
different fields of the same agent, and both survive.

What cannot be resolved is reported, never guessed. A conflict names the path,
what the common ancestor said, and what each side wants — enough for a person
to choose.
"""
from __future__ import annotations

from typing import Any, Optional

from .models import Conflict

# Lists whose entries carry an identity we can merge on.
KEY_FIELDS = ("id", "name", "key", "user_id", "contact", "channel", "capability",
              "environment", "knowledge")


def _key_of(item: Any) -> Optional[str]:
    if not isinstance(item, dict):
        return None
    for field in KEY_FIELDS:
        value = item.get(field)
        if isinstance(value, str) and value:
            return f"{field}={value}"
    return None


def _keyed(items: list[Any]) -> Optional[dict[str, Any]]:
    """Index a list by entry identity, or None if the list is not keyed."""
    out: dict[str, Any] = {}
    for item in items:
        key = _key_of(item)
        if key is None or key in out:
            return None
        out[key] = item
    return out


def merge(
    base: Any, ours: Any, theirs: Any, *, path: str = ""
) -> tuple[Any, list[Conflict]]:
    """Merge `ours` and `theirs` against their common ancestor `base`.

    Returns the merged document and every unresolved conflict.
    """
    conflicts: list[Conflict] = []

    # Nobody changed it, or only one side did.
    if ours == theirs:
        return ours, conflicts
    if base == ours:
        return theirs, conflicts
    if base == theirs:
        return ours, conflicts

    if isinstance(ours, dict) and isinstance(theirs, dict):
        base_dict = base if isinstance(base, dict) else {}
        merged: dict[str, Any] = {}
        for key in dict.fromkeys([*base_dict, *ours, *theirs]):
            here = f"{path}.{key}" if path else key
            in_ours, in_theirs = key in ours, key in theirs
            in_base = key in base_dict
            if in_ours and in_theirs:
                value, sub = merge(base_dict.get(key), ours[key], theirs[key], path=here)
                merged[key] = value
                conflicts.extend(sub)
            elif in_ours and not in_theirs:
                # They deleted what we changed: a person must decide.
                if in_base and ours[key] != base_dict[key]:
                    conflicts.append(
                        Conflict(path=here, base=base_dict.get(key), ours=ours[key],
                                 theirs=None, kind="edit_delete")
                    )
                    merged[key] = ours[key]
                elif not in_base:
                    merged[key] = ours[key]      # we added it
            elif in_theirs and not in_ours:
                if in_base and theirs[key] != base_dict[key]:
                    conflicts.append(
                        Conflict(path=here, base=base_dict.get(key), ours=None,
                                 theirs=theirs[key], kind="delete_edit")
                    )
                elif not in_base:
                    merged[key] = theirs[key]    # they added it
        return merged, conflicts

    if isinstance(ours, list) and isinstance(theirs, list):
        base_list = base if isinstance(base, list) else []
        keyed_ours, keyed_theirs = _keyed(ours), _keyed(theirs)
        keyed_base = _keyed(base_list) if base_list else {}
        if keyed_ours is not None and keyed_theirs is not None and keyed_base is not None:
            merged_items: list[Any] = []
            seen: set[str] = set()
            # Keep our ordering, then append what only they have: the result is
            # stable for the person who is saving.
            for key in [*keyed_ours, *keyed_theirs]:
                if key in seen:
                    continue
                seen.add(key)
                here = f"{path}[{key}]"
                mine, yours = keyed_ours.get(key), keyed_theirs.get(key)
                ancestor = keyed_base.get(key)
                if mine is not None and yours is not None:
                    value, sub = merge(ancestor, mine, yours, path=here)
                    merged_items.append(value)
                    conflicts.extend(sub)
                elif mine is not None:
                    if ancestor is not None and mine != ancestor:
                        conflicts.append(
                            Conflict(path=here, base=ancestor, ours=mine, theirs=None,
                                     kind="edit_delete")
                        )
                        merged_items.append(mine)
                    elif ancestor is None:
                        merged_items.append(mine)
                else:
                    if ancestor is not None and yours != ancestor:
                        conflicts.append(
                            Conflict(path=here, base=ancestor, ours=None, theirs=yours,
                                     kind="delete_edit")
                        )
                    elif ancestor is None:
                        merged_items.append(yours)
            return merged_items, conflicts
        # An unkeyed list (plain strings, say) merges as a set union when both
        # sides only added, and conflicts otherwise.
        if all(not isinstance(i, (dict, list)) for i in [*ours, *theirs]):
            added_ours = [i for i in ours if i not in base_list]
            added_theirs = [i for i in theirs if i not in base_list]
            removed = [i for i in base_list if i not in ours or i not in theirs]
            union = [i for i in base_list if i not in removed]
            union += [i for i in added_ours + added_theirs if i not in union]
            return union, conflicts

    conflicts.append(
        Conflict(path=path or ".", base=base, ours=ours, theirs=theirs, kind="value")
    )
    return ours, conflicts


def apply_resolutions(
    merged: Any, conflicts: list[Conflict], resolutions: dict[str, Any]
) -> tuple[Any, list[Conflict]]:
    """Apply a person's choices, returning what is still unresolved.

    A resolution is `"ours"`, `"theirs"`, or an explicit value.
    """
    remaining: list[Conflict] = []
    for conflict in conflicts:
        if conflict.path not in resolutions:
            remaining.append(conflict)
            continue
        choice = resolutions[conflict.path]
        value = (
            conflict.ours if choice == "ours"
            else conflict.theirs if choice == "theirs"
            else choice
        )
        _set_path(merged, conflict.path, value)
    return merged, remaining


def _set_path(document: Any, path: str, value: Any) -> None:
    """Write a value at a merge path such as `organization.teams[id=finance].name`."""
    parts = _split_path(path)
    cursor = document
    for part in parts[:-1]:
        cursor = _descend(cursor, part)
        if cursor is None:
            return
    last = parts[-1]
    if isinstance(cursor, dict):
        if value is None:
            cursor.pop(last, None)
        else:
            cursor[last] = value
    elif isinstance(cursor, list) and last.startswith("[") and last.endswith("]"):
        key = last[1:-1]
        for index, item in enumerate(cursor):
            if _key_of(item) == key:
                if value is None:
                    cursor.pop(index)
                else:
                    cursor[index] = value
                return


def _split_path(path: str) -> list[str]:
    parts: list[str] = []
    buffer = ""
    for char in path:
        if char == ".":
            if buffer:
                parts.append(buffer)
                buffer = ""
        elif char == "[":
            if buffer:
                parts.append(buffer)
                buffer = ""
            buffer = "["
        elif char == "]":
            parts.append(buffer + "]")
            buffer = ""
        else:
            buffer += char
    if buffer:
        parts.append(buffer)
    return parts


def _descend(cursor: Any, part: str) -> Any:
    if part.startswith("[") and part.endswith("]"):
        key = part[1:-1]
        if isinstance(cursor, list):
            return next((i for i in cursor if _key_of(i) == key), None)
        return None
    if isinstance(cursor, dict):
        return cursor.get(part)
    return None


def summarize(conflicts: list[Conflict]) -> str:
    if not conflicts:
        return "no conflicts"
    lines = [f"{len(conflicts)} conflict(s):"]
    lines += [f"  - {c.describe()}" for c in conflicts[:10]]
    if len(conflicts) > 10:
        lines.append(f"  … and {len(conflicts) - 10} more")
    return "\n".join(lines)
