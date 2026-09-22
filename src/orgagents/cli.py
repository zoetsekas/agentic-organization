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

    manifest = json.loads(Path(args.manifest).read_text())
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
    source = Path(args.path).read_text()
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
    text = starter_spec(name, owner=getattr(args, "owner", "") or "")
    out = Path(args.out) if args.out else Path(f"{name}.system.yaml")
    if out.exists() and not args.force:
        print(f"error: {out} exists; pass --force to overwrite")
        return 1
    out.write_text(text)
    print(f"wrote {out}")
    print(f"  next: orgagents spec validate {out}")
    print(f"        orgagents phase {out} --target local --scaffold")
    return 0


def _compiler_command(args: argparse.Namespace) -> int:
    from .compiler import build_ir, compile_system
    from .compiler.base import register_builtin_targets
    from .compiler.engine import CompileError
    from .compiler.tenancy import TenantIsolationError
    from .spec import load_binding, load_spec, validate_spec

    if args.cmd == "targets":
        for description in register_builtin_targets().describe_all():
            print(f"{description['id']:18} {description['title']}")
            print(f"{'':18} {description['summary']}")
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
                                target=args.target)
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

        from .scheduling import describe, next_fire_times
        from .runtime.scheduler import SchedulerService

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
        payload = _json.loads(Path(args.file).read_text())
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
    from .designer.audit import AuditOutcome
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orgagents")
    parser.add_argument("--db", default="orgagents.db")
    parser.add_argument("--base-url", default="http://localhost:8000")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed", help="populate the demo organization")

    p_serve = sub.add_parser("serve", help="run the API and designer UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

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

    sub.add_parser("targets", help="list available deployment targets")

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

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(args.db, args.base_url), host=args.host, port=args.port)
        return 0

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
