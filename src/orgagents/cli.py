"""Command line entry point: ``orgagents <command>``."""
from __future__ import annotations

import argparse
import json
import sys

from .platform import Platform


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

    p_cat = sub.add_parser("catalog", help="search the marketplace")
    p_cat.add_argument("query", nargs="?", default="")
    p_cat.add_argument("--kind")

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(args.db, args.base_url), host=args.host, port=args.port)
        return 0

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
