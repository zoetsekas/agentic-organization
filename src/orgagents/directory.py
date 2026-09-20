"""People directory lookups, so pairings can be reconciled against reality.

`AgentSpec.humans` names individuals, and ADR-0026 makes exactly one of them
accountable for the agent. Individuals leave. Nothing in the spec notices, so a
departed employee stays listed as the accountable owner — or as the approver a
gated action routes to — until an incident finds it.

This module is the read side of that problem, and deliberately nothing more:

* `Directory` is a protocol, not a client. Anything that can answer "who is
  this contact?" satisfies it — a file, a test stub, or an adapter over LDAP,
  SCIM or Microsoft Graph injected from outside this package.
* Three answers are possible and all three are distinct: **active**,
  **departed**, and **not known to me**. Collapsing the last two would either
  invent departures for contractors the directory never held, or hide real
  ones; ADR-0047 keeps them apart all the way to the findings.
* The default is `NullDirectory`, which knows nothing and says so. A system
  with no directory configured must produce *no* findings at all, rather than
  reporting everybody as present (which would be a lie) or everybody as unknown
  (which would be noise).

Reconciliation (`reconcile`) walks a spec's pairings and reports what the
directory disputes. Turning that into severity-tagged findings is
`spec.validate`'s job, because severity is a governance decision.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, runtime_checkable

from .spec.model import HumanCounterpart, HumanRole, SystemSpec


class PersonStatus(str, Enum):
    """What a directory is able to say about a contact."""

    ACTIVE = "active"        # present and employed
    DEPARTED = "departed"    # the directory holds them and marks them gone
    UNKNOWN = "unknown"      # the directory has never heard of this contact


@dataclass(frozen=True)
class Person:
    """One directory record. Everything but `contact` and `status` is optional
    because directories differ in what they expose, and a partial answer is
    still worth having."""

    contact: str
    status: PersonStatus = PersonStatus.UNKNOWN
    display_name: str = ""
    groups: tuple[str, ...] = ()
    manager: Optional[str] = None
    # Free-form: where this came from, echoed into findings so a reviewer can
    # tell which system is making the claim.
    source: str = ""

    @property
    def is_active(self) -> bool:
        return self.status is PersonStatus.ACTIVE

    @property
    def is_departed(self) -> bool:
        return self.status is PersonStatus.DEPARTED


@runtime_checkable
class Directory(Protocol):
    """Anything that can resolve a contact to a `Person`.

    Implementations never raise for an unknown contact: they return a record
    with `PersonStatus.UNKNOWN`. A directory that cannot be reached at all is a
    different matter — see `DirectoryUnavailable`, which callers are expected
    to treat as "no opinion", never as "everyone departed".
    """

    name: str

    def lookup(self, contact: str) -> Person:
        """Resolve one contact. Never raises for a miss."""

    def knows_anyone(self) -> bool:
        """Whether this directory holds data at all.

        False means its answers carry no information, so "unknown to the
        directory" must not be reported. `NullDirectory` is the only built-in
        that answers False.
        """


class DirectoryUnavailable(RuntimeError):
    """The backing directory could not be consulted.

    Raised by adapters, and caught by `reconcile` when `strict=False` (the
    default): a directory outage degrades reconciliation to "no opinion" rather
    than failing a build (ADR-0047).
    """


class NullDirectory:
    """The default: knows nothing, and says so rather than guessing."""

    name = "null"

    def lookup(self, contact: str) -> Person:
        return Person(contact=contact, status=PersonStatus.UNKNOWN, source=self.name)

    def knows_anyone(self) -> bool:
        return False


def _normalize(contact: str) -> str:
    return contact.strip().lower()


def _person_from_mapping(contact: str, data: Mapping[str, Any], source: str) -> Person:
    """Read one record from a loosely-shaped mapping.

    Accepts the spellings the common directory schemas use — SCIM's `active`,
    Graph's `accountEnabled`/`displayName` — so an adapter does not have to
    reshape a payload before handing it over.
    """
    if "status" in data:
        status = PersonStatus(str(data["status"]).lower())
    else:
        flag = data.get("active", data.get("accountEnabled", data.get("enabled")))
        if flag is None:
            status = PersonStatus.UNKNOWN
        else:
            status = PersonStatus.ACTIVE if bool(flag) else PersonStatus.DEPARTED
    groups = tuple(str(g) for g in data.get("groups", ()) or ())
    return Person(
        contact=contact,
        status=status,
        display_name=str(data.get("display_name", data.get("displayName", "")) or ""),
        groups=groups,
        manager=(str(data["manager"]) if data.get("manager") else None),
        source=source,
    )


class StaticDirectory:
    """A directory held in memory, loaded from a dict or a JSON/YAML file.

    This is what local runs and tests use. It is authoritative about the people
    it holds: a contact it does not have is genuinely unknown, not departed.
    """

    def __init__(
        self,
        people: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]] | None = None,
        *,
        name: str = "static",
    ) -> None:
        self.name = name
        self._people: dict[str, Person] = {}
        if isinstance(people, Mapping):
            items = [(str(k), v) for k, v in people.items()]
        else:
            items = []
            for entry in people or ():
                contact = entry.get("contact") or entry.get("userName") or entry.get("mail")
                if not contact:
                    continue
                items.append((str(contact), entry))
        for contact, data in items:
            if isinstance(data, str):          # "alice@x": "departed"
                data = {"status": data}
            self._people[_normalize(contact)] = _person_from_mapping(
                contact, data, self.name
            )

    @classmethod
    def from_file(cls, path: str | Path, *, name: str = "") -> "StaticDirectory":
        path = Path(path)
        text = path.read_text()
        if path.suffix in {".yaml", ".yml"}:
            import yaml            # already a hard dependency of the spec loader

            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text or "{}")
        if isinstance(data, Mapping) and "people" in data:
            data = data["people"]
        return cls(data, name=name or f"file:{path.name}")

    def lookup(self, contact: str) -> Person:
        found = self._people.get(_normalize(contact))
        if found is not None:
            return found
        return Person(contact=contact, status=PersonStatus.UNKNOWN, source=self.name)

    def knows_anyone(self) -> bool:
        return bool(self._people)

    def __len__(self) -> int:
        return len(self._people)


class FetchDirectory:
    """An adapter over an injected fetch callable.

    There is no network client in this package on purpose: an LDAP, SCIM or
    Graph integration supplies `fetch`, a callable taking a contact and
    returning that provider's record (or None for a miss). The mapping of the
    payload to a `Person` is shared, the transport is the caller's, and tests
    drive it with a stub.

    `fetch` may raise: anything that is not a `DirectoryUnavailable` is
    translated into one, so a provider error reaches callers as "no opinion"
    rather than as an exception nobody expected.
    """

    def __init__(
        self,
        fetch: Callable[[str], Optional[Mapping[str, Any]]],
        *,
        name: str = "fetch",
        populated: bool = True,
        cache: bool = True,
    ) -> None:
        self._fetch = fetch
        self.name = name
        self._populated = populated
        self._cache: Optional[dict[str, Person]] = {} if cache else None

    def lookup(self, contact: str) -> Person:
        key = _normalize(contact)
        if self._cache is not None and key in self._cache:
            return self._cache[key]
        try:
            raw = self._fetch(contact)
        except DirectoryUnavailable:
            raise
        except Exception as exc:                      # provider-specific errors
            raise DirectoryUnavailable(
                f"directory '{self.name}' could not be consulted: {exc}"
            ) from exc
        if raw is None:
            person = Person(contact=contact, status=PersonStatus.UNKNOWN,
                            source=self.name)
        else:
            person = _person_from_mapping(contact, raw, self.name)
        if self._cache is not None:
            self._cache[key] = person
        return person

    def knows_anyone(self) -> bool:
        return self._populated


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PairingIssue:
    """One pairing the directory disputes.

    `status` is either DEPARTED ("the directory holds this person and says they
    are gone") or UNKNOWN ("the directory has never heard of them"). They are
    different problems with different remedies, so they are never merged.
    """

    where: str                  # agent id, or mission id
    kind: str                   # "agent" | "mission"
    contact: str
    name: str
    roles: tuple[str, ...]
    status: PersonStatus
    person: Person
    is_owner: bool = False

    @property
    def departed(self) -> bool:
        return self.status is PersonStatus.DEPARTED

    @property
    def unknown(self) -> bool:
        return self.status is PersonStatus.UNKNOWN


@dataclass
class ReconciliationReport:
    directory: str
    consulted: bool = True         # False when the directory had no opinion
    unavailable_reason: str = ""
    issues: list[PairingIssue] = field(default_factory=list)

    @property
    def departed(self) -> list[PairingIssue]:
        return [i for i in self.issues if i.departed]

    @property
    def unknown(self) -> list[PairingIssue]:
        return [i for i in self.issues if i.unknown]

    def __bool__(self) -> bool:
        return bool(self.issues)


def _pairings(spec: SystemSpec) -> list[tuple[str, str, HumanCounterpart]]:
    """Every human pairing in the spec, whatever role it carries."""
    out: list[tuple[str, str, HumanCounterpart]] = []
    for agent in spec.agents():
        for human in agent.humans:
            out.append((agent.id, "agent", human))
    for mission in getattr(spec, "missions", ()) or ():
        if mission.sponsor is not None:
            out.append((mission.id, "mission", mission.sponsor))
    return out


def reconcile(
    spec: SystemSpec,
    directory: Optional[Directory] = None,
    *,
    strict: bool = False,
) -> ReconciliationReport:
    """Check every pairing in `spec` against `directory`.

    A directory that knows nothing yields an empty report with
    `consulted=False`; so does an outage, unless `strict=True`, which re-raises
    `DirectoryUnavailable` for callers that would rather know.
    """
    directory = directory or NullDirectory()
    report = ReconciliationReport(directory=getattr(directory, "name", "directory"))
    if not directory.knows_anyone():
        report.consulted = False
        return report

    for where, kind, human in _pairings(spec):
        try:
            person = directory.lookup(human.contact)
        except DirectoryUnavailable as exc:
            if strict:
                raise
            # An outage is not evidence of departure: drop every finding rather
            # than report a half-checked spec as clean-but-for-these.
            return ReconciliationReport(
                directory=report.directory, consulted=False,
                unavailable_reason=str(exc),
            )
        if person.is_active:
            continue
        report.issues.append(
            PairingIssue(
                where=where,
                kind=kind,
                contact=human.contact,
                name=human.name,
                roles=tuple(r.value for r in human.roles),
                status=person.status,
                person=person,
                is_owner=HumanRole.OWNER in human.roles or kind == "mission",
            )
        )
    return report


__all__ = [
    "Directory",
    "DirectoryUnavailable",
    "FetchDirectory",
    "NullDirectory",
    "PairingIssue",
    "Person",
    "PersonStatus",
    "ReconciliationReport",
    "StaticDirectory",
    "reconcile",
]
