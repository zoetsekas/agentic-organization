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

    if not getattr(args, "path", None):
        print(f"error: {args.cmd} needs a path to a system spec")
        return 2
    spec = load_spec(args.path)
    binding = load_binding(args.binding) if getattr(args, "binding", None) else None

    if args.cmd == "phase":
        from .phases import review

        report = review(spec, binding=binding, target=args.target)
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
        if args.action == "validate":
            # Without --directory the reconciliation is inert by construction:
            # NullDirectory knows nobody and produces no findings, so behaviour
            # is unchanged for anyone who does not supply one (ADR-0047).
            directory = None
            if getattr(args, "directory", None):
                from .directory import StaticDirectory

                directory = StaticDirectory.from_file(args.directory)
            findings = validate_spec(spec, directory=directory)
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
                      f"env={agent.environment.id if agent.environment else '-':16} "
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
        )
    except (CompileError, TenantIsolationError) as e:
        print(f"error: {e}")
        return 1
    for result in results:
        print(result.summary())
    return 0


def _catalogs_command(args: argparse.Namespace) -> int:
    from .catalogs import ApprovalStatus, CatalogKind, CatalogService, seed_catalog
    from .platform import Platform

    platform = Platform(args.db, configure_logs=False)
    service = CatalogService(platform.store)

    if args.action == "seed":
        print(f"published {seed_catalog(service)} entries")
        return 0
    if args.action == "stats":
        print(json.dumps(service.stats(), indent=2))
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
                        choices=["validate", "ir", "show", "migrate", "schema"])
    p_spec.add_argument("path", nargs="?")
    p_spec.add_argument("--binding")
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
    p_cat2.add_argument("action", choices=["list", "seed", "approve", "stats",
                                           "models", "refresh", "stale",
                                           "usage"])
    p_cat2.add_argument("args", nargs="*")
    p_cat2.add_argument("--kind")

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
