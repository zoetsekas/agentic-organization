"""MCP servers over the public API: `orgagents mcp designer|runtime` (ADR-0115).

Needs the optional extra: ``pip install -e ".[mcp]"``. The package is named
``mcp_server`` rather than ``mcp`` so it never shadows the SDK it imports.
"""
from __future__ import annotations

import argparse
import sys


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("server", choices=["designer", "runtime"])
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="stdio (one user, from ORGAGENTS_USER) or "
                             "streamable HTTP (bearer token per request)")
    parser.add_argument("--url", default=None,
                        help="the API to act through (default ORGAGENTS_API_URL "
                             "or http://127.0.0.1:8000)")
    parser.add_argument("--in-process", dest="in_process", default=None,
                        metavar="DB",
                        help="instead of --url, run the API in this process "
                             "over this SQLite store")
    parser.add_argument("--host", default="127.0.0.1",
                        help="HTTP transport bind address")
    parser.add_argument("--port", type=int, default=8765,
                        help="HTTP transport port")
    parser.add_argument("--allow-mutations", action="store_true",
                        help="runtime only: offer run_agent, resume_session "
                             "and ack_alert")


def main(args: argparse.Namespace) -> int:
    try:
        from ._common import ConfigurationError, ServerConfig, serve
    except ImportError as e:            # the extra is not installed
        print(f"the MCP servers need the 'mcp' extra: pip install "
              f"'orgagents[mcp]' ({e})", file=sys.stderr)
        return 2
    try:
        config = ServerConfig.from_env(
            transport=args.transport, api_url=args.url, db=args.in_process,
            allow_mutations=args.allow_mutations or None)
        config.check()
    except (ConfigurationError, ValueError) as e:
        print(f"orgagents mcp {args.server}: {e}", file=sys.stderr)
        return 2
    if args.server == "designer":
        from .designer import build_designer_server as build
    else:
        from .runtime import build_runtime_server as build
    serve(build(config), config, host=args.host, port=args.port)
    return 0
