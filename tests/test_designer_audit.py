"""The designer audit log: what it records, where it survives, and what it
refuses to do (ADR-0043).

Like the rest of the designer suite, the persistence tests run against every
backend — an audit trail that only exists in memory is not an audit trail.
"""
import inspect

import pytest

from orgagents.designer import (
    AuditAction,
    AuditEvent,
    AuditLog,
    AuditOutcome,
    DesignerService,
    FileSystemRepository,
    MemoryRepository,
    Member,
    PermissionDenied,
    Principal,
    SqlRepository,
    SystemStatus,
    UserRole,
)
from orgagents.designer.models import DesignerSettings
from orgagents.store import Store

ANA = Principal("ana", "Ana")        # owner
BOB = Principal("bob", "Bob")        # editor
CAI = Principal("cai", "Cai")        # viewer


@pytest.fixture(params=["memory", "filesystem", "relational", "postgres"])
def repository(request, tmp_path):
    if request.param == "memory":
        return MemoryRepository()
    if request.param == "filesystem":
        return FileSystemRepository(tmp_path / "designer")
    if request.param == "postgres":
        # The relational backend over PostgreSQL (ADR-0113); skipped, with
        # the reason, where there is no Docker to start one.
        from orgagents.designer.repository import PostgresRepository
        repo = PostgresRepository.from_url(
            request.getfixturevalue("postgres_url"))
        request.addfinalizer(repo.engine.dispose)
        return repo
    return SqlRepository(Store(tmp_path / "designer.db"))


@pytest.fixture()
def service(repository):
    svc = DesignerService(repository, DesignerSettings(persistence="memory"))
    workspace = svc.create_workspace(ANA, "Acme Design")
    svc.add_member(ANA, workspace.id, Member(user_id="bob", display_name="Bob",
                                             role=UserRole.EDITOR))
    svc.add_member(ANA, workspace.id, Member(user_id="cai", display_name="Cai",
                                             role=UserRole.VIEWER))
    svc.workspace_id = workspace.id
    return svc


@pytest.fixture()
def system(service):
    return service.create_system(ANA, workspace_id=service.workspace_id, name="acme")


def _version(spec: dict, version: str) -> dict:
    """The same scalar field, edited two ways — the shape that truly conflicts."""
    return {**spec, "metadata": {**spec["metadata"], "version": version}}


def actions(events) -> list[str]:
    return [e.action.value for e in events]


# -- every audited action leaves an event, on every backend ----------------


def test_system_lifecycle_is_recorded(service, system):
    service.save_system(BOB, system.id, name="acme renamed", base_version=1)
    service.delete_system(ANA, system.id)

    events = service.audit_events(ANA)
    assert actions(events)[:3] == ["system.delete", "system.save", "system.create"]

    save = next(e for e in events if e.action is AuditAction.SYSTEM_SAVE)
    assert save.actor == "bob" and save.actor_name == "Bob"
    assert (save.version_before, save.version_after) == (1, 2)

    # The log outlives the design it describes.
    deleted = next(e for e in events if e.action is AuditAction.SYSTEM_DELETE)
    assert deleted.system_id == system.id and deleted.version_before == 2
    assert service.repository.get_system(system.id) is None


def test_lock_acquire_release_and_steal_are_recorded(service, system):
    service.acquire_lock(BOB, system.id, target="*", scope="system")
    assert service.release_lock(BOB, system.id)

    service.acquire_lock(BOB, system.id, target="*", scope="system")
    assert service.break_lock(ANA, system.id)

    events = service.audit_events(ANA, system_id=system.id)
    assert actions(events)[:4] == [
        "lock.break", "lock.acquire", "lock.release", "lock.acquire",
    ]
    stolen = events[0]
    assert stolen.actor == "ana" and stolen.lock_holder == "bob"


def test_an_expired_lock_is_recorded_even_though_nobody_asked(service, system):
    service.locks.ttl_seconds = -1          # already expired the moment it is taken
    service.acquire_lock(BOB, system.id, target="agent-1")
    service.locks.ttl_seconds = 900

    assert service.locks.active(system.id) == []
    expiry = service.audit_events(ANA, system_id=system.id,
                                  action="lock.expire")
    assert expiry and expiry[0].lock_holder == "bob"
    assert expiry[0].actor == ""            # expiry has no request behind it


def test_merge_and_conflict_are_recorded(service, system):
    spec = system.spec
    service.save_system(ANA, system.id, spec={**spec, "channels": [{"id": "a"}]},
                        base_version=1)

    # Bob is still on version 1 and edits a different part: a clean merge.
    merged = service.save_system(BOB, system.id,
                                 spec={**spec, "roles": [{"id": "r"}]},
                                 base_version=1, strategy="merge")
    assert merged.status == "merged"
    applied = service.audit_events(ANA, system_id=system.id,
                                   action="merge.applied")
    assert applied[0].merged is True
    assert (applied[0].version_before, applied[0].version_after) == (1, 3)

    # And now the same field from both sides: a conflict needing a decision.
    service.save_system(ANA, system.id, spec=_version(spec, "0.2.0"),
                        base_version=3)
    clash = service.save_system(BOB, system.id, spec=_version(spec, "0.3.0"),
                                base_version=3, strategy="merge")
    assert clash.status == "conflict"
    recorded = service.audit_events(ANA, system_id=system.id,
                                    action="merge.conflict")
    assert recorded[0].outcome is AuditOutcome.CONFLICT
    assert recorded[0].conflict_paths == [c.path for c in clash.conflicts]


def test_conflict_resolution_is_recorded_separately(service, system):
    spec = system.spec
    service.save_system(ANA, system.id, spec=_version(spec, "0.2.0"),
                        base_version=1)
    clash = service.save_system(BOB, system.id, spec=_version(spec, "0.3.0"),
                                base_version=1, strategy="merge")
    assert clash.status == "conflict"
    resolutions = {c.path: "ours" for c in clash.conflicts}
    resolved = service.save_system(BOB, system.id, spec=_version(spec, "0.3.0"),
                                   base_version=1, strategy="merge",
                                   resolutions=resolutions)
    assert resolved.status == "merged"
    event = service.audit_events(ANA, system_id=system.id,
                                 action="merge.resolved")[0]
    assert event.actor == "bob"
    assert event.detail["resolved"] == sorted(resolutions)


def test_settings_changes_are_recorded(service):
    service.update_settings(ANA, {"lock_ttl_seconds": 60})
    event = service.audit_events(ANA, action="designer.settings")[0]
    assert event.actor == "ana" and event.detail["changed"] == ["lock_ttl_seconds"]


def test_membership_changes_are_recorded(service):
    service.add_member(ANA, service.workspace_id,
                       Member(user_id="dee", role=UserRole.EDITOR))
    service.remove_member(ANA, service.workspace_id, "dee")
    added = service.audit_events(ANA, action="workspace.member.add")[0]
    removed = service.audit_events(ANA, action="workspace.member.remove")[0]
    assert added.detail == {"member": "dee", "role": "editor"}
    assert removed.detail == {"member": "dee"}


# -- denials, which is what the log is for --------------------------------


def test_a_refused_action_is_recorded_with_the_missing_permission(service, system):
    with pytest.raises(PermissionDenied):
        service.delete_system(CAI, system.id)          # viewer may not delete

    denial = service.audit_events(ANA, system_id=system.id)[0]
    assert denial.action is AuditAction.SYSTEM_DELETE
    assert denial.outcome is AuditOutcome.DENIED
    assert denial.actor == "cai" and denial.permission == "system.delete"
    assert "viewer" in denial.reason


def test_a_stranger_is_recorded_too(service, system):
    stranger = Principal("mal", "Mal")
    with pytest.raises(PermissionDenied):
        service.open_system(stranger, system.id)

    denial = service.audit_events(ANA, system_id=system.id)[0]
    assert denial.actor == "mal" and denial.outcome is AuditOutcome.DENIED
    assert denial.permission == "system.view"


def test_a_refused_publish_is_recorded(service, system):
    with pytest.raises(PermissionDenied):
        service.save_system(BOB, system.id, status=SystemStatus.PUBLISHED,
                            base_version=1)
    denial = service.audit_events(ANA, system_id=system.id)[0]
    assert denial.action is AuditAction.SYSTEM_PUBLISH
    assert denial.permission == "system.publish"


# -- who may read it ------------------------------------------------------


def test_only_admins_and_owners_may_read_the_log(service, system):
    assert service.audit_events(ANA)                       # owner
    for who in (BOB, CAI):
        with pytest.raises(PermissionDenied):
            service.audit_events(who)
        with pytest.raises(PermissionDenied):
            service.audit_events(who, system_id=system.id)


def test_reading_the_log_is_scoped_to_readable_workspaces(service, system):
    other = service.create_workspace(BOB, "Bob's own")
    theirs = service.create_system(BOB, workspace_id=other.id, name="bobs")

    seen = {e.system_id for e in service.audit_events(ANA)}
    assert system.id in seen and theirs.id not in seen


# -- queries --------------------------------------------------------------


def test_queries_filter_by_system_actor_and_time(service, system):
    second = service.create_system(ANA, workspace_id=service.workspace_id,
                                   name="other")
    service.save_system(BOB, system.id, name="renamed", base_version=1)

    by_system = service.audit_events(ANA, system_id=system.id)
    assert {e.system_id for e in by_system} == {system.id}
    assert second.id not in {e.system_id for e in by_system}

    by_actor = service.audit_events(ANA, actor="bob")
    assert by_actor and all(e.actor == "bob" for e in by_actor)

    everything = service.audit_events(ANA)
    newest, oldest = everything[0], everything[-1]
    assert service.audit_events(ANA, since=newest.timestamp)[-1].timestamp \
        >= newest.timestamp
    assert oldest not in service.audit_events(ANA, since=newest.timestamp)
    assert newest not in service.audit_events(ANA, until=newest.timestamp)

    assert len(service.audit_events(ANA, limit=2)) == 2


def test_ordering_is_newest_first_and_total(service, system):
    for i in range(5):
        service.save_system(ANA, system.id, name=f"n{i}", base_version=i + 1)
    events = service.audit_events(ANA)
    keys = [e.order_key for e in events]
    assert keys == sorted(keys, reverse=True)
    assert len(set(e.id for e in events)) == len(events)


# -- append-only ----------------------------------------------------------


def test_no_public_api_mutates_or_deletes_an_event(service):
    forbidden = ("update", "delete", "remove", "purge", "prune", "clear",
                 "truncate", "edit")
    surfaces = {
        "AuditLog": AuditLog,
        "DesignerService": DesignerService,
    }
    for name, obj in surfaces.items():
        offenders = [
            attr for attr in dir(obj)
            if not attr.startswith("_")
            and "audit" in attr.lower()
            and any(word in attr.lower() for word in forbidden)
        ]
        assert offenders == [], f"{name} exposes {offenders}"

    assert {m for m in dir(AuditLog) if not m.startswith("_")} == {
        "record", "query"
    }


def test_the_repository_offers_no_way_to_remove_an_event(repository):
    for attr in dir(repository):
        if attr.startswith("_"):
            continue
        assert not ("audit" in attr.lower() and "delete" in attr.lower())
    assert hasattr(repository, "append_audit")


def test_a_written_event_is_never_rewritten(service, system):
    log = service.audit
    first = log.record(AuditAction.SYSTEM_SAVE, ANA, system_id=system.id,
                       workspace_id=service.workspace_id, version_after=9)
    second = log.record(AuditAction.SYSTEM_SAVE, ANA, system_id=system.id,
                        workspace_id=service.workspace_id, version_after=10)
    assert first.id != second.id

    # Mutating the returned object cannot reach back into the store.
    first.reason = "tampered"
    stored = next(e for e in service.audit_events(ANA, system_id=system.id)
                  if e.id == first.id)
    assert stored.reason == ""
    assert stored.version_after == 9


def test_the_http_api_exposes_no_write_route(service):
    from orgagents.api import create_app

    app = create_app()
    audit_routes = [
        (r.path, sorted(r.methods)) for r in app.routes
        if "audit" in getattr(r, "path", "")
    ]
    assert audit_routes, "the audit log must be readable over HTTP"
    for path, methods in audit_routes:
        assert set(methods) <= {"GET", "HEAD"}, f"{path} accepts {methods}"


# -- a failure to audit must not take the operation down ------------------


def test_an_audit_failure_is_logged_but_does_not_break_the_operation(
    service, system, caplog
):
    class Broken:
        def append_audit(self, event):
            raise OSError("disk is full")

    service.audit = AuditLog(Broken())
    with caplog.at_level("ERROR"):
        outcome = service.save_system(ANA, system.id, name="still saved",
                                      base_version=1)
    assert outcome.status == "saved" and outcome.record.name == "still saved"
    assert "audit event was not persisted" in caplog.text


# -- the model itself -----------------------------------------------------


def test_an_event_round_trips_through_json():
    event = AuditEvent(action=AuditAction.LOCK_BREAK, actor="ana",
                       outcome=AuditOutcome.SUCCESS, system_id="sys_1",
                       lock_target="agent-1")
    again = AuditEvent.model_validate_json(event.model_dump_json())
    assert again == event
    assert again.action is AuditAction.LOCK_BREAK


def test_record_takes_the_signature_the_service_relies_on():
    # record() is the only write path; keep its shape honest.
    params = inspect.signature(AuditLog.record).parameters
    assert list(params) == ["self", "action", "actor", "fields"]
