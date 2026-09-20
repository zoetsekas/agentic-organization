#!/usr/bin/env python3
"""The alpha smoke test: does any of this actually run?

Everything in this repository is generated, parsed and unit-tested, and none of
it has ever been started — there is no Docker daemon where it was written. This
script is the check that closes that gap, and it is written to be run by a
human on a machine that has one.

It is deliberately loud about *which* step failed, because the useful
information on the first run is not "it broke" but "it got this far".

    python scripts/smoke.py [--keep]

Exit code 0 means: the designer answered, a system compiled, the generated
stack parsed, a tenant stack started, and an agent produced a result.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "examples" / "acme.system.yaml"
BINDING = ROOT / "examples" / "acme.binding.yaml"
OUT = ROOT / "build" / "smoke"
BASE = "http://localhost:8000"

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
_results: list[tuple[str, bool, str]] = []


def step(name: str):
    def wrap(fn):
        def run(*a, **kw):
            print(f"{DIM}··{RESET} {name} ", end="", flush=True)
            try:
                detail = fn(*a, **kw) or ""
            except Exception as exc:                     # noqa: BLE001
                print(f"{RED}FAILED{RESET}\n   {exc}")
                _results.append((name, False, str(exc)))
                raise SystemExit(1) from exc
            print(f"{GREEN}ok{RESET} {DIM}{detail}{RESET}")
            _results.append((name, True, detail))
        return run
    return wrap


def sh(*args: str, cwd: Path = ROOT, timeout: int = 900) -> str:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-15:]
        raise RuntimeError(" ".join(args) + "\n   " + "\n   ".join(tail))
    return proc.stdout


def get(path: str, timeout: int = 10):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


@step("docker is available")
def check_docker() -> str:
    if not shutil.which("docker"):
        raise RuntimeError("the docker CLI is not installed")
    out = sh("docker", "version", "--format", "{{.Server.Version}}", timeout=30)
    return f"server {out.strip()}"


@step("the spec validates")
def check_spec() -> str:
    out = sh(sys.executable, "-m", "orgagents.cli", "spec", "validate", str(SPEC))
    return out.strip().splitlines()[-1]


@step("the system compiles for the local target")
def compile_system() -> str:
    if OUT.exists():
        shutil.rmtree(OUT)
    sh(sys.executable, "-m", "orgagents.cli", "compile", str(SPEC),
       "--binding", str(BINDING), "--target", "local", "--out", str(OUT), "--force")
    files = list(OUT.rglob("docker-compose*.y*ml"))
    if not files:
        raise RuntimeError("no Compose file was generated")
    return f"{len(files)} Compose file(s)"


@step("docker parses every generated Compose file")
def parse_compose() -> str:
    count = 0
    for path in OUT.rglob("docker-compose*.y*ml"):
        sh("docker", "compose", "-f", str(path), "config", "-q", "--no-interpolate",
           cwd=path.parent, timeout=120)
        count += 1
    return f"{count} parsed"


@step("the designer image builds")
def build_designer() -> str:
    sh("docker", "compose", "build", timeout=1800)
    return "built"


@step("the designer starts and answers /healthz")
def start_designer() -> str:
    sh("docker", "compose", "up", "-d", timeout=600)
    last = ""
    for _ in range(60):
        try:
            get("/healthz", timeout=5)
            return "healthy"
        except Exception as exc:                          # noqa: BLE001
            last = str(exc)
            time.sleep(2)
    logs = sh("docker", "compose", "logs", "--tail=40", "designer", timeout=60)
    raise RuntimeError(f"never became healthy ({last})\n   {logs}")


@step("the designer UI is served")
def check_ui() -> str:
    with urllib.request.urlopen(BASE + "/ui/", timeout=10) as r:
        if r.status != 200:
            raise RuntimeError(f"/ui/ returned {r.status}")
    return "/ui/ 200"


@step("the demo organization seeds and an agent runs")
def run_agent() -> str:
    sh("docker", "compose", "exec", "-T", "designer",
       "python", "-m", "orgagents.cli", "--db", "/data/designer.db", "seed", timeout=300)
    out = sh("docker", "compose", "exec", "-T", "designer",
             "python", "-m", "orgagents.cli", "--db", "/data/designer.db",
             "run", "analyst", "Summarize open invoices.", timeout=600)
    if "completed" not in out.lower():
        raise RuntimeError(f"the run did not complete:\n   {out.strip()[-400:]}")
    return "analyst completed a session"


@step("the tenant stack starts")
def start_tenant() -> str:
    path = next(OUT.rglob("docker-compose*.y*ml"))
    sh("docker", "compose", "-f", str(path), "up", "-d", "--build",
       cwd=path.parent, timeout=1800)
    out = sh("docker", "compose", "-f", str(path), "ps", "--format", "json",
             cwd=path.parent, timeout=120)
    running = sum(1 for line in out.splitlines() if line.strip())
    if not running:
        raise RuntimeError("no services are running")
    return f"{running} service(s)"


def teardown() -> None:
    print(f"{DIM}·· tearing down{RESET}")
    subprocess.run(["docker", "compose", "down", "-v"], cwd=ROOT,
                   capture_output=True, timeout=300)
    for path in OUT.rglob("docker-compose*.y*ml"):
        subprocess.run(["docker", "compose", "-f", str(path), "down", "-v"],
                       cwd=path.parent, capture_output=True, timeout=300)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep", action="store_true",
                    help="leave the stacks running afterwards")
    ap.add_argument("--skip-tenant", action="store_true",
                    help="stop after the designer; do not start the tenant stack")
    args = ap.parse_args()

    print(f"\n{YELLOW}alpha smoke test{RESET} — nothing here has run before; "
          f"expect to fix something\n")
    try:
        check_docker()
        check_spec()
        compile_system()
        parse_compose()
        build_designer()
        start_designer()
        check_ui()
        run_agent()
        if not args.skip_tenant:
            start_tenant()
    finally:
        passed = sum(1 for _, ok, _ in _results if ok)
        print(f"\n{passed}/{len(_results)} steps passed")
        if not args.keep:
            teardown()
        else:
            print(f"{DIM}·· left running (--keep). `make down` to stop.{RESET}")
    print(f"\n{GREEN}alpha smoke passed{RESET}: the designer runs, a system "
          f"compiles, and an agent completed a session.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
