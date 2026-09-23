"""The designer service: the API's whole surface, in one testable object.

Everything the frontend can do goes through here — list and open systems, save
with concurrency control, lock and unlock, resolve conflicts, browse and
restore revisions, manage members and settings. The UI holds no rules of its
own, which is what makes the frontend genuinely replaceable (ADR-0031).
"""
from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError

from dataclasses import dataclass, field
from typing import Any, Optional

from ..spec.loader import load_spec_text
from ..spec.validate import Finding, validate_spec
from .audit import AuditAction, AuditEvent, AuditLog, AuditOutcome
from .locks import LockConflict, LockManager
from .merge import apply_resolutions, merge
from .models import (
    Conflict,
    DesignerSettings,
    Diagram,
    DiagramKind,
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

    def _permissions_for(self, role: Optional[UserRole]) -> list[str]:
        """What to tell a caller they may do, settings included."""
        return permissions_for(
            role, break_lock_requires=self._settings.lock_break_requires
        )

    def _require(self, workspace: Optional[Workspace], principal: Principal,
                 permission: str, action: AuditAction, *,
                 system_id: str = "", **fields: Any) -> Decision:
        """`require`, but a refusal is written down before it is raised.

        A denial leaves no other trace anywhere in the system — no revision, no
        lock, nothing — so if it is not recorded here it is not recorded at all.
        """
        decision = decide(
            workspace, principal, permission,
            default_role=self._settings.default_role,
            break_lock_requires=self._settings.lock_break_requires,
        )
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
        # `model_copy(update=...)` does not validate, so a settings PUT could
        # put a string where an int is declared and a nonsense value where a
        # Literal is. It surfaced as a 500 from the repository's own wrapper
        # on the relational backend, and on the others it simply persisted.
        # Validate here, where the rules live, and refuse with the reason.
        try:
            updated = DesignerSettings.model_validate(
                {**self._settings.model_dump(), **changes}
            )
        except PydanticValidationError as exc:
            raise ValueError(
                "these settings were refused: "
                + "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                    for e in exc.errors()
                )
            ) from exc
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
                    "role": role.value,
                    "permissions": self._permissions_for(role),
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
            "permissions": self._permissions_for(role),
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
            _check_diagram_kinds(layout, spec if spec is not None else record.spec)
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

    # -- review surfaces (read-only) ---------------------------------------
    #
    # The gate and the IR diff report on evidence and on revisions that already
    # exist. Nothing here writes: a gate that produced evidence in order to
    # report on it would not be a gate, and a diff is a reading of history.

    def spec_at(self, principal: Principal, system_id: str,
                version: Optional[int] = None) -> tuple[dict[str, Any],
                                                        Optional[dict[str, Any]], int]:
        """The stored spec and binding of one version, behind the view check."""
        record = self._system(system_id)
        self._require(self.repository.get_workspace(record.workspace_id), principal,
                      VIEW, AuditAction.SYSTEM_VIEW, system_id=system_id)
        if version is None or version == record.version:
            return record.spec, record.binding, record.version
        revision = self.repository.revision(system_id, version)
        if revision is None:
            raise DesignerError(f"no revision {version} of '{system_id}'")
        return revision.spec, revision.binding, revision.version

    def publish_candidate(
        self, principal: Principal, system_id: str
    ) -> tuple[dict[str, Any], Optional[dict[str, Any]], int, str]:
        """The stored design somebody is asking to deploy, behind PUBLISH.

        The **stored** one, deliberately. A publish names a revision, so an
        unsaved draft cannot be published: the artifact a reviewer looked at
        and the artifact the fabric builds have to be the same document, and
        the version number is what makes that checkable afterwards.

        Publishing is separated from editing on purpose. `EDITOR` may change a
        design all day and may not ask for it to be run; `ADMIN` and `OWNER`
        hold `system.publish`. Deciding what an organization of agents should
        be and deciding to switch it on are different acts.
        """
        record = self._system(system_id)
        self._require(
            self.repository.get_workspace(record.workspace_id), principal,
            PUBLISH, AuditAction.SYSTEM_PUBLISH, system_id=system_id,
        )
        return record.spec, record.binding, record.version, record.name

    def record_publish(
        self, principal: Principal, system_id: str, *,
        outcome: AuditOutcome, **fields: Any
    ) -> None:
        """Write down what a publish did, refusals included.

        A refused publish is the interesting one: it is the phase gate saying
        no to a named person about a named design, and nothing else in the
        system would keep that.
        """
        record = self.repository.get_system(system_id)
        # The publish specifics — which tenant, which target, which deployment
        # — are not `AuditEvent` fields and should not become any: `detail` is
        # where an action's own particulars go, so the event schema does not
        # grow a column per caller.
        version = fields.pop("version", None)
        reason = str(fields.pop("reason", ""))
        self.audit.record(
            AuditAction.SYSTEM_PUBLISH, principal, outcome=outcome,
            workspace_id=record.workspace_id if record else "",
            system_id=system_id, version_after=version, reason=reason,
            detail={k: v for k, v in fields.items() if v not in (None, "")},
        )

    def review_pair(
        self, principal: Principal, system_id: str, *,
        left: Optional[int] = None, right: Optional[int] = None,
    ) -> tuple[tuple[dict[str, Any], Optional[dict[str, Any]], int],
               tuple[dict[str, Any], Optional[dict[str, Any]], int]]:
        """Two versions of one design to compare, resolved behind one check.

        The defaults are the pair a reviewer means by "what changed": the
        current version against the one before it. A design with only one
        version compares against itself, which is an empty diff rather than a
        refusal — nothing has changed yet is a true answer.
        """
        record = self._system(system_id)
        self._require(self.repository.get_workspace(record.workspace_id), principal,
                      VIEW, AuditAction.SYSTEM_VIEW, system_id=system_id)
        history = {r.version: r for r in self.repository.revisions(system_id,
                                                                   limit=5000)}

        def at(version: int) -> tuple[dict[str, Any], Optional[dict[str, Any]], int]:
            if version == record.version:
                return record.spec, record.binding, record.version
            revision = history.get(version)
            if revision is None:
                raise DesignerError(f"no revision {version} of '{system_id}'")
            return revision.spec, revision.binding, revision.version

        to_version = record.version if right is None else right
        if to_version != record.version and to_version not in history:
            raise DesignerError(f"no revision {to_version} of '{system_id}'")
        if left is None:
            earlier = [v for v in history if v < to_version]
            from_version = max(earlier) if earlier else to_version
        else:
            from_version = left
        return at(from_version), at(to_version)

    # -- validation --------------------------------------------------------

    def validate(self, record: SystemRecord) -> dict[str, Any]:
        """Run the spec validator over a design in progress, tolerating drafts.

        Findings are structured and carry the component they are about. A
        draft that does not parse used to come back as one string holding the
        whole of pydantic's report — four errors, four documentation URLs and
        every input value, rendered as a single bullet in the inspector. It
        was unreadable and it named nothing you could click. Each pydantic
        error is now its own finding, pointed at the nearest component with an
        id, so the UI can take you to what is wrong.

        `errors` and `warnings` stay as strings for callers that only report;
        `findings` is the same set with its parts still separate.
        """
        import yaml

        try:
            spec = load_spec_text(yaml.safe_dump(record.spec))
        except PydanticValidationError as exc:
            findings = _parse_findings(exc, record.spec)
        except Exception as e:      # not even YAML/shape; say so plainly
            findings = [Finding(severity="error", code="unreadable",
                                message=f"{type(e).__name__}: {e}")]
        else:
            findings = list(validate_spec(spec))
        return {
            "ok": not any(f.severity == "error" for f in findings),
            "errors": [str(f) for f in findings if f.severity == "error"],
            "warnings": [str(f) for f in findings if f.severity == "warning"],
            "findings": [
                {"severity": f.severity, "code": f.code, "where": f.where,
                 "message": f.message,
                 "component": _component_at(record.spec, f.where)}
                for f in findings
            ],
        }



class DiagramKindMismatch(ValueError):
    """A diagram claims to draw something its root is not (ADR-0100).

    A kind is a claim about what a canvas contains, and a canvas that quietly
    drew the wrong thing for its contents would be worse than one that
    refused: the picture is what people review.
    """


def _check_diagram_kinds(layout: Layout, spec: dict[str, Any]) -> None:
    workflow_ids = {w.get("id") for w in (spec or {}).get("workflows", [])
                    if isinstance(w, dict)}
    for diagram in layout.diagrams.values():
        if diagram.kind is DiagramKind.PROCESS:
            if not diagram.root:
                raise DiagramKindMismatch(
                    f"diagram '{diagram.name}' is a process canvas and names "
                    "no workflow. A process canvas draws one workflow's graph")
            if diagram.root not in workflow_ids:
                raise DiagramKindMismatch(
                    f"diagram '{diagram.name}' is a process canvas rooted at "
                    f"'{diagram.root}', which is not a workflow in this design")
        elif diagram.root and diagram.root in workflow_ids:
            raise DiagramKindMismatch(
                f"diagram '{diagram.name}' is an organisation canvas rooted at "
                f"workflow '{diagram.root}'. Set its kind to process, or root "
                "it at a team")


def _merge_layout(theirs: Layout, ours: Layout) -> Layout:
    """Keep our positions, adopt theirs for nodes and diagrams we do not have.

    Nodes only, per diagram. Edges are derived from the spec by whatever draws
    the picture, so there is nothing here to reconcile — and merging two
    stored edge lists was reconciling something neither side had ever written.

    A diagram the other side added is kept whole: two people working on one
    design are usually looking at two different parts of it, and dropping
    somebody's new diagram because you did not have it is the worst possible
    resolution.
    """
    merged = ours.model_copy(deep=True)
    for diagram_id, theirs_diagram in theirs.diagrams.items():
        ours_diagram = merged.diagrams.get(diagram_id)
        if ours_diagram is None:
            merged.diagrams[diagram_id] = theirs_diagram.model_copy(deep=True)
            continue
        for node_id, node in theirs_diagram.nodes.items():
            ours_diagram.nodes.setdefault(node_id, node)
    # `active` is where *this* reader is looking, so it is never merged.
    return Layout.model_validate(merged.model_dump())


def _starter_spec(name: str) -> dict[str, Any]:
    """The smallest spec that opens cleanly on a canvas."""
    return {
        "metadata": {"name": name, "spec_version": "1.1.0", "version": "0.1.0",
                     "environment": "development"},
        # No mandate: a new organization has not said what it may decide, and
        # validation says so rather than defaulting it to unlimited
        # (ADR-0065 rule 5).
        "organization": {"id": "root", "name": name, "leader": "",
                         "members": [], "teams": []},
        "data_classes": [], "capabilities": [], "decisions": [],
        "environments": [], "roles": [],
        "policies": [], "channels": [], "triggers": [], "knowledge": [],
        "skills": [], "plugins": [], "tools": [], "endpoints": [],
    }


# --------------------------------------------------------------- findings
#
# Making a validation failure traceable is a matter of naming the component,
# not the path: `organization.teams.0.teams.0.mandate` is where the error is,
# but "team_3" is what the reader has on the canvas and can click.

def _walk_to(data: Any, path: tuple[Any, ...]) -> list[Any]:
    """Everything on the way to `path`, nearest last, skipping what is missing."""
    seen: list[Any] = [data]
    here = data
    for step in path:
        try:
            here = (here[int(step)] if isinstance(here, list)
                    else here[str(step)])
        except (KeyError, IndexError, TypeError, ValueError):
            break
        seen.append(here)
    return seen


def _component_at(spec: dict[str, Any], where: str) -> str:
    """The id of the nearest declared component containing `where`.

    Nearest, because an error on a team's mandate belongs to that team and not
    to the organisation that holds it. Empty when nothing on the path has an
    id, which is honest: not every error is about a component.
    """
    if not where:
        return ""
    # The spec validator's findings name the component directly ("payables_
    # clerk"), while pydantic's name a path into the document. Both arrive
    # here, so a `where` that is already a declared id is taken as one.
    if where in _declared_ids(spec):
        return where
    path = tuple(where.split("."))
    for node in reversed(_walk_to(spec, path)):
        if isinstance(node, dict) and isinstance(node.get("id"), str):
            return node["id"]
    return ""


def _parse_findings(exc: PydanticValidationError,
                    spec: dict[str, Any]) -> list[Finding]:
    """One finding per pydantic error, with the noise left out.

    Pydantic's own rendering repeats the input value and a documentation URL
    for every error. Neither helps somebody looking at a canvas, and together
    they buried the one sentence that did.
    """
    out: list[Finding] = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"])
        message = str(error.get("msg", "")).removeprefix("Value error, ")
        out.append(Finding(severity="error", code=str(error.get("type", "invalid")),
                           message=message, where=where))
    return out


def _declared_ids(spec: Any) -> set[str]:
    """Every id declared anywhere in a spec document."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("id"), str):
                found.add(node["id"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(spec)
    return found
