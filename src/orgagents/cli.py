"""Command line entry point: ``orgagents <command>``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .platform import Platform


def _scheduler_command(args: argparse.Namespace) -> int:
    """Run the compiled triggers against a loaded platform."""
    import time
    from datetime import datetime, timezone

    from .platform import Platform
    from .runtime.scheduler import from_manifest

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    platform = Platform(args.sched_db, configure_logs=False)

    def runner(trigger, now):
        prompt = trigger.input.get("question") or trigger.description or trigger.id
        return platform.runtime.run(trigger.agent, prompt, created_by=f"trigger:{trigger.id}")

    def notifier(channel, message, payload):
        print(f"[notify {channel}] {message}")

    service = from_manifest(manifest, runner, notifier=notifier)
    now = datetime.now(timezone.utc)
    service.prime(now)
    for row in service.table(now):
        print(f"{row['trigger']:24} agent={row['agent']:18} next={row['next_run']}")
    if args.once:
        for outcome in service.tick(now):
            status = "skipped" if outcome.skipped else ("ok" if outcome.ok else "failed")
            print(f"{outcome.trigger_id}: {status} {outcome.reason}")
        return 0
    print("\nscheduler running; ctrl-c to stop")
    try:
        while True:
            time.sleep(30)
            for outcome in service.tick(datetime.now(timezone.utc)):
                status = "skipped" if outcome.skipped else ("ok" if outcome.ok else "failed")
                print(f"{outcome.trigger_id}: {status} {outcome.reason}")
    except KeyboardInterrupt:
        return 0


def _spec_language_command(args: argparse.Namespace) -> int:
    """`spec migrate` and `spec schema` — version tooling (WS-002 M4/M5)."""
    import yaml

    from .spec.loader import SpecVersionError, dump_spec, load_spec_text
    from .spec.migrations import CURRENT, migrate
    from .spec.schema import system_spec_schema_json

    if args.action == "schema":
        text = system_spec_schema_json()
        if args.out:
            Path(args.out).write_text(text)
            print(f"wrote {args.out}")
        else:
            print(text, end="")
        return 0

    if not args.path:
        print("error: spec migrate needs a path")
        return 2
    source = Path(args.path).read_text(encoding="utf-8")
    data = yaml.safe_load(source) or {}
    declared = str((data.get("metadata") or {}).get("spec_version", CURRENT))
    try:
        upgraded, changes = migrate(data, to=CURRENT)
    except SpecVersionError as e:
        print(f"error: {e}")
        return 1

    if not changes:
        print(f"{args.path} is already at spec_version {CURRENT}; nothing to do")
        return 0
    print(f"{args.path}: {declared} → {CURRENT}")
    for line in changes:
        print(f"  {line}")

    # Validate before writing: a migration that produces an unloadable
    # document should fail loudly rather than overwrite the original.
    spec = load_spec_text(yaml.safe_dump(upgraded, sort_keys=False))
    if args.write:
        Path(args.path).write_text(dump_spec(spec))
        print(f"wrote {args.path}")
    else:
        print("(pass --write to save)")
    return 0


def _approved_catalog(args: argparse.Namespace) -> list:
    """The platform's catalog, best-effort.

    Scaffolding is a convenience, so a missing or unreadable catalog must not
    stop it: the generic templates stand in and the author is no worse off
    than before the catalog existed.
    """
    try:
        from .catalogs import CatalogService
        from .platform import Platform

        return list(CatalogService(
            Platform(args.db, configure_logs=False).store).list())
    except Exception:                       # noqa: BLE001 - convenience only
        return []


def _spec_new_command(args: argparse.Namespace) -> int:
    """`spec new <name>` — a starter design that is valid from the first save.

    It compiles immediately, so the author sees something real, and it carries
    the phase gate's remaining checks as commented blocks so the next step is
    never a blank page (ADR-0090).
    """
    from .scaffold import starter_spec

    name = getattr(args, "path", None)
    if not name:
        print("error: spec new needs a name, e.g. `orgagents spec new acme`")
        return 2
    name = Path(name).stem.replace(".system", "")
    text = starter_spec(name, owner=getattr(args, "owner", "") or "",
                        catalog=_approved_catalog(args))
    out = Path(args.out) if args.out else Path(f"{name}.system.yaml")
    if out.exists() and not args.force:
        print(f"error: {out} exists; pass --force to overwrite")
        return 1
    out.write_text(text)
    print(f"wrote {out}")
    print(f"  next: orgagents spec validate {out}")
    print(f"        orgagents phase {out} --target local --scaffold")
    return 0


def _providers_command(args: argparse.Namespace) -> int:
    """`providers` — what is installed, and what each one can carry.

    The designer reads this to decide which fields to offer: deep agents has
    an interrupt gate and skills, the OpenAI Agents SDK has handoffs, and a
    UI that hardcoded either would be wrong for the next one (ADR-0091).
    """
    from .compiler.base import register_builtin_targets
    from .plugins import FEATURES
    from .runtime.adapters import register_builtin_adapters

    targets = register_builtin_targets()
    runtimes = register_builtin_adapters()
    rows = runtimes.describe_all() + targets.descriptors()

    if args.feature:
        if args.feature not in FEATURES:
            print(f"unknown feature '{args.feature}'; known: "
                  f"{', '.join(sorted(FEATURES))}")
            return 2
        named = [r["id"] for r in rows if args.feature in r["supports"]]
        print(f"{args.feature}: {', '.join(named) or 'nothing supports it'}")
        return 0

    if args.format == "json":
        print(json.dumps({"features": FEATURES, "providers": rows}, indent=2))
        return 0

    for kind in ("runtime", "target"):
        subset = [r for r in rows if r["kind"] == kind]
        if not subset:
            continue
        print(f"\n── {kind}s " + "─" * (52 - len(kind)))
        for row in subset:
            mark = " [plugin]" if row["third_party"] else ""
            print(f"  {row['id']:24}{mark} {row['title']}")
            print(f"  {'':24} supports: {', '.join(row['supports']) or 'nothing declared'}")
    failed = {**runtimes.failures, **targets.registry.failures}
    if failed:
        print("\n! plugins that failed to load:")
        for name, why in sorted(failed.items()):
            print(f"  {name}: {why}")
    return 0


def _compiler_command(args: argparse.Namespace) -> int:
    from .compiler import build_ir, compile_system
    from .compiler.base import register_builtin_targets
    from .compiler.engine import CompileError
    from .compiler.tenancy import TenantIsolationError
    from .spec import load_binding, load_spec, validate_spec

    # A template directory turns the template target on: it is inert without
    # one, so it registers only when the user points it somewhere (ADR-0092).
    if getattr(args, "template_dir", None):
        from .compiler.base import REGISTRY
        from .compiler.targets.template import TemplateTarget

        register_builtin_targets()
        REGISTRY.register(TemplateTarget(args.template_dir), replace=True)

    if args.cmd == "targets":
        for description in register_builtin_targets().describe_all():
            # `describe()` is each target's own dict: only `id` is promised.
            # A target that says what it emits rather than giving a summary
            # is described by that (the registry's descriptor does the same).
            summary = description.get("summary") or description.get("emits", "")
            print(f"{description['id']:18} {description.get('title', description['id'])}")
            if summary:
                print(f"{'':18} {summary}")
            for caveat in description.get("caveats", []):
                print(f"{'':18} ! {caveat}")
        return 0

    # These two answer questions *about* the spec language, so neither can go
    # through `load_spec` — one reads a document too old for it, the other
    # reads no document at all.
    if args.cmd == "spec" and args.action in ("migrate", "schema"):
        return _spec_language_command(args)

    # `spec new` reads no document either — it writes the first one.
    if args.cmd == "spec" and args.action == "new":
        return _spec_new_command(args)

    if not getattr(args, "path", None):
        print(f"error: {args.cmd} needs a path to a system spec")
        return 2
    spec = load_spec(args.path)
    binding = load_binding(args.binding) if getattr(args, "binding", None) else None
    # The fabric's house rules, never the design's: a spec cannot name the
    # policy it is judged by (ADR-0076).
    platform_policy = None
    if getattr(args, "platform_policy", None):
        from .platform_policy import load as _load_platform_policy

        platform_policy = _load_platform_policy(args.platform_policy)

    if args.cmd == "phase":
        from .phases import review

        report = review(spec, binding=binding, target=args.target,
                        platform_policy=platform_policy)
        for phase in ("definition", "implementation"):
            checks = report.of(phase)
            if not checks:
                continue
            print(f"\n── {phase} phase " + "─" * (46 - len(phase)))
            for check in checks:
                print(f"  {check}")
                if check.fix:
                    print(f"      → {check.fix}")
        print(f"\n{report.summary()}")
        if getattr(args, "scaffold", False):
            # The gate already knows what is missing; print the spec that
            # answers it rather than leaving the author at a blank page.
            from .scaffold import scaffold_for

            text = scaffold_for(report.failures(), name=spec.metadata.name,
                                target=args.target,
                                catalog=_approved_catalog(args))
            if args.out:
                Path(args.out).write_text(text)
                print(f"\nscaffold written to {args.out}")
            else:
                print("\n" + text)
        if args.target:
            print(
                f"ready to compile for '{args.target}': "
                f"{'yes' if report.ready_to_compile else 'no'}"
            )
        else:
            print("pass --target to review the implementation phase too")
        return 0 if not report.failures() else 1

    if args.cmd == "missions":
        from datetime import date as _date

        from .missions import sweep, window_is_open

        on = _date.fromisoformat(args.on) if args.on else _date.today()
        if args.action == "sweep":
            closed = sweep(spec, on)
            if not closed:
                print(f"nothing to sweep as of {on.isoformat()}")
            for mission_id in closed:
                print(f"completed {mission_id}")
            if closed and args.write:
                from .spec.loader import dump_spec

                Path(args.path).write_text(dump_spec(spec))
                print(f"wrote {args.path}")
            elif closed:
                print("(pass --write to save; the runtime already ignores them)")
            return 0
        for mission in spec.missions:
            state = "open" if window_is_open(
                mission.status.value, mission.starts_on, mission.ends_on, on
            ) else "closed"
            window = f"{mission.starts_on or '?'} → {mission.ends_on or '?'}"
            print(f"{mission.id:28} {mission.status.value:10} {state:7} {window}")
            print(f"{'':28} leader={mission.leader} "
                  f"members={', '.join(mission.members)}")
        return 0

    if args.cmd == "schedule":
        from datetime import datetime, timedelta, timezone

        from .runtime.scheduler import SchedulerService
        from .scheduling import describe, next_fire_times

        now = datetime.now(timezone.utc)
        for trigger in spec.triggers:
            flag = "" if trigger.enabled else "  (disabled)"
            print(f"{trigger.id:26} {describe(trigger)}{flag}")
            if trigger.cadence:
                for moment in next_fire_times(trigger.cadence, now, args.count):
                    print(f"{'':28}→ {moment.isoformat()}")
        if args.simulate_days:
            fired: list[str] = []
            service = SchedulerService(
                triggers=[t for t in spec.triggers if t.cadence],
                runner=lambda t, when: fired.append(t.id),
            )
            service.run_window(now, now + timedelta(days=args.simulate_days),
                               timedelta(minutes=5))
            counts: dict[str, int] = {}
            for tid in fired:
                counts[tid] = counts.get(tid, 0) + 1
            print(f"\nsimulated {args.simulate_days} day(s):")
            for tid, count in sorted(counts.items()):
                print(f"  {tid:26} {count} run(s)")
        return 0

    if args.cmd == "spec":
        if args.action == "diff":
            from .compiler.diff import IncomparableIRError, diff_ir

            if not args.against:
                print("error: 'spec diff' needs two specs: <before> <after>")
                return 2
            bound = binding.for_target(args.target) if binding else None
            left = build_ir(spec, target=args.target, binding=bound)
            right = build_ir(load_spec(args.against), target=args.target,
                             binding=bound)
            try:
                result = diff_ir(left, right, force=args.force)
            except IncomparableIRError as e:
                # Distinct exit code: "I will not compare these" is a different
                # answer from "I compared them and found something".
                print(f"refused: {e}")
                return 2
            print(json.dumps(result.to_dict(), indent=2)
                  if args.format == "json" else result.to_text())
            if args.fail_on != "none":
                order = ["low", "medium", "high", "critical"]
                floor = order.index(args.fail_on)
                blocking = [
                    c for c in result.changes
                    if c.security_relevant
                    and c.severity.value in order
                    and order.index(c.severity.value) >= floor
                ]
                return 1 if blocking else 0
            return 0
        if args.action == "validate":
            # Without --directory the reconciliation is inert by construction:
            # NullDirectory knows nobody and produces no findings, so behaviour
            # is unchanged for anyone who does not supply one (ADR-0047).
            directory = None
            if getattr(args, "directory", None):
                from .directory import StaticDirectory

                directory = StaticDirectory.from_file(args.directory)
            findings = validate_spec(spec, directory=directory,
                                     platform_policy=platform_policy)
            for f in findings:
                print(f)
            errors = [f for f in findings if f.severity == "error"]
            print(f"{len(spec.agents())} agents, {len(spec.teams())} teams, "
                  f"{len(errors)} errors, {len(findings) - len(errors)} warnings")
            return 1 if errors else 0
        bound = binding.for_target(args.target) if binding else None
        ir = build_ir(spec, target=args.target, binding=bound)
        if args.action == "ir":
            print(json.dumps(ir.model_dump(mode="json"), indent=2))
        else:
            for agent in ir.agents:
                print(f"{agent.id:20} team={'/'.join(agent.team_path):28} "
                      f"env={','.join(e.id for e in agent.environments) or '-':16} "
                      f"perms={len(agent.permissions)}")
        return 0

    tenant_ir = None
    foreign_prefixes: set[str] = set()
    if getattr(args, "tenant", None):
        from .fabric.tenants import TenantRegistry
        from .platform import Platform

        registry = TenantRegistry(Platform(args.db, configure_logs=False).store)
        tenant = registry.get(args.tenant)
        if tenant is None:
            print(f"error: no such tenant '{args.tenant}'")
            return 1
        if not tenant.may_deploy():
            print(f"error: tenant '{tenant.id}' is {tenant.status.value}")
            return 1
        tenant_ir = tenant.to_ir()
        foreign_prefixes = registry.prefixes(exclude=tenant.id)

    try:
        results = compile_system(
            spec,
            targets=args.targets,
            out_dir=args.out,
            binding=binding,
            force=args.force,
            tenant=tenant_ir,
            foreign_prefixes=foreign_prefixes,
            platform_policy=platform_policy,
        )
    except (CompileError, TenantIsolationError) as e:
        print(f"error: {e}")
        return 1
    for result in results:
        print(result.summary())
    return 0


def _catalogs_add(service, args: argparse.Namespace) -> int:
    """`catalogs add` — a JSON file, or inline fields for the common case.

    The common case is one MCP server, so kind and name are positional and the
    kind's own attributes are `--attr key=value`; a `[]`-suffixed key repeats
    into a list, which is what a tool list needs.
    """
    import json as _json

    from .catalogs import CatalogEntry, CatalogKind

    if args.file:
        payload = _json.loads(Path(args.file).read_text(encoding="utf-8"))
        entries = payload if isinstance(payload, list) else [payload]
        published = [service.publish(CatalogEntry.model_validate(e),
                                     actor=args.actor) for e in entries]
        for entry in published:
            print(f"{entry.id}  {entry.name}  {entry.status.value}")
        return 0
    if len(args.args) < 2:
        print("usage: orgagents catalogs add <kind> <name> [--attr k=v ...]")
        return 2
    kind, name = args.args[0], args.args[1]
    attributes: dict[str, object] = {}
    for pair in args.attr:
        key, _, value = pair.partition("=")
        if key.endswith("[]"):
            attributes.setdefault(key[:-2], []).append(value)
        elif value.lower() in ("true", "false"):
            attributes[key] = value.lower() == "true"
        elif value.lstrip("-").isdigit():
            attributes[key] = int(value)
        else:
            attributes[key] = value
    entry = CatalogEntry(
        kind=CatalogKind(kind), name=name, summary=args.summary,
        description=args.description, owner=args.owner, version=args.version,
        tags=args.tag, documentation_url=args.docs, attributes=attributes,
    )
    service.publish(entry, actor=args.actor)
    # Said at the point of creation, because an operator who publishes a server
    # and finds no design can pick it has been told nothing.
    print(f"{entry.id}  {entry.name}  {entry.status.value} "
          f"— not selectable until approved "
          f"(orgagents catalogs approve {entry.id})")
    return 0


def _catalogs_command(args: argparse.Namespace) -> int:
    from .catalogs import (
        ApprovalStatus,
        CatalogError,
        CatalogKind,
        CatalogService,
        seed_catalog,
    )
    from .platform import Platform

    platform = Platform(args.db, configure_logs=False)
    service = CatalogService(platform.store)

    if args.action == "seed":
        print(f"published {seed_catalog(service)} entries")
        return 0
    if args.action == "stats":
        print(json.dumps(service.stats(), indent=2))
        return 0
    if args.action == "add":
        return _catalogs_add(service, args)
    if args.action in ("edit", "retire", "delete", "send-back", "review"):
        if not args.args:
            print(f"usage: orgagents catalogs {args.action} <entry_id> ...")
            return 2
        entry_id = args.args[0]
        try:
            if args.action == "edit":
                fields = {k: v for k, v in (
                    ("name", args.name), ("summary", args.summary),
                    ("description", args.description), ("owner", args.owner),
                    ("documentation_url", args.docs),
                    ("tags", args.tag or None)) if v}
                # `--attr`/`--version` go through amend, which is where the
                # approved-entry refusal lives.
                substantive: dict[str, object] = {}
                if args.version:
                    substantive["version"] = args.version
                if args.attr:
                    entry = service._require(entry_id)
                    merged = dict(entry.attributes)
                    for pair in args.attr:
                        key, _, value = pair.partition("=")
                        merged[key] = value
                    substantive["attributes"] = merged
                entry = service.get(entry_id)
                if fields:
                    entry = service.update(entry_id, fields, actor=args.actor)
                if substantive:
                    entry = service.amend(entry_id, substantive, actor=args.actor)
                if not fields and not substantive:
                    print("nothing to change; pass --name/--summary/--owner/--attr")
                    return 2
            elif args.action == "review":
                status = args.status or (args.args[1] if len(args.args) > 1 else "")
                if not status:
                    print("usage: orgagents catalogs review <entry_id> --status <s>")
                    return 2
                entry = service.review(entry_id, ApprovalStatus(status),
                                       reviewer=args.actor, note=args.note)
            elif args.action == "send-back":
                entry = service.send_back(entry_id, actor=args.actor,
                                          note=args.note)
            elif args.action == "retire":
                entry = service.retire(entry_id, reviewer=args.actor,
                                       superseded_by=args.superseded_by or None,
                                       force=args.force)
            else:
                print(f"deleted {service.delete(entry_id, actor=args.actor)}")
                return 0
        except CatalogError as e:
            print(f"error: {e}")
            return 1
        print(f"{entry.name}: {entry.status.value}")
        return 0
    if args.action == "approve":
        if not args.args:
            print("usage: orgagents catalogs approve <entry_id>")
            return 2
        entry = service.review(args.args[0], ApprovalStatus.APPROVED,
                               reviewer="cli")
        print(f"{entry.name}: {entry.status.value}")
        return 0
    if args.action == "models":
        for entry in service.models():
            attributes = entry.attributes
            cost = attributes.get("cost_per_million_input")
            print(f"{entry.name:34} {entry.status.value:11} "
                  f"{', '.join(attributes.get('classes', [])) or '—':44} "
                  f"{('$' + str(cost)) if cost else '—':>8}/M in  "
                  f"{attributes.get('context_tokens', 0):>8} ctx  "
                  f"{entry.figure_state:11} "
                  f"{entry.provenance.describe()}")
        return 0
    if args.action == "refresh":
        if not args.args:
            print("usage: orgagents catalogs refresh <figures.json>")
            return 2
        from .catalogs import FileFigureSource

        report = service.refresh_figures(FileFigureSource(args.args[0]))
        print(report.summary())
        for name, fields in sorted(report.updated.items()):
            print(f"  updated {name}: {', '.join(fields)}")
        for name in report.uncovered:
            # Naming them is the point: the source said nothing, so these
            # figures are still whatever somebody typed.
            print(f"  left alone {name}")
        return 0
    if args.action == "usage":
        print(json.dumps(service.usage_report(), indent=2))
        return 0
    if args.action == "stale":
        for entry in service.stale_entries():
            print(f"{entry.name:34} {entry.provenance.describe()}")
        return 0
    kind = CatalogKind(args.kind) if args.kind else None
    for entry in service.search(" ".join(args.args), kind=kind):
        print(f"{entry.kind.value:22} {entry.name:34} {entry.status.value:11} "
              f"{entry.summary[:52]}")
    return 0


def _tenants_command(args: argparse.Namespace) -> int:
    """`tenants list|show|register|suspend|resume|retire` — the fabric plane.

    The CLI is an operator at the console of its own installation, so it acts
    as `fabric_admin`; the transition table still decides every move, and a
    refusal here is the same refusal the API gives (WS-028 M5).
    """
    from .designer.audit import AuditOutcome
    from .fabric.audit import FabricAuditAction, FabricAuditLog
    from .fabric.deployments import DeploymentService, OperatorRole
    from .fabric.tenants import (
        PrefixError,
        TenantIllegalTransition,
        TenantRegistry,
        TenantRetirementBlocked,
        TenantStatus,
        TenantTransitionDenied,
    )
    from .platform import Platform

    platform = Platform(args.db, configure_logs=False)
    tenants = TenantRegistry(platform.store)
    deployments = DeploymentService(platform.store)
    audit = FabricAuditLog(platform.store)
    actor = args.actor

    def record(action: FabricAuditAction, tenant_id: str,
               outcome: AuditOutcome, **detail) -> None:
        audit.record(action, actor=actor, outcome=outcome, tenant_id=tenant_id,
                     route=f"cli tenants {args.action}", reason=args.reason,
                     operator_roles=[OperatorRole.ADMIN.value], detail=detail)

    if args.action == "list":
        for tenant in tenants.list():
            print(f"{tenant.id:24} {tenant.namespace_prefix:20} "
                  f"{tenant.status.value:10} {tenant.isolation_domain.id:26} "
                  f"{len(deployments.list(tenant.id)):>3} deployments")
        return 0

    if not args.args:
        print(f"usage: orgagents tenants {args.action} <tenant_id>")
        return 2
    tenant_id = args.args[0]

    if args.action == "show":
        tenant = tenants.get(tenant_id)
        if tenant is None:
            print(f"error: no such tenant: {tenant_id}")
            return 1
        payload = tenant.model_dump(mode="json")
        payload["deployments"] = [
            {"id": d.id, "state": d.state.value} for d in deployments.list(tenant_id)
        ]
        print(json.dumps(payload, indent=2))
        return 0

    if args.action == "register":
        try:
            tenant = tenants.register(
                id=tenant_id,
                name=args.name or " ".join(args.args[1:]) or tenant_id,
                namespace_prefix=args.prefix,
                entitlements=args.entitlement,
                cloud_boundary=args.cloud_boundary,
            )
        except PrefixError as e:
            # Every prefix rule — shape, reserved names, collisions, and the
            # prefixes of retired tenants — is decided in fabric/tenants.py.
            record(FabricAuditAction.TENANT_REGISTER, tenant_id,
                   AuditOutcome.CONFLICT, error=str(e))
            print(f"error: {e}")
            return 1
        record(FabricAuditAction.TENANT_REGISTER, tenant.id, AuditOutcome.SUCCESS,
               namespace_prefix=tenant.namespace_prefix,
               isolation_domain=tenant.isolation_domain.id)
        print(f"registered {tenant.id}: prefix={tenant.namespace_prefix} "
              f"domain={tenant.isolation_domain.id} status={tenant.status.value}")
        return 0

    target = {"suspend": TenantStatus.SUSPENDED,
              "resume": TenantStatus.ACTIVE,
              "retire": TenantStatus.RETIRED}[args.action]
    try:
        moved = tenants.transition(
            tenant_id, target, actor=actor, role=OperatorRole.ADMIN,
            reason=args.reason,
            deployments=deployments.list(tenant_id),
        )
    except KeyError as e:
        print(f"error: {e}")
        return 1
    except (TenantIllegalTransition, TenantRetirementBlocked,
            TenantTransitionDenied) as e:
        record(FabricAuditAction.TENANT_LIFECYCLE, tenant_id,
               AuditOutcome.CONFLICT, error=str(e))
        print(f"error: {e}")
        return 1
    record(FabricAuditAction.TENANT_LIFECYCLE, tenant_id, AuditOutcome.SUCCESS,
           to=moved.status.value)
    print(f"{moved.id}: {moved.status.value}")
    return 0


def _evaluation_command(args: argparse.Namespace) -> int:
    """Run the declared cases, or report the gate without running anything.

    `gate` deliberately does not run: it answers "what does the evidence say
    today", and a command that silently produced evidence in order to report
    on it would defeat the point of the gate.
    """
    from .evaluations import EvaluationService, GateState
    from .platform import Platform
    from .spec import load_spec
    from .spec.model import LifecycleStage

    spec = load_spec(args.path)
    stage = LifecycleStage(args.stage) if args.stage else LifecycleStage.PRODUCTION
    db = getattr(args, "eval_db", None) or getattr(args, "gate_db", None)
    platform = Platform(db, configure_logs=False)
    service = EvaluationService(platform.store)

    if args.cmd == "evaluate":
        run = service.run(
            spec,
            agent_ids=[args.agent] if args.agent else None,
            to_stage=stage,
        )
        for result in run.results:
            mark = {"passed": "ok", "failed": "FAIL"}.get(
                result.outcome.value, result.outcome.value)
            print(f"{mark:>11}  {result.agent_id:18} {result.case_id:22} "
                  f"{result.reason}")
        print()
    # Both commands report the gate; only `evaluate` produced the evidence.
    verdicts = service.gate_states(spec, to_stage=stage)

    blocking = 0
    for agent_id, verdict in sorted(verdicts.items()):
        state = verdict.state.value
        required = getattr(verdict, "required", True)
        flag = "" if state == GateState.PASSED.value or not required else "  <-"
        print(f"{state:15} {agent_id:20} {getattr(verdict, 'reason', '')}{flag}")
        if required and state != GateState.PASSED.value:
            blocking += 1
    if blocking:
        print(f"\n{blocking} agent(s) do not meet a required evaluation gate")
    return 1 if blocking else 0


def _records_command(args: argparse.Namespace) -> int:
    from . import records

    rs = records.load(args.root)
    if args.action == "validate":
        errors = records.validate(rs)
        for e in errors:
            print(f"error: {e}")
        print(f"{len(rs.records)} records, {len(errors)} violations")
        return 1 if errors else 0
    if args.action == "index":
        for path in records.write_indexes(rs, args.root):
            print(f"wrote {path}")
        return 0
    if args.action == "graph":
        print(json.dumps(records.graph(rs), indent=2))
        return 0
    if args.action == "list":
        for r in sorted(rs.records.values(), key=lambda x: x.id):
            print(f"{r.id:9} {r.status:11} v{r.version:8} {r.title}")
        return 0
    if args.action == "new":
        if len(args.args) < 2:
            print("usage: orgagents records new <adr|ws> <title>")
            return 2
        kind, title = args.args[0], " ".join(args.args[1:])
        print(records.scaffold(rs, kind, title, args.root))
        return 0
    return 2


def _write_out(text: str, out: str) -> None:
    if out == "-":
        sys.stdout.write(text)
        return
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {out}", file=sys.stderr)


def _exchange_command(args) -> int:
    """`orgagents export` / `orgagents import` (ADR-0113)."""
    from .spec import exchange

    if args.cmd == "export" and args.schema:
        _write_out(exchange.schema_text(), args.out)
        return 0
    try:
        if args.cmd == "export" and args.system:
            from .designer.repository import PostgresRepository
            from .spec.binding import Binding
            from .spec.model import SystemSpec
            repo = PostgresRepository.from_url()
            record = repo.get_system(args.system)
            if record is None:
                print(f"no design '{args.system}'", file=sys.stderr)
                return 1
            if args.part == "binding":
                if not record.binding:
                    print("this design has no binding", file=sys.stderr)
                    return 1
                data = record.binding if "targets" in record.binding else \
                    {"spec": record.spec.get("metadata", {}).get("name", ""),
                     "targets": [record.binding]}
                obj = Binding.model_validate(data)
            else:
                obj = SystemSpec.model_validate(record.spec)
        else:
            if not args.file:
                print("name a file, or --system", file=sys.stderr)
                return 2
            obj = exchange.load(Path(args.file).read_text(encoding="utf-8"))
    except exchange.TypedDocumentError as e:
        for issue in e.issues:
            print(issue, file=sys.stderr)
        return 1
    _write_out(exchange.dump(obj, args.format, typed=not args.untyped),
               args.out)
    return 0


def _db_command(args) -> int:
    """`orgagents db …` (ADR-0113)."""
    from .persistence import migrations, relational

    if args.action == "ddl":
        sys.stdout.write(relational.ddl())
        return 0
    if args.action == "generate-migration":
        path = migrations.generate(" ".join(args.args) or "change")
        print(f"wrote {path}" if path else
              "no change: the committed migrations match the profiles")
        return 0
    from .persistence.store import engine, migrate
    eng = engine(args.database_url)
    try:
        applied = migrate(eng)
        if args.action == "migrate":
            print("applied " + ", ".join(applied) if applied
                  else "the database is up to date")
            return 0
        if args.action == "query":
            from .persistence.queries import run
            name, *params = args.args or ["q4"]
            kw = dict(p.split("=", 1) for p in params)
            with eng.connect() as conn:
                for row in run(conn, name, **kw):
                    print("\t".join("" if v is None else str(v) for v in row))
            return 0
        from .persistence.sqlite_import import migrate_from_sqlite
        if not args.args:
            print("name the SQLite designer database", file=sys.stderr)
            return 2
        report = migrate_from_sqlite(args.args[0], eng, once=args.once)
        print(report.summary())
        return 0 if report.ok else 1
    finally:
        eng.dispose()


def _examples_command(args) -> int:
    """`orgagents examples list|load|import` — the designer's Load example
    and Import from file, in a shell.

    The same module the UI's route calls, over the same designer store the
    server uses, so an example loaded here is the one a reader opens there.
    """
    from .designer.examples import UnknownExample, list_examples, load_example

    examples = list_examples()
    if args.action == "list":
        if not examples:
            print("no examples found (set ORGAGENTS_EXAMPLES_DIR to where "
                  "they are)")
            return 1
        width = max(len(e.id) for e in examples)
        for e in examples:
            print(f"{e.id:<{width}}  {e.agents:>3} agents  {e.teams:>3} teams  "
                  f"{e.workflows} workflow(s)  {e.name}")
        return 0

    if not args.example:
        print("load needs an example id (see 'orgagents examples list'), "
              "or 'all'; import needs a path")
        return 2
    if not args.user:
        # Not defaulted: a workspace is visible only to its members, so an
        # example loaded as somebody the reader is not would be invisible to
        # them — which would look exactly like the load having failed.
        print("load needs --user: the name you use in the designer's 'as' "
              "box, or the example will be in a workspace you cannot see")
        return 2

    from .api import create_app
    from .designer.rbac import Principal

    app = create_app(args.db, args.base_url)
    designer = app.state.designer
    who = Principal(user_id=args.user, display_name=args.user)
    if args.action == "import":
        # A design from anywhere on disk, not only the shipped examples.
        from .designer.service import FieldErrors
        path = Path(args.example)
        if not path.is_file():
            print(f"no file {path}")
            return 2
        workspace = args.workspace or designer.create_workspace(
            who, args.name or path.stem.removesuffix(".system")).id
        try:
            record = designer.import_system(
                who, workspace_id=workspace, text=path.read_text(encoding="utf-8"),
                filename=path.name, name=args.name)
        except FieldErrors as e:
            print(f"refused: {e}")
            return 1
        print(f"imported {path.name} as '{record.name}' ({record.id}) into "
              f"workspace {workspace}")
        return 0
    ids = [e.id for e in examples] if args.example == "all" else [args.example]
    for example_id in ids:
        try:
            result = load_example(designer, who, example_id,
                                  workspace_id=args.workspace, name=args.name)
        except UnknownExample as exc:
            print(exc.args[0])
            return 1
        where = ("a new workspace" if result["created_workspace"]
                 else f"workspace {result['workspace_id']}")
        print(f"loaded {example_id} as '{result['name']}' into {where} "
              f"for {args.user} ({result['system_id']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Output carries arrows and dashes; a console whose code page cannot show
    # them (cp1252 on Windows) gets a '?' rather than a traceback after the
    # work is already done.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="orgagents")
    parser.add_argument("--db", default="orgagents.db")
    parser.add_argument("--base-url", default="http://localhost:8000")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed", help="populate the demo organization")

    p_serve = sub.add_parser("serve", help="run the API and designer UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    # The command every generated agent container runs (ADR-0109).
    p_work = sub.add_parser("worker", help="run one compiled agent as a service")
    p_work.add_argument("agent_id")
    p_work.add_argument("--ir", default="/app/system.ir.json",
                        help="the compiled system (system.ir.json)")
    p_work.add_argument("--host", default="0.0.0.0")
    p_work.add_argument("--port", type=int, default=8000)

    # The one-shot that creates the tenant's bus stream and consumers (ADR-0118).
    p_bus = sub.add_parser("bus-init",
                           help="create the bus stream and one consumer per agent")
    p_bus.add_argument("--ir", default="/app/system.ir.json")
    p_bus.add_argument("--attempts", type=int, default=30)

    p_prov = sub.add_parser(
        "providers",
        help="runtimes and targets installed, and what each supports")
    p_prov.add_argument("--feature", help="list only providers with this feature")
    p_prov.add_argument("--format", choices=["text", "json"], default="text")

    sub.add_parser("tree", help="print the org chart")
    sub.add_parser("metrics", help="print operational metrics")

    p_run = sub.add_parser("run", help="run an agent")
    p_run.add_argument("agent_id")
    p_run.add_argument("prompt")

    p_spec = sub.add_parser("spec", help="work with a System Spec")
    p_spec.add_argument("action",
                        choices=["new", "validate", "ir", "show", "migrate",
                                 "schema", "diff"])
    p_spec.add_argument("path", nargs="?",
                        help="the spec to read; for 'new', the name to create")
    p_spec.add_argument("--owner",
                        help="for 'new': the team accountable for the system")
    p_spec.add_argument("--binding")
    p_spec.add_argument("--platform-policy", dest="platform_policy",
                        help="the fabric's house rules to judge against")
    p_spec.add_argument("against", nargs="?",
                        help="for 'diff': the second spec; the first is `path`")
    p_spec.add_argument("--format", choices=["text", "json"], default="text")
    p_spec.add_argument("--fail-on",
                        choices=["critical", "high", "medium", "low", "none"],
                        default="none",
                        help="for 'diff': exit 1 on a security finding at or "
                             "above this severity")
    p_spec.add_argument("--force", action="store_true",
                        help="for 'diff': compare IRs the diff would refuse")
    p_spec.add_argument("--target", default="local")
    p_spec.add_argument("--directory",
                        help="a people directory (JSON/YAML) to reconcile human "
                             "pairings against; without it, departures go "
                             "undetected")
    p_spec.add_argument("--write", action="store_true",
                        help="for 'migrate': write the upgraded spec back")
    p_spec.add_argument("-o", "--out", dest="out",
                        help="for 'schema': write the JSON Schema to this file")

    p_comp = sub.add_parser("compile", help="generate code and infrastructure")
    p_comp.add_argument("path")
    p_comp.add_argument("--target", action="append", dest="targets")
    p_comp.add_argument("--binding")
    p_comp.add_argument("--platform-policy", dest="platform_policy",
                        help="the fabric's house rules this design is judged "
                             "against; its id and version are stamped into the IR")
    p_comp.add_argument("--out", default="build")
    p_comp.add_argument("--force", action="store_true")
    p_comp.add_argument(
        "--tenant",
        help="compile for this fabric tenant; every generated name is qualified "
             "with its namespace prefix and an unqualified artifact is refused",
    )
    p_comp.add_argument("--db", default="orgagents.db",
                        help="fabric database holding the tenant registry")

    p_targets = sub.add_parser("targets",
                               help="list available deployment targets")

    # Bring your own output format without writing Python (ADR-0092).
    for parser_ in (p_comp, p_targets):
        parser_.add_argument(
            "--template-dir",
            help="render this directory of *.tmpl files against the IR "
                 "(use with --target template)")

    p_phase = sub.add_parser(
        "phase", help="check definition- and implementation-phase readiness"
    )
    p_phase.add_argument("path")
    p_phase.add_argument("--binding")
    p_phase.add_argument("--target")
    p_phase.add_argument("--platform-policy", dest="platform_policy",
                         help="the fabric's house rules to judge against")
    p_phase.add_argument("--scaffold", action="store_true",
                         help="print the spec blocks that answer each failing "
                              "check, commented and ready to fill in")
    p_phase.add_argument("-o", "--out", dest="out",
                         help="for --scaffold: write to this file")

    p_sched = sub.add_parser("schedule", help="preview when triggers fire")
    p_sched.add_argument("path")
    p_sched.add_argument("--binding")
    p_sched.add_argument("--count", type=int, default=3)
    p_sched.add_argument("--simulate-days", type=int, default=0)

    p_run_sched = sub.add_parser("scheduler", help="run the trigger scheduler")
    p_run_sched.add_argument("--manifest", default="triggers.json")
    p_run_sched.add_argument("--db", dest="sched_db", default="orgagents.db")
    p_run_sched.add_argument("--once", action="store_true",
                             help="fire what is due now, then exit")

    p_mis = sub.add_parser("missions", help="short-lived teams and their windows")
    p_mis.add_argument("path")
    p_mis.add_argument("action", nargs="?", default="list",
                       choices=["list", "sweep"])
    p_mis.add_argument("--on", help="evaluate as of this ISO date (default: today)")
    p_mis.add_argument("--write", action="store_true",
                       help="for 'sweep': write the closed missions back to the spec")

    p_cat2 = sub.add_parser("catalogs", help="the platform catalog of building blocks")
    p_cat2.add_argument("action", choices=["list", "seed", "add", "edit",
                                           "review", "approve", "send-back",
                                           "retire", "delete", "stats",
                                           "models", "refresh", "stale",
                                           "usage"])
    p_cat2.add_argument("args", nargs="*")
    p_cat2.add_argument("--kind")
    p_cat2.add_argument("--file", help="for 'add': a JSON entry, or a list of them")
    p_cat2.add_argument("--attr", action="append", default=[],
                        help="kind attribute as key=value; key[]=value repeats")
    p_cat2.add_argument("--name", default="")
    p_cat2.add_argument("--summary", default="")
    p_cat2.add_argument("--description", default="")
    p_cat2.add_argument("--owner", default="")
    p_cat2.add_argument("--docs", default="", help="documentation URL")
    p_cat2.add_argument("--tag", action="append", default=[])
    p_cat2.add_argument("--version", default="")
    p_cat2.add_argument("--status", default="", help="for 'review'")
    p_cat2.add_argument("--note", default="")
    p_cat2.add_argument("--superseded-by", dest="superseded_by", default="")
    p_cat2.add_argument("--force", action="store_true",
                        help="for 'retire': break the designs using it")
    p_cat2.add_argument("--actor", default="cli", help="who is acting, recorded")

    p_ten = sub.add_parser("tenants", help="fabric tenants and their lifecycle")
    p_ten.add_argument("action", choices=["list", "show", "register", "suspend",
                                          "resume", "retire"])
    p_ten.add_argument("args", nargs="*", help="for everything but 'list': <tenant_id>")
    p_ten.add_argument("--name", default="", help="human-readable tenant name")
    p_ten.add_argument("--prefix", help="namespace prefix (default: the tenant id)")
    p_ten.add_argument("--entitlement", action="append", default=[])
    p_ten.add_argument("--cloud-boundary", default="",
                       help="the project/account/subscription cloud targets deploy into")
    p_ten.add_argument("--reason", default="", help="recorded in the operator audit log")
    p_ten.add_argument("--actor", default="cli", help="who is acting, for the audit log")

    p_eval = sub.add_parser("evaluate", help="run the declared evaluation cases")
    p_eval.add_argument("path")
    p_eval.add_argument("--agent")
    p_eval.add_argument("--stage")
    p_eval.add_argument("--db", dest="eval_db", default="orgagents.db")

    p_gate = sub.add_parser("gate", help="print the evaluation gate per agent")
    p_gate.add_argument("path")
    p_gate.add_argument("--stage")
    p_gate.add_argument("--db", dest="gate_db", default="orgagents.db")

    p_rec = sub.add_parser("records", help="ADR and workstream record governance")
    p_rec.add_argument("action", choices=["validate", "index", "graph", "new", "list"])
    p_rec.add_argument("args", nargs="*", help="for 'new': <adr|ws> <title>")
    p_rec.add_argument("--root", default=".")

    p_cat = sub.add_parser("catalog", help="search the marketplace")
    p_cat.add_argument("query", nargs="?", default="")
    p_cat.add_argument("--kind")

    p_ex = sub.add_parser(
        "examples", help="list the shipped example organisations, or load one "
                         "into the designer")
    p_ex.add_argument("action", choices=["list", "load", "import"])
    p_ex.add_argument("example", nargs="?", default="",
                      help="for 'load': an id from 'examples list', or 'all'; "
                           "for 'import': the path of a *.system.yaml on disk")
    p_ex.add_argument("--user", default="",
                      help="for 'load': who owns it — the name you use in the "
                           "designer's 'as' box, since a workspace is only "
                           "visible to its members")
    p_ex.add_argument("--workspace", default="",
                      help="for 'load': an existing workspace id; default is a "
                           "new workspace named after the example")
    p_ex.add_argument("--name", default="",
                      help="for 'load': a name for the design")

    p_mm = sub.add_parser(
        "metamodel", help="print the UML metamodel (ADR-0101) as PlantUML, or "
                          "regenerate docs/metamodel")
    p_mm.add_argument("action", choices=["model", "profile", "diagram",
                                         "docs", "check", "scenarios",
                                         "trace", "transformation"],
                      help="model/profile: print PlantUML; diagram: write "
                           "docs/metamodel/*.puml; docs: write the profile "
                           "reference (one page per profile) and the "
                           "diagrams (ADR-0112); check: validate a spec "
                           "against the model's constraints; scenarios: play "
                           "and write docs/metamodel/scenarios")
    p_mm.add_argument("spec", nargs="?", default="",
                      help="for 'check': the spec file")
    p_mm.add_argument("--out", default="docs/metamodel")

    p_ds = sub.add_parser(
        "designer", help="the designer's specification (ADR-0103)")
    p_ds.add_argument("action", choices=["gestures"])
    p_ds.add_argument("--out", default="docs/designer")

    # The public contract (ADR-0115): the OpenAPI document, and MCP servers
    # that act through it as a named designer user.
    p_api = sub.add_parser("api", help="the public HTTP API contract")
    p_api.add_argument("action", choices=["schema"],
                       help="schema: write the OpenAPI document")
    p_api.add_argument("--out", default="",
                       help="default docs/api/openapi.json; '-' prints it")
    p_mcp = sub.add_parser("mcp", help="run an MCP server (needs the 'mcp' extra)")
    from .mcp_server import add_arguments as _mcp_arguments
    _mcp_arguments(p_mcp)

    # Typed exchange and the relational store (ADR-0113).
    p_export = sub.add_parser(
        "export", help="write a spec or binding as YAML or JSON in which "
                       "every element names its UML type (ADR-0113)")
    p_export.add_argument("file", nargs="?", default="",
                          help="a *.system.yaml or *.binding.yaml (typed or "
                               "not); or use --system")
    p_export.add_argument("--system", default="",
                          help="a design's id in the designer database "
                               "(ORGAGENTS_DATABASE_URL)")
    p_export.add_argument("--part", choices=["spec", "binding"],
                          default="spec", help="with --system: which part")
    p_export.add_argument("--format", choices=["yaml", "json"],
                          default="yaml")
    p_export.add_argument("--untyped", action="store_true",
                          help="leave the types out (the pre-ADR-0113 form)")
    p_export.add_argument("--schema", action="store_true",
                          help="write the typed format's JSON Schema instead")
    p_export.add_argument("-o", "--out", default="-",
                          help="the file to write; '-' prints it")
    p_import = sub.add_parser(
        "import", help="read a typed or untyped spec or binding, check every "
                       "type against where it sits, and write it back")
    p_import.add_argument("file")
    p_import.add_argument("--format", choices=["yaml", "json"],
                          default="yaml")
    p_import.add_argument("--untyped", action="store_true",
                          help="write it without types")
    p_import.add_argument("-o", "--out", default="-")
    p_db = sub.add_parser(
        "db", help="the designer's PostgreSQL database (ADR-0113)")
    p_db.add_argument("action", choices=["migrate", "ddl",
                                         "generate-migration",
                                         "migrate-from-sqlite", "query"])
    p_db.add_argument("args", nargs="*",
                      help="generate-migration: a name; migrate-from-sqlite: "
                           "the SQLite designer.db; query: q1..q5 and its "
                           "parameters as name=value")
    p_db.add_argument("--database-url", default="",
                      help="default: ORGAGENTS_DATABASE_URL")
    p_db.add_argument("--once", action="store_true",
                      help="migrate-from-sqlite: do nothing if this file was "
                           "imported before")

    args = parser.parse_args(argv)

    if args.cmd in ("export", "import"):
        return _exchange_command(args)

    if args.cmd == "db":
        return _db_command(args)

    if args.cmd == "api":
        from .api_contract import SCHEMA_PATH, schema_text
        if args.out == "-":
            sys.stdout.write(schema_text())
            return 0
        out = Path(args.out) if args.out else SCHEMA_PATH
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(schema_text(), encoding="utf-8", newline="\n")
        print(f"wrote {out}")
        return 0

    if args.cmd == "mcp":
        from .mcp_server import main as _mcp_main
        return _mcp_main(args)

    if args.cmd == "designer":
        from .designer.gestures import catalogue
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "gestures.md").write_text(catalogue())
        print(f"wrote {out}/gestures.md")
        return 0

    if args.cmd == "metamodel":
        from .metamodel import to_plantuml, to_plantuml_ownership, to_plantuml_profile
        if args.action == "model":
            print(to_plantuml(), end="")
        elif args.action == "profile":
            print(to_plantuml_profile(), end="")
        elif args.action == "check":
            from .metamodel.constraints import check
            from .spec.loader import load_spec
            found = check(load_spec(args.spec))
            for v in found:
                print(v)
            print(f"{len(found)} violation(s)")
            return 1 if found else 0
        elif args.action == "trace":
            import tempfile

            from .compiler.engine import compile_system
            from .metamodel.transformation import trace, trace_targets
            from .spec.loader import load_spec
            spec = load_spec(args.spec)
            results = compile_system(spec, out_dir=tempfile.mkdtemp(),
                                     write=False)
            t = trace(spec, results[0].ir)
            print(f"{len(t.elements)} elements and {len(t.links)} links "
                  f"traced into the IR; {len(t.not_built)} not built:")
            for ref, why in sorted(t.not_built.items()):
                print(f"  {ref}: {why}")
            gaps = list(t.gaps)
            for r in results:
                gaps += trace_targets(r.ir, r.files, r.target)
            for g in gaps:
                print(f"GAP {g}")
            print("complete" if not gaps else f"{len(gaps)} gap(s)")
            return 1 if gaps else 0
        elif args.action == "docs":
            from .metamodel.reference import write
            written = write(Path(args.out))
            print(f"wrote {len(written)} files to {args.out}: "
                  + ", ".join(p.name for p in written))
            return 0
        elif args.action == "transformation":
            from .metamodel.transformation import describe
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / "transformation.md").write_text(describe())
            print(f"wrote {out}/transformation.md")
            return 0
        elif args.action == "scenarios":
            from .metamodel.scenarios import SCENARIOS, catalogue, play, to_object_diagram
            out = Path(args.out)
            (out / "scenarios").mkdir(parents=True, exist_ok=True)
            failed = 0
            for sc in SCENARIOS:
                played = play(sc)
                failed += bool(played.failures)
                (out / "scenarios" / f"{sc.id}.puml").write_text(
                    to_object_diagram(played))
                for f in played.failures:
                    print(f"{sc.id}: {f}")
            (out / "scenarios.md").write_text(catalogue())
            print(f"{len(SCENARIOS) - failed}/{len(SCENARIOS)} scenarios hold;"
                  f" wrote {out}/scenarios.md and scenarios/*.puml")
            return 1 if failed else 0
        else:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / "orgagents-model.puml").write_text(to_plantuml())
            (out / "orgagents-profile.puml").write_text(to_plantuml_profile())
            (out / "orgagents-ownership.puml").write_text(
                to_plantuml_ownership())
            print(f"wrote {out}/orgagents-model.puml, orgagents-profile.puml "
                  "(render with PlantUML)")
        return 0

    if args.cmd == "examples":
        return _examples_command(args)

    if args.cmd == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(args.db, args.base_url), host=args.host, port=args.port)
        return 0

    if args.cmd == "worker":
        from .runtime.worker import serve as serve_worker

        db = args.db if args.db != "orgagents.db" else ""
        return serve_worker(args.agent_id, host=args.host, port=args.port,
                            ir_path=args.ir, db=db)

    if args.cmd == "bus-init":
        import json as _json
        import os as _os

        from .runtime.agent_bus import bus_init

        ir = _json.loads(Path(args.ir).read_text(encoding="utf-8"))
        return bus_init(ir, url=_os.environ.get("ORGAGENTS_BUS_URL", "nats://nats:4222"),
                        user=_os.environ.get("ORGAGENTS_BUS_ADMIN_USER", ""),
                        password=_os.environ.get("ORGAGENTS_BUS_ADMIN_PASSWORD", ""),
                        attempts=args.attempts)

    if args.cmd == "providers":
        return _providers_command(args)

    if args.cmd in ("evaluate", "gate"):
        return _evaluation_command(args)

    if args.cmd in ("spec", "compile", "targets", "phase", "schedule", "missions"):
        return _compiler_command(args)

    if args.cmd == "scheduler":
        return _scheduler_command(args)

    if args.cmd == "catalogs":
        return _catalogs_command(args)

    if args.cmd == "tenants":
        return _tenants_command(args)

    if args.cmd == "records":
        return _records_command(args)

    if args.cmd == "seed":
        from .seed import seed

        seed(args.db, args.base_url)
        return 0

    platform = Platform(args.db, base_url=args.base_url)
    if args.cmd == "tree":
        print(json.dumps(platform.org.to_tree(), indent=2))
    elif args.cmd == "metrics":
        print(json.dumps(platform.obs.metrics(), indent=2))
    elif args.cmd == "run":
        result = platform.runtime.run(args.agent_id, args.prompt, created_by="cli")
        print(result.output)
        print(f"\nsession: {result.session_url} [{result.state.value}]")
    elif args.cmd == "catalog":
        for e in platform.catalog.search(args.query, kind=args.kind):
            print(f"{e.kind:16} {e.name:32} installs={e.installs} {e.summary[:60]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
