"""Command line entry point: ``orgagents <command>``."""
from __future__ import annotations

import argparse
import json
import sys

from .platform import Platform


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

    if args.cmd in ("spec", "compile", "targets"):
        return _compiler_command(args)

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
