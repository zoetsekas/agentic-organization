"""Designer backend: persistence, RBAC, locking and concurrent editing.

The repository tests run against every backend, because a seam nobody exercises
is a seam that does not exist (ADR-0031).
"""
import pytest

from orgagents.designer import (
    DesignerError,
    DesignerService,
    FileSystemRepository,
    Layout,
    LockConflict,
    MemoryRepository,
    Member,
    PermissionDenied,
    Principal,
    SqlRepository,
    SystemRecord,
    SystemStatus,
    UserRole,
    VersionConflict,
    apply_resolutions,
    merge,
)
from orgagents.designer.models import CanvasNode, DesignerSettings, LockScope, NodeKind
from orgagents.store import Store

ANA = Principal("ana", "Ana")
BOB = Principal("bob", "Bob")
CAI = Principal("cai", "Cai")


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


# -- persistence, across every backend (ADR-0031) -------------------------


def test_create_read_update_delete(service, system):
    assert system.version == 1
    listed = service.list_systems(ANA)
    assert [s["id"] for s in listed] == [system.id]

    outcome = service.save_system(ANA, system.id, name="acme renamed",
                                  base_version=1)
    assert outcome.status == "saved" and outcome.record.version == 2

    opened = service.open_system(ANA, system.id)
    assert opened["record"]["name"] == "acme renamed"
    assert service.delete_system(ANA, system.id)
    assert service.list_systems(ANA) == []


def test_layout_is_persisted_but_kept_out_of_the_spec(service, system):
    """ADR-0034: node positions are presentation, never part of the design."""
    layout = Layout(nodes={"team_1": CanvasNode(id="team_1", kind=NodeKind.TEAM,
                                                x=120, y=80)})
    # A layout written the old way — nodes at the top — still reads: it
    # becomes the one diagram every design used to have.
    saved = service.save_system(ANA, system.id, layout=layout, base_version=1).record
    assert saved.layout.diagram.nodes["team_1"].x == 120
    assert saved.layout.diagram.name == "Organisation"
    assert "layout" not in saved.spec
    assert "nodes" not in str(saved.spec)


def test_revisions_are_captured_and_restorable(service, system):
    service.save_system(ANA, system.id, name="v2", base_version=1)
    service.save_system(ANA, system.id, name="v3", base_version=2)
    revisions = service.revisions(ANA, system.id)
    assert [r.version for r in revisions] == [3, 2, 1]
    restored = service.restore(ANA, system.id, 1)
    assert restored.version == 4     # restoring is itself a new version
    assert restored.spec == revisions[-1].spec


def test_multiple_systems_are_isolated(service):
    first = service.create_system(ANA, workspace_id=service.workspace_id, name="one")
    second = service.create_system(ANA, workspace_id=service.workspace_id, name="two")
    service.save_system(ANA, first.id, name="one edited", base_version=1)
    assert service.open_system(ANA, second.id)["record"]["name"] == "two"


def test_unknown_system_is_reported_clearly(service):
    with pytest.raises(DesignerError, match="no system"):
        service.open_system(ANA, "sys_missing")


# -- RBAC (ADR-0032) -------------------------------------------------------


def test_viewers_may_read_but_not_edit(service, system):
    assert service.open_system(CAI, system.id)["role"] == "viewer"
    with pytest.raises(PermissionDenied, match="does not grant"):
        service.save_system(CAI, system.id, name="nope", base_version=1)


def test_editors_may_edit_but_not_delete_or_manage(service, system):
    assert service.save_system(BOB, system.id, name="bob edit",
                               base_version=1).status == "saved"
    with pytest.raises(PermissionDenied):
        service.delete_system(BOB, system.id)
    with pytest.raises(PermissionDenied):
        service.add_member(BOB, service.workspace_id,
                           Member(user_id="dee", role=UserRole.EDITOR))


def test_non_members_see_nothing(service, system):
    stranger = Principal("mallory")
    assert service.list_systems(stranger) == []
    with pytest.raises(PermissionDenied, match="not a member"):
        service.open_system(stranger, system.id)


def test_a_workspace_keeps_at_least_one_owner(service):
    with pytest.raises(DesignerError, match="at least one owner"):
        service.remove_member(ANA, service.workspace_id, "ana")


def test_whoami_reports_role_and_permissions(service):
    me = service.whoami(BOB)
    assert me["workspaces"][0]["role"] == "editor"
    assert "system.edit" in me["workspaces"][0]["permissions"]
    assert "lock.break" not in me["workspaces"][0]["permissions"]


# -- locking (ADR-0033) ----------------------------------------------------


def test_component_locks_let_two_people_work_at_once(service, system):
    service.acquire_lock(ANA, system.id, target="agent:analyst")
    # Bob is free to take a different component.
    assert service.acquire_lock(BOB, system.id, target="agent:cfo")
    with pytest.raises(LockConflict, match="locked by Ana"):
        service.acquire_lock(BOB, system.id, target="agent:analyst")


def test_a_system_lock_blocks_saves_by_others(service, system):
    service.acquire_lock(ANA, system.id, scope="system", target="*")
    with pytest.raises(LockConflict):
        service.save_system(BOB, system.id, name="sneaky", base_version=1)
    # The holder is unaffected.
    assert service.save_system(ANA, system.id, name="mine",
                               base_version=1).status == "saved"


def test_locks_expire_so_a_closed_tab_does_not_freeze_a_design(service, system):
    service.settings.lock_ttl_seconds = 0
    service.locks.ttl_seconds = 0
    service.acquire_lock(ANA, system.id, scope="system", target="*")
    assert service.locks.active(system.id) == []
    assert service.save_system(BOB, system.id, name="unblocked",
                               base_version=1).status == "saved"


def test_release_and_break(service, system):
    service.acquire_lock(ANA, system.id, scope="system", target="*")
    assert not service.release_lock(BOB, system.id)        # not the holder
    with pytest.raises(PermissionDenied):
        service.break_lock(BOB, system.id)                 # editors may not break
    assert service.break_lock(ANA, system.id)              # admins and owners may
    assert service.locks.active(system.id) == []


def test_heartbeat_extends_only_your_own_lock(service, system):
    service.acquire_lock(ANA, system.id, scope="system", target="*")
    assert service.heartbeat(ANA, system.id) is not None
    assert service.heartbeat(BOB, system.id) is None


# -- concurrent editing (ADR-0033) ----------------------------------------


def _spec_with(system, **metadata):
    spec = dict(system.spec)
    spec["metadata"] = {**spec["metadata"], **metadata}
    return spec


def test_independent_edits_merge_without_a_conflict(service, system):
    service.save_system(ANA, system.id, spec=_spec_with(system, owner="Ana"),
                        base_version=1)
    outcome = service.save_system(BOB, system.id,
                                  spec=_spec_with(system, description="Bob's note"),
                                  base_version=1)
    assert outcome.status == "merged"
    assert outcome.record.spec["metadata"]["owner"] == "Ana"
    assert outcome.record.spec["metadata"]["description"] == "Bob's note"


def test_the_same_field_edited_twice_is_a_conflict_not_a_guess(service, system):
    service.save_system(ANA, system.id, spec=_spec_with(system, owner="Ana"),
                        base_version=1)
    outcome = service.save_system(BOB, system.id,
                                  spec=_spec_with(system, owner="Bob"),
                                  base_version=1)
    assert outcome.status == "conflict"
    assert [c.path for c in outcome.conflicts] == ["metadata.owner"]
    assert outcome.record.spec["metadata"]["owner"] == "Ana"   # nothing written


def test_conflicts_are_resolved_by_choosing(service, system):
    service.save_system(ANA, system.id, spec=_spec_with(system, owner="Ana"),
                        base_version=1)
    first = service.save_system(BOB, system.id, spec=_spec_with(system, owner="Bob"),
                                base_version=1)
    resolved = service.save_system(
        BOB, system.id, spec=_spec_with(system, owner="Bob"), base_version=1,
        resolutions={c.path: "ours" for c in first.conflicts},
    )
    assert resolved.status == "merged"
    assert resolved.record.spec["metadata"]["owner"] == "Bob"


def test_reject_strategy_refuses_instead_of_merging(service, system):
    service.save_system(ANA, system.id, spec=_spec_with(system, owner="Ana"),
                        base_version=1)
    outcome = service.save_system(BOB, system.id,
                                  spec=_spec_with(system, description="x"),
                                  base_version=1, strategy="reject")
    assert outcome.status == "stale"
    assert "reload or merge" in outcome.message


def test_stale_write_without_merge_never_loses_the_other_edit(service, system):
    service.save_system(ANA, system.id, name="ana's name", base_version=1)
    outcome = service.save_system(BOB, system.id, name="bob's name", base_version=1)
    # No spec supplied, so there is nothing to merge: the write is refused.
    assert outcome.status == "stale"
    assert service.open_system(ANA, system.id)["record"]["name"] == "ana's name"


def test_repository_enforces_the_version_directly(repository):
    record = SystemRecord(name="direct", workspace_id="w")
    saved = repository.save_system(record, expected_version=None, author="ana")
    with pytest.raises(VersionConflict):
        repository.save_system(saved, expected_version=99, author="bob")


# -- merge semantics -------------------------------------------------------


def test_merge_keeps_both_additions_to_a_keyed_list():
    base = {"agents": [{"id": "a", "name": "A"}]}
    ours = {"agents": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]}
    theirs = {"agents": [{"id": "a", "name": "A"}, {"id": "c", "name": "C"}]}
    merged, conflicts = merge(base, ours, theirs)
    assert [a["id"] for a in merged["agents"]] == ["a", "b", "c"]
    assert conflicts == []


def test_merge_detects_an_edit_delete_conflict():
    base = {"agents": [{"id": "a", "name": "A"}]}
    ours = {"agents": [{"id": "a", "name": "renamed"}]}
    theirs = {"agents": []}
    merged, conflicts = merge(base, ours, theirs)
    assert [c.kind for c in conflicts] == ["edit_delete"]
    assert merged["agents"][0]["name"] == "renamed"


def test_resolutions_can_take_the_other_side():
    base = {"metadata": {"owner": "x"}}
    ours = {"metadata": {"owner": "ana"}}
    theirs = {"metadata": {"owner": "bob"}}
    merged, conflicts = merge(base, ours, theirs)
    merged, remaining = apply_resolutions(merged, conflicts,
                                          {"metadata.owner": "theirs"})
    assert merged["metadata"]["owner"] == "bob" and remaining == []


def test_publishing_needs_the_permission(service, system):
    with pytest.raises(PermissionDenied):
        service.save_system(BOB, system.id, status=SystemStatus.PUBLISHED,
                            base_version=1)
    assert service.save_system(ANA, system.id, status=SystemStatus.PUBLISHED,
                               base_version=1).status == "saved"


def test_settings_are_admin_only_and_persist(service):
    with pytest.raises(PermissionDenied):
        service.update_settings(CAI, {"lock_ttl_seconds": 60})
    updated = service.update_settings(ANA, {"lock_ttl_seconds": 60,
                                            "default_merge_strategy": "reject"})
    assert updated.lock_ttl_seconds == 60
    assert service.locks.ttl_seconds == 60


def test_validation_runs_over_a_draft(service, system):
    report = service.open_system(ANA, system.id)["validation"]
    assert report["ok"] is False          # a starter spec has no leader yet
    assert any("leader" in e for e in report["errors"])
