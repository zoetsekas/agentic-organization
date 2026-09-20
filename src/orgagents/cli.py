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


def _compiler_command(args: argparse.Namespace) -> int:
    from .compiler import build_ir, compile_system
    from .compiler.base import register_builtin_targets
    from .compiler.engine import CompileError
    from .spec import load_binding, load_spec, validate_spec

    if args.cmd == "targets":
        for description in register_builtin_targets().describe_all():
            print(f"{description['id']:18} {description['title']}")
            print(f"{'':18} {description['summary']}")
            for caveat in description.get("caveats", []):
                print(f"{'':18} ! {caveat}")
        return 0

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
            findings = validate_spec(spec)
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

    try:
        results = compile_system(
            spec,
            targets=args.targets,
            out_dir=args.out,
            binding=binding,
            force=args.force,
        )
    except CompileError as e:
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
                  f"{attributes.get('context_tokens', 0):>8} ctx")
        return 0
    kind = CatalogKind(args.kind) if args.kind else None
    for entry in service.search(" ".join(args.args), kind=kind):
        print(f"{entry.kind.value:22} {entry.name:34} {entry.status.value:11} "
              f"{entry.summary[:52]}")
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
    p_spec.add_argument("action", choices=["validate", "ir", "show"])
    p_spec.add_argument("path")
    p_spec.add_argument("--binding")
    p_spec.add_argument("--target", default="local")

    p_comp = sub.add_parser("compile", help="generate code and infrastructure")
    p_comp.add_argument("path")
    p_comp.add_argument("--target", action="append", dest="targets")
    p_comp.add_argument("--binding")
    p_comp.add_argument("--out", default="build")
    p_comp.add_argument("--force", action="store_true")

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
                                           "models"])
    p_cat2.add_argument("args", nargs="*")
    p_cat2.add_argument("--kind")

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
