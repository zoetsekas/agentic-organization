#!/usr/bin/env python3
"""AYC on this workstation: compile, build, start, stop (ADR-0109).

    python examples/ayc/local_stack.py up        # env + compile + wheel + build + start
    python examples/ayc/local_stack.py e2e       # the purchase-to-pay scenario
    python examples/ayc/local_stack.py e2e-messaging   # agents delegating over NATS
    python examples/ayc/local_stack.py --project ayc-msg --port-base 19000 up
                                                 # a second copy beside the first
    python examples/ayc/local_stack.py ps
    python examples/ayc/local_stack.py down      # stop (add --volumes to wipe)
    python examples/ayc/local_stack.py rotate-approval-key   # new issuer key

`make ayc-up` / `ayc-e2e` / `ayc-down` call this; it exists as a script so the
same steps run where there is no `make` (Windows, by default).

The stack is the generated one — examples/ayc/generated/local, compiled from
ayc.system.yaml with ayc.local.binding.yaml — plus the overlays in its
`overlays/` folder, under the Compose project `ayc-local`, so it can sit next
to the designer (port 8000) without either touching the other.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path


def _utf8_console() -> None:
    """Print box-drawing and arrows on a Windows console without PYTHONUTF8=1.

    A cp1252 console cannot encode them, and a traceback after the work is
    done hides whether it passed. Reconfigured, not replaced, so a stream that
    is not a text console (a pipe under a test runner) is left as it is.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

ROOT = Path(__file__).resolve().parents[2]
AYC = ROOT / "examples" / "ayc"
OUT = AYC / "generated"
STACK = OUT / "local"
PROJECT = os.environ.get("AYC_PROJECT", "ayc-local")

#: Host ports, by what they publish, relative to a base (default 18000). A
#: second copy of the stack runs beside the first with `--project` and
#: `--port-base`; the overlay reads these as `${AYC_PORT_<NAME>}`.
PORT_BASE = int(os.environ.get("AYC_PORT_BASE", "18000"))
PORT_OFFSETS = {"DESIGNER": 0, "CHAT": 80, "SHOPIFY": 101, "FISHBOWL": 102,
                "ACCOUNTING": 103, "CMS": 104, "DEPLOY_PIPELINE": 105, "LANGFLOW": -140}


def ports(base: int) -> dict[str, int]:
    return {name: base + off for name, off in PORT_OFFSETS.items()}


def stack_env(project: str, base: int) -> dict[str, str]:
    """What compose needs to run this copy: its ports, and an image prefix of
    its own unless it is the default stack, so building one copy never
    re-tags the images another is running."""
    env = {f"AYC_PORT_{k}": str(v) for k, v in ports(base).items()}
    if project != "ayc-local":
        env["AYC_IMAGE_PREFIX"] = project
    return env

#: Names the stack needs values for. Values are generated per checkout, into
#: a `.env` git ignores; nothing here is a real credential for anything.
ENV_NAMES = ["STATE_PASSWORD", "ARTIFACTS_USER", "ARTIFACTS_PASSWORD",
             "FISHBOWL_READ_TOKEN", "FISHBOWL_PURCHASING_TOKEN",
             "FISHBOWL_AUDIT_TOKEN", "FISHBOWL_DOCK_TOKEN", "LANGFLOW_SECRET_KEY"]

#: The approval issuer's keypair (ADR-0114). The private key goes to the chat
#: -- the issuer -- only; the public keys (current first, then any still
#: trusted after a rotation) go to every worker, which can verify and never
#: sign.
SIGNING_KEY = "ORGAGENTS_APPROVAL_SIGNING_KEY"
PUBLIC_KEYS = "ORGAGENTS_APPROVAL_PUBLIC_KEYS"
#: What earlier versions wrote and a worker must no longer be able to read:
#: ADR-0114 v1.0's HMAC secrets, and ADR-0118 v1.0's broker passwords
#: (replaced by NKeys, whose public halves are all the broker holds).
RETIRED = ("ORGAGENTS_APPROVAL_SECRET", "ORGAGENTS_APPROVAL_KEY_",
           "ORGAGENTS_BUS_PASSWORD_", "ORGAGENTS_BUS_ADMIN_PASSWORD")


def _suffix(ident: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in ident).upper()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def new_keypair() -> tuple[str, str]:
    """(`kid:private`, `kid:public`), Ed25519, raw 32-byte keys base64url --
    the format orgagents.security.service_auth reads (a test holds the two
    together). Needs `cryptography`, which the repo's venv has."""
    import time

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    kid = time.strftime("k%Y%m%d") + "-" + secrets.token_hex(3)
    key = Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw,
                                        serialization.PublicFormat.Raw)
    return f"{kid}:{_b64(priv)}", f"{kid}:{_b64(pub)}"


def read_env() -> dict[str, str]:
    have: dict[str, str] = {}
    path = STACK / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                have[k] = v
    return have


def write_env(have: dict[str, str]) -> None:
    (STACK / ".env").write_text(
        "# Generated by examples/ayc/local_stack.py for this checkout only.\n"
        "# Never committed (.gitignore: .env). Values are random.\n"
        + "".join(f"{k}={v}\n" for k, v in have.items()), encoding="utf-8")


def compiled() -> dict:
    return json.loads((STACK / "system.ir.json").read_text(encoding="utf-8"))


def per_stack_names(ir: dict) -> list[str]:
    """Names that depend on the design (ADR-0114): a service token per worker
    and a sign-in passcode per person the chat may sign in."""
    return ([f"ORGAGENTS_WORKER_TOKEN_{_suffix(a['id'])}" for a in ir["agents"]]
            + [f"CHAT_PASSCODE_{_suffix(p['id'])}" for p in ir.get("people", [])])


def _nkey_module():
    """orgagents.security.nkey, loaded by path: this script runs from the
    repo's venv without importing the platform package."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_orgagents_nkey", ROOT / "src" / "orgagents" / "security" / "nkey.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ensure_bus_nkeys(have: dict[str, str], ir: dict) -> list[str]:
    """An NKey per agent and one for the bus operator (ADR-0118 v1.1): the
    seed (`ORGAGENTS_BUS_SEED_<AGENT>`) is given to that agent's worker only,
    the public key (`ORGAGENTS_BUS_NKEY_<AGENT>`) to the broker, and the list
    of every agent's public key (`ORGAGENTS_BUS_PUBLIC_KEYS`) to every worker
    to verify signed hops with. Returns the names it added."""
    nk = _nkey_module()
    added: list[str] = []
    for ident, seed_name, pub_name in (
            [("orgagents_bus_admin", "ORGAGENTS_BUS_ADMIN_SEED", "ORGAGENTS_BUS_ADMIN_NKEY")]
            + [(a["id"], f"ORGAGENTS_BUS_SEED_{_suffix(a['id'])}",
                f"ORGAGENTS_BUS_NKEY_{_suffix(a['id'])}") for a in ir["agents"]]):
        if not have.get(seed_name):
            have[seed_name], _ = nk.create_user()
            added.append(seed_name)
        public = nk.public_of(have[seed_name])
        if have.get(pub_name) != public:
            have[pub_name] = public
            added.append(pub_name)
    listing = ",".join(a["id"] + ":" + have["ORGAGENTS_BUS_NKEY_" + _suffix(a["id"])]
                       for a in ir["agents"])
    if have.get("ORGAGENTS_BUS_PUBLIC_KEYS") != listing:
        have["ORGAGENTS_BUS_PUBLIC_KEYS"] = listing
        added.append("ORGAGENTS_BUS_PUBLIC_KEYS")
    return added


def run(*args: str, cwd: Path = ROOT, check: bool = True, **kw) -> subprocess.CompletedProcess:
    print("$ " + " ".join(args), flush=True)
    return subprocess.run(args, cwd=cwd, check=check, **kw)


def compose(*args: str, check: bool = True, **kw) -> subprocess.CompletedProcess:
    files = ["-f", "docker-compose.yaml"]
    for overlay in sorted((STACK / "overlays").glob("*.y*ml")):
        files += ["-f", f"overlays/{overlay.name}"]
    # More compose files for this copy only (e.g. image names of its own),
    # from outside the tree: AYC_COMPOSE_EXTRA, os.pathsep-separated.
    for extra in filter(None, os.environ.get("AYC_COMPOSE_EXTRA", "").split(os.pathsep)):
        files += ["-f", extra]
    env = {**os.environ, **stack_env(PROJECT, PORT_BASE)}
    return run("docker", "compose", "-p", PROJECT, *files, *args, cwd=STACK,
               check=check, env=env, **kw)


def cmd_env(_: argparse.Namespace) -> None:
    path = STACK / ".env"
    existed = path.exists()
    have = read_env()
    ir = compiled()
    added = [n for n in ENV_NAMES + per_stack_names(ir) if not have.get(n)]
    for name in added:
        have[name] = "ayc" if name == "ARTIFACTS_USER" else secrets.token_urlsafe(18)
    # The symmetric secret and per-agent HMAC keys of ADR-0114 v1.0 are gone:
    # anything that could read a worker's env could sign with them.
    retired = [k for k in have if k.startswith(RETIRED)]
    for k in retired:
        del have[k]
    if not have.get(SIGNING_KEY):
        have[SIGNING_KEY], have[PUBLIC_KEYS] = new_keypair()
        added += [SIGNING_KEY, PUBLIC_KEYS]
    added += ensure_bus_nkeys(have, ir)
    if added or retired or not existed:
        write_env(have)
    print(f"{path.relative_to(ROOT)}: {len(have)} values ({len(added)} new"
          + (f", {len(retired)} retired" if retired else "") + ")")


def cmd_rotate(args: argparse.Namespace) -> None:
    """A new issuer key. The chat signs with it from its next start; workers
    trust it *and* the previous one (so a release minted a moment ago still
    lands) until the next rotation drops it. Restart the stack to apply."""
    have = read_env()
    if not have.get(SIGNING_KEY):
        raise SystemExit("no approval key yet; run `env` first")
    signing, public = new_keypair()
    keep = [k for k in have.get(PUBLIC_KEYS, "").split(",") if k][: max(args.keep, 0)]
    have[SIGNING_KEY] = signing
    have[PUBLIC_KEYS] = ",".join([public] + keep)
    write_env(have)
    print(f"approval key rotated to {signing.split(':', 1)[0]}; workers trust "
          + ", ".join(k.split(":", 1)[0] for k in have[PUBLIC_KEYS].split(","))
          + ". Run `up --no-generate` to apply.")


def cmd_generate(_: argparse.Namespace) -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}
    run(sys.executable, "-m", "orgagents.cli", "compile",
        "examples/ayc/ayc.system.yaml", "--binding", "examples/ayc/ayc.local.binding.yaml",
        "--target", "local", "--out", "examples/ayc/generated", "--force", env=env)


def cmd_wheel(_: argparse.Namespace) -> None:
    wheels = STACK / "wheels"
    for old in wheels.glob("orgagents-*.whl"):
        old.unlink()
    run(sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps", "--quiet",
        "-w", str(wheels))
    # pip leaves its build tree next to the source; it is not ours to keep.
    shutil.rmtree(ROOT / "build" / "lib", ignore_errors=True)


def passcodes() -> list[tuple[str, str, str]]:
    """(person id, name, passcode) for everyone who can sign in to the chat."""
    env = {}
    for line in (STACK / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return [(p["id"], p.get("name", p["id"]), env.get(f"CHAT_PASSCODE_{_suffix(p['id'])}", ""))
            for p in compiled().get("people", [])]


def cmd_passcodes(_: argparse.Namespace) -> None:
    print("Chat sign-in (a local demo identity provider; ADR-0114). Passcodes are")
    print(f"in {(STACK / '.env').relative_to(ROOT)} as CHAT_PASSCODE_<PERSON>:")
    for pid, name, code in passcodes():
        print(f"  {name:<10} {pid:<12} {code}")


def cmd_up(args: argparse.Namespace) -> None:
    if not args.no_generate:
        cmd_generate(args)
    # After the compile, so a newly designed agent or person gets its values.
    cmd_env(args)
    cmd_wheel(args)
    waited = compose("up", "-d", "--build", "--remove-orphans", "--wait",
                     "--wait-timeout", "900", check=False)
    if waited.returncode != 0:
        # `--wait` counts a one-shot that has finished (bus-init, ADR-0118)
        # as a failure when it is recreated; what matters is that it exited 0
        # and everything else is up.
        import time

        deadline, bad = time.time() + 900, ["no containers"]
        while time.time() < deadline:
            out = compose("ps", "-a", "--format", "json", capture_output=True, text=True)
            rows = [json.loads(x) for x in out.stdout.splitlines()
                    if x.strip().startswith("{")]
            bad = [r["Service"] for r in rows
                   if (r["Service"] == "bus-init" and r.get("ExitCode") != 0)
                   or (r["Service"] != "bus-init" and (
                       r.get("State") != "running"
                       or r.get("Health") not in ("", None, "healthy")))]
            if rows and not bad:
                break
            time.sleep(5)
        else:
            raise SystemExit(f"the stack did not come up: {', '.join(bad)}")
    compose("ps")
    p = ports(PORT_BASE)
    print(f"""
AYC is up (project '{PROJECT}'), on this machine only (127.0.0.1):
  chat with any agent      http://127.0.0.1:{p['CHAT']}/
  tenant designer/API      http://127.0.0.1:{p['DESIGNER']}/ui/
  mock Shopify state       http://127.0.0.1:{p['SHOPIFY']}/state
  mock Fishbowl state      http://127.0.0.1:{p['FISHBOWL']}/state
  mock accounting state    http://127.0.0.1:{p['ACCOUNTING']}/state
  mock CMS state           http://127.0.0.1:{p['CMS']}/state
  mock deploy pipeline     http://127.0.0.1:{p['DEPLOY_PIPELINE']}/state
""")
    cmd_passcodes(args)


def cmd_down(args: argparse.Namespace) -> None:
    compose("down", *(["--volumes"] if args.volumes else []), "--remove-orphans")


def cmd_ps(_: argparse.Namespace) -> None:
    compose("ps")


def _script_env() -> dict[str, str]:
    return {**os.environ, "AYC_PROJECT": PROJECT, "AYC_PORT_BASE": str(PORT_BASE)}


def cmd_e2e(_: argparse.Namespace) -> None:
    run(sys.executable, str(AYC / "end_to_end_local.py"), env=_script_env())


def cmd_e2e_messaging(_: argparse.Namespace) -> None:
    run(sys.executable, str(AYC / "end_to_end_messaging.py"), env=_script_env())


def main() -> int:
    global PROJECT, PORT_BASE
    _utf8_console()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=PROJECT,
                    help="Compose project name (default ayc-local, or $AYC_PROJECT)")
    ap.add_argument("--port-base", type=int, default=PORT_BASE,
                    help="host port base: designer on it, chat on +80, mocks on "
                         "+101..+105 (default 18000, or $AYC_PORT_BASE)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("env", help="write generated/local/.env with fresh local values")
    sub.add_parser("generate", help="compile AYC for the local target")
    sub.add_parser("wheel", help="build the orgagents wheel the images install")
    up = sub.add_parser("up", help="env, compile, wheel, build and start")
    up.add_argument("--no-generate", action="store_true",
                    help="start what is already generated")
    down = sub.add_parser("down", help="stop the stack")
    down.add_argument("--volumes", action="store_true", help="and delete its volumes")
    sub.add_parser("ps", help="show the stack's containers")
    sub.add_parser("passcodes", help="print the chat sign-in passcodes")
    rot = sub.add_parser("rotate-approval-key",
                         help="new approval signing key; workers keep trusting the previous")
    rot.add_argument("--keep", type=int, default=1,
                     help="how many previous public keys workers still trust (default 1)")
    sub.add_parser("e2e", help="run the purchase-to-pay scenario against it")
    sub.add_parser("e2e-messaging",
                   help="run the agent-to-agent delegation scenario against it")
    args = ap.parse_args()
    PROJECT, PORT_BASE = args.project, args.port_base
    {"env": cmd_env, "generate": cmd_generate, "wheel": cmd_wheel, "up": cmd_up,
     "down": cmd_down, "ps": cmd_ps, "e2e": cmd_e2e,
     "e2e-messaging": cmd_e2e_messaging,
     "passcodes": cmd_passcodes, "rotate-approval-key": cmd_rotate}[args.cmd](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
