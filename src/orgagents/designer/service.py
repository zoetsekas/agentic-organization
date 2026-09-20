"""The designer service: the API's whole surface, in one testable object.

Everything the frontend can do goes through here — list and open systems, save
with concurrency control, lock and unlock, resolve conflicts, browse and
restore revisions, manage members and settings. The UI holds no rules of its
own, which is what makes the frontend genuinely replaceable (ADR-0031).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..spec.loader import load_spec_text
from ..spec.validate import validate_spec
from .audit import AuditAction, AuditEvent, AuditLog, AuditOutcome
from .locks import LockConflict, LockManager
from .merge import apply_resolutions, merge
from .models import (
    Conflict,
    DesignerSettings,
    Layout,
    Lock,
    LockScope,
    Member,
    Revision,
    SystemRecord,
    SystemStatus,
    UserRole,
    Workspace,
)
from .rbac import (
    BREAK_LOCK,
    CREATE,
    DELETE,
    EDIT,
    LOCK,
    MANAGE_MEMBERS,
    MANAGE_SETTINGS,
    PUBLISH,
    READ_AUDIT,
    RESTORE,
    VIEW,
    Decision,
    PermissionDenied,
    Principal,
    decide,
    permissions_for,
    role_of,
)
from .repository import Repository, VersionConflict


class DesignerError(RuntimeError):
    """A request the designer refused for a reason the user can act on."""


@dataclass
class SaveOutcome:
    """What happened to a save: accepted, merged, or blocked by conflicts."""

    status: str                       # "saved" | "merged" | "conflict" | "stale"
    record: Optional[SystemRecord] = None
    conflicts: list[Conflict] = field(default_factory=list)
    merged_spec: Optional[dict[str, Any]] = None
    base_version: int = 0
    current_version: int = 0
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "base_version": self.base_version,
            "current_version": self.current_version,
            "record": self.record.model_dump(mode="json") if self.record else None,
            "conflicts": [c.model_dump(mode="json") for c in self.conflicts],
            "merged_spec": self.merged_spec,
        }


class DesignerService:
    def __init__(self, repository: Repository,
                 settings: Optional[DesignerSettings] = None) -> None:
        self.repository = repository
        self._settings = settings or repository.settings()
        self.audit = AuditLog(repository)
        self.locks = LockManager(repository, self._settings.lock_ttl_seconds,
                                 on_expire=self._audit_expired_lock)

    # -- audit -------------------------------------------------------------

    def _audit_expired_lock(self, lock: Lock) -> None:
        self.audit.record(
            AuditAction.LOCK_EXPIRE, None, outcome=AuditOutcome.SUCCESS,
            workspace_id=self._workspace_id_of(lock.system_id),
            system_id=lock.system_id, lock_target=lock.target,
            lock_holder=lock.holder,
            reason=f"lock expired at {lock.expires_at}",
        )

    def _workspace_id_of(self, system_id: str) -> str:
        record = self.repository.get_system(system_id)
        return record.workspace_id if record else ""

    def _require(self, workspace: Optional[Workspace], principal: Principal,
                 permission: str, action: AuditAction, *,
                 system_id: str = "", **fields: Any) -> Decision:
        """`require`, but a refusal is written down before it is raised.

        A denial leaves no other trace anywhere in the system — no revision, no
        lock, nothing — so if it is not recorded here it is not recorded at all.
        """
        decision = decide(workspace, principal, permission,
                          default_role=self._settings.default_role)
        if not decision.allowed:
            self.audit.record(
                action, principal, outcome=AuditOutcome.DENIED,
                workspace_id=workspace.id if workspace else "",
                system_id=system_id, permission=permission,
                reason=decision.reason, **fields,
            )
            raise PermissionDenied(decision.reason)
        return decision

    def audit_events(
        self, principal: Principal, *, system_id: Optional[str] = None,
        actor: Optional[str] = None, action: Optional[str] = None,
        since: Optional[str] = None, until: Optional[str] = None,
        limit: int = 200,
    ) -> list[AuditEvent]:
        """The log, scoped to the workspaces this principal may audit."""
        if system_id is not None:
            record = self._system(system_id)
            workspace = self.repository.get_workspace(record.workspace_id)
            self._require(workspace, principal, READ_AUDIT,
                          AuditAction.AUDIT_READ, system_id=system_id)
            readable = None
        else:
            readable = {
                w.id for w in self.repository.list_workspaces()
                if decide(w, principal, READ_AUDIT,
                          default_role=self._settings.default_role).allowed
            }
            if not readable:
                raise PermissionDenied(
                    f"{principal.label} may not read the designer audit log"
                )
        return self.audit.query(
            system_id=system_id, actor=actor,
            action=AuditAction(action) if action else None,
            workspace_ids=readable, since=since, until=until, limit=limit,
        )

    # -- settings ----------------------------------------------------------

    @property
    def settings(self) -> DesignerSettings:
        return self._settings

    def update_settings(self, principal: Principal,
                        changes: dict[str, Any]) -> DesignerSettings:
        workspace = self._workspace_for_settings(principal)
        self._require(workspace, principal, MANAGE_SETTINGS,
                      AuditAction.SETTINGS_UPDATE)
        updated = self._settings.model_copy(update=changes)
        updated.updated_by = principal.user_id
        self._settings = self.repository.save_settings(updated)
        self.locks.ttl_seconds = self._settings.lock_ttl_seconds
        self.audit.record(
            AuditAction.SETTINGS_UPDATE, principal,
            workspace_id=workspace.id if workspace else "",
            detail={"changed": sorted(changes)},
        )
        return self._settings

    def _workspace_for_settings(self, principal: Principal) -> Optional[Workspace]:
        """Settings are installation-wide; any workspace admin may change them."""
        for workspace in self.repository.list_workspaces():
            member = workspace.member(principal.user_id)
            if member and member.role in (UserRole.ADMIN, UserRole.OWNER):
                return workspace
        return self.repository.list_workspaces()[0] if self.repository.list_workspaces() \
            else None

    # -- workspaces --------------------------------------------------------

    def create_workspace(self, principal: Principal, name: str,
                         description: str = "") -> Workspace:
        workspace = Workspace(
            name=name, description=description,
            members=[Member(user_id=principal.user_id,
                            display_name=principal.display_name,
                            email=principal.email, role=UserRole.OWNER)],
        )
        saved = self.repository.save_workspace(workspace)
        self.audit.record(AuditAction.WORKSPACE_CREATE, principal,
                          workspace_id=saved.id, detail={"name": saved.name})
        return saved

    def workspaces(self, principal: Principal) -> list[Workspace]:
        return [
            w for w in self.repository.list_workspaces()
            if w.member(principal.user_id) is not None
        ]

    def add_member(self, principal: Principal, workspace_id: str,
                   member: Member) -> Workspace:
        workspace = self._workspace(workspace_id)
        self._require(workspace, principal, MANAGE_MEMBERS,
                      AuditAction.MEMBER_ADD)
        workspace.members = [
            m for m in workspace.members if m.user_id != member.user_id
        ] + [member]
        saved = self.repository.save_workspace(workspace)
        self.audit.record(AuditAction.MEMBER_ADD, principal,
                          workspace_id=workspace_id,
                          detail={"member": member.user_id,
                                  "role": member.role.value})
        return saved

    def remove_member(self, principal: Principal, workspace_id: str,
                      user_id: str) -> Workspace:
        workspace = self._workspace(workspace_id)
        self._require(workspace, principal, MANAGE_MEMBERS,
                      AuditAction.MEMBER_REMOVE)
        remaining = [m for m in workspace.members if m.user_id != user_id]
        if not any(m.role is UserRole.OWNER for m in remaining):
            raise DesignerError("a workspace must keep at least one owner")
        workspace.members = remaining
        saved = self.repository.save_workspace(workspace)
        self.audit.record(AuditAction.MEMBER_REMOVE, principal,
                          workspace_id=workspace_id,
                          detail={"member": user_id})
        return saved

    def _workspace(self, workspace_id: str) -> Workspace:
        workspace = self.repository.get_workspace(workspace_id)
        if workspace is None:
            raise DesignerError(f"no workspace '{workspace_id}'")
        return workspace

    def whoami(self, principal: Principal) -> dict[str, Any]:
        memberships = []
        for workspace in self.repository.list_workspaces():
            role = role_of(workspace, principal, None)  # type: ignore[arg-type]
            if role:
                memberships.append({
                    "workspace_id": workspace.id, "workspace": workspace.name,
                    "role": role.value, "permissions": permissions_for(role),
                })
        return {
            "user_id": principal.user_id,
            "display_name": principal.label,
            "email": principal.email,
            "workspaces": memberships,
        }

    # -- systems -----------------------------------------------------------

    def list_systems(self, principal: Principal,
                     workspace_id: Optional[str] = None) -> list[dict[str, Any]]:
        allowed = {w.id for w in self.workspaces(principal)}
        out = []
        for record in self.repository.list_systems(workspace_id):
            if record.workspace_id not in allowed:
                continue
            summary = record.summary()
            summary["locks"] = [
                lock.model_dump(mode="json") for lock in self.locks.active(record.id)
            ]
            out.append(summary)
        return out

    def create_system(self, principal: Principal, *, workspace_id: str, name: str,
                      description: str = "", spec: Optional[dict[str, Any]] = None,
                      layout: Optional[Layout] = None) -> SystemRecord:
        workspace = self._workspace(workspace_id)
        self._require(workspace, principal, CREATE, AuditAction.SYSTEM_CREATE)
        record = SystemRecord(
            workspace_id=workspace_id, name=name, description=description,
            spec=spec or _starter_spec(name), layout=layout or Layout(),
            created_by=principal.user_id,
        )
        saved = self.repository.save_system(record, expected_version=None,
                                            author=principal.user_id,
                                            message="created")
        self.audit.record(AuditAction.SYSTEM_CREATE, principal,
                          workspace_id=workspace_id, system_id=saved.id,
                          version_after=saved.version,
                          detail={"name": saved.name})
        return saved

    def open_system(self, principal: Principal, system_id: str) -> dict[str, Any]:
        record = self._system(system_id)
        workspace = self.repository.get_workspace(record.workspace_id)
        self._require(workspace, principal, VIEW, AuditAction.SYSTEM_VIEW,
                      system_id=system_id)
        role = role_of(workspace, principal, self._settings.default_role)
        return {
            "record": record.model_dump(mode="json"),
            "locks": [lock.model_dump(mode="json")
                      for lock in self.locks.active(system_id)],
            "role": role.value if role else None,
            "permissions": permissions_for(role),
            "validation": self.validate(record),
        }

    def save_system(
        self,
        principal: Principal,
        system_id: str,
        *,
        spec: Optional[dict[str, Any]] = None,
        layout: Optional[Layout] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[SystemStatus] = None,
        base_version: Optional[int] = None,
        strategy: Optional[str] = None,
        resolutions: Optional[dict[str, Any]] = None,
        message: str = "",
    ) -> SaveOutcome:
        """Write a change, honouring locks, versions and the merge policy."""
        record = self._system(system_id)
        workspace = self.repository.get_workspace(record.workspace_id)
        self._require(workspace, principal, EDIT, AuditAction.SYSTEM_SAVE,
                      system_id=system_id)

        blocking = self.locks.blocks(system_id, "*", principal)
        if blocking is not None and self._settings.concurrency != "optimistic":
            self.audit.record(
                AuditAction.SYSTEM_SAVE, principal, outcome=AuditOutcome.CONFLICT,
                workspace_id=record.workspace_id, system_id=system_id,
                version_before=record.version, lock_target=blocking.target,
                lock_holder=blocking.holder, reason=str(LockConflict(blocking)),
            )
            raise LockConflict(blocking)

        strategy = strategy or self._settings.default_merge_strategy
        updated = record.model_copy(deep=True)
        if spec is not None:
            updated.spec = spec
        if layout is not None:
            updated.layout = layout
        if name is not None:
            updated.name = name
        if description is not None:
            updated.description = description
        if status is not None:
            if status is SystemStatus.PUBLISHED:
                self._require(workspace, principal, PUBLISH,
                              AuditAction.SYSTEM_PUBLISH, system_id=system_id)
            updated.status = status

        try:
            saved = self.repository.save_system(
                updated, expected_version=base_version if base_version is not None
                else record.version, author=principal.user_id, message=message,
            )
            self.audit.record(
                AuditAction.SYSTEM_SAVE, principal,
                workspace_id=record.workspace_id, system_id=system_id,
                version_before=record.version, version_after=saved.version,
                detail={"message": message} if message else {},
            )
            return SaveOutcome("saved", saved, base_version=base_version or 0,
                               current_version=saved.version,
                               message="saved")
        except VersionConflict as conflict:
            if strategy != "merge" or spec is None:
                self.audit.record(
                    AuditAction.SYSTEM_SAVE, principal,
                    outcome=AuditOutcome.CONFLICT,
                    workspace_id=record.workspace_id, system_id=system_id,
                    version_before=conflict.expected,
                    version_after=conflict.actual,
                    reason="write was based on a version that is no longer current",
                )
                return SaveOutcome(
                    "stale", conflict.current, base_version=conflict.expected,
                    current_version=conflict.actual,
                    message="someone else saved while you were editing; reload or "
                            "merge",
                )
            return self._merge_save(principal, conflict, updated, resolutions, message)

    def _merge_save(self, principal: Principal, conflict: VersionConflict,
                    updated: SystemRecord, resolutions: Optional[dict[str, Any]],
                    message: str) -> SaveOutcome:
        ancestor = self.repository.revision(updated.id, conflict.expected)
        base_spec = ancestor.spec if ancestor else {}
        merged_spec, conflicts = merge(base_spec, updated.spec, conflict.current.spec)
        if resolutions:
            merged_spec, conflicts = apply_resolutions(merged_spec, conflicts,
                                                       resolutions)
        if conflicts:
            self.audit.record(
                AuditAction.MERGE_CONFLICT, principal,
                outcome=AuditOutcome.CONFLICT,
                workspace_id=conflict.current.workspace_id, system_id=updated.id,
                version_before=conflict.expected, version_after=conflict.actual,
                merged=True, conflict_paths=[c.path for c in conflicts],
                reason=f"{len(conflicts)} conflict(s) need a decision",
            )
            return SaveOutcome(
                "conflict", conflict.current, conflicts=conflicts,
                merged_spec=merged_spec, base_version=conflict.expected,
                current_version=conflict.actual,
                message=f"{len(conflicts)} conflict(s) need a decision",
            )
        merged_record = conflict.current.model_copy(deep=True)
        merged_record.spec = merged_spec
        # Layout is presentation: the saver's view wins, with the other side's
        # positions kept for nodes they added (ADR-0034).
        merged_record.layout = _merge_layout(conflict.current.layout, updated.layout)
        merged_record.name = updated.name
        merged_record.description = updated.description
        merged_record.status = updated.status
        saved = self.repository.save_system(
            merged_record, expected_version=conflict.actual,
            author=principal.user_id,
            message=message or f"merged with v{conflict.actual}",
        )
        self.audit.record(
            AuditAction.MERGE_RESOLVED if resolutions else AuditAction.MERGE_APPLIED,
            principal, workspace_id=saved.workspace_id, system_id=saved.id,
            version_before=conflict.expected, version_after=saved.version,
            merged=True,
            detail={"resolved": sorted(resolutions)} if resolutions else {},
        )
        return SaveOutcome("merged", saved, base_version=conflict.expected,
                           current_version=saved.version,
                           message=f"merged with version {conflict.actual}")

    def delete_system(self, principal: Principal, system_id: str) -> bool:
        record = self._system(system_id)
        self._require(self.repository.get_workspace(record.workspace_id), principal,
                      DELETE, AuditAction.SYSTEM_DELETE, system_id=system_id)
        deleted = self.repository.delete_system(system_id)
        # The log outlives the design it describes: deleting a system is exactly
        # the event somebody will come looking for afterwards.
        self.audit.record(AuditAction.SYSTEM_DELETE, principal,
                          workspace_id=record.workspace_id, system_id=system_id,
                          version_before=record.version,
                          outcome=AuditOutcome.SUCCESS if deleted
                          else AuditOutcome.FAILED,
                          detail={"name": record.name})
        return deleted

    def _system(self, system_id: str) -> SystemRecord:
        record = self.repository.get_system(system_id)
        if record is None:
            raise DesignerError(f"no system '{system_id}'")
        return record

    # -- locks -------------------------------------------------------------

    def acquire_lock(self, principal: Principal, system_id: str, *,
                     target: str = "*", scope: str = "component",
                     note: str = "") -> Lock:
        record = self._system(system_id)
        self._require(self.repository.get_workspace(record.workspace_id), principal,
                      LOCK, AuditAction.LOCK_ACQUIRE, system_id=system_id,
                      lock_target=target)
        try:
            lock = self.locks.acquire(system_id, principal, scope=LockScope(scope),
                                      target=target, note=note)
        except LockConflict as clash:
            self.audit.record(
                AuditAction.LOCK_ACQUIRE, principal, outcome=AuditOutcome.CONFLICT,
                workspace_id=record.workspace_id, system_id=system_id,
                lock_target=target, lock_holder=clash.lock.holder,
                reason=str(clash),
            )
            raise
        self.audit.record(AuditAction.LOCK_ACQUIRE, principal,
                          workspace_id=record.workspace_id, system_id=system_id,
                          lock_target=target, lock_holder=principal.user_id,
                          detail={"scope": scope})
        return lock

    def release_lock(self, principal: Principal, system_id: str,
                     target: str = "*") -> bool:
        released = self.locks.release(system_id, principal, target)
        if released:
            self.audit.record(AuditAction.LOCK_RELEASE, principal,
                              workspace_id=self._workspace_id_of(system_id),
                              system_id=system_id, lock_target=target,
                              lock_holder=principal.user_id)
        return released

    def heartbeat(self, principal: Principal, system_id: str,
                  target: str = "*") -> Optional[Lock]:
        return self.locks.heartbeat(system_id, principal, target)

    def break_lock(self, principal: Principal, system_id: str,
                   target: str = "*") -> bool:
        record = self._system(system_id)
        self._require(self.repository.get_workspace(record.workspace_id), principal,
                      BREAK_LOCK, AuditAction.LOCK_BREAK, system_id=system_id,
                      lock_target=target)
        held = self.locks.holder_of(system_id, target)
        broken = self.locks.break_lock(system_id, target)
        if broken:
            self.audit.record(AuditAction.LOCK_BREAK, principal,
                              workspace_id=record.workspace_id,
                              system_id=system_id, lock_target=target,
                              lock_holder=held.holder if held else "",
                              reason="lock taken from its holder")
        return broken

    # -- revisions ---------------------------------------------------------

    def revisions(self, principal: Principal, system_id: str,
                  limit: int = 50) -> list[Revision]:
        record = self._system(system_id)
        self._require(self.repository.get_workspace(record.workspace_id), principal,
                      VIEW, AuditAction.SYSTEM_VIEW, system_id=system_id)
        return self.repository.revisions(system_id, limit)

    def restore(self, principal: Principal, system_id: str,
                version: int) -> SystemRecord:
        record = self._system(system_id)
        self._require(self.repository.get_workspace(record.workspace_id), principal,
                      RESTORE, AuditAction.SYSTEM_RESTORE, system_id=system_id)
        revision = self.repository.revision(system_id, version)
        if revision is None:
            raise DesignerError(f"no revision {version} of '{system_id}'")
        record.spec = revision.spec
        record.binding = revision.binding
        record.layout = revision.layout
        before = record.version
        saved = self.repository.save_system(
            record, expected_version=record.version, author=principal.user_id,
            message=f"restored version {version}",
        )
        self.audit.record(AuditAction.SYSTEM_RESTORE, principal,
                          workspace_id=saved.workspace_id, system_id=system_id,
                          version_before=before, version_after=saved.version,
                          detail={"restored": version})
        return saved

    # -- validation --------------------------------------------------------

    def validate(self, record: SystemRecord) -> dict[str, Any]:
        """Run the spec validator over a design in progress, tolerating drafts."""
        import yaml

        try:
            spec = load_spec_text(yaml.safe_dump(record.spec))
        except Exception as e:      # a draft may not parse yet; say so plainly
            return {"ok": False, "errors": [f"{type(e).__name__}: {e}"],
                    "warnings": []}
        findings = validate_spec(spec)
        return {
            "ok": not any(f.severity == "error" for f in findings),
            "errors": [str(f) for f in findings if f.severity == "error"],
            "warnings": [str(f) for f in findings if f.severity == "warning"],
        }


def _merge_layout(theirs: Layout, ours: Layout) -> Layout:
    """Keep our positions, adopt theirs for nodes we do not have."""
    merged = ours.model_copy(deep=True)
    for node_id, node in theirs.nodes.items():
        merged.nodes.setdefault(node_id, node)
    known = {(e.source, e.target, e.kind) for e in merged.edges}
    merged.edges += [
        e for e in theirs.edges if (e.source, e.target, e.kind) not in known
    ]
    return merged


def _starter_spec(name: str) -> dict[str, Any]:
    """The smallest spec that opens cleanly on a canvas."""
    return {
        "metadata": {"name": name, "spec_version": "1.1.0", "version": "0.1.0",
                     "environment": "development"},
        "organization": {"id": "root", "name": name, "leader": "", "mandate": [],
                         "members": [], "teams": []},
        "data_classes": [], "capabilities": [], "environments": [], "roles": [],
        "policies": [], "channels": [], "triggers": [], "knowledge": [],
        "skills": [], "plugins": [], "tools": [], "endpoints": [],
    }
