#!/usr/bin/env python3
"""The designer's PostgreSQL password, as a Compose secret (ADR-0114 v1.2).

    python scripts/designer_secrets.py init      # before the first `docker compose up`
    python scripts/designer_secrets.py rotate    # a new password on a running database
    python scripts/designer_secrets.py status

`docker-compose.yml` reads the password from `.secrets/orgagents_postgres_password`
(git-ignored) and refuses to start without it; it never falls back to a
default. `init` writes that file once:

* `$ORGAGENTS_POSTGRES_PASSWORD`, if you set it;
* otherwise, if this Compose project already has a `designer-postgres`
  volume -- a deployment started before this change, whose database was
  initialised with the old default password `orgagents` -- that old password,
  so the running database keeps working, with a warning to `rotate`;
* otherwise a random one.

`rotate` changes the password *inside* the running database first (`ALTER
ROLE`, over the container's local socket, which the image trusts), then
writes the file, keeping the previous one as `.previous`; `--apply` then
recreates the designer so it reads the new secret. Postgres itself only reads
the file when a volume is first initialised, so it needs no restart.

Nothing here stops or restarts a container unless `rotate --apply` is given.
"""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET = ROOT / ".secrets" / "orgagents_postgres_password"
#: What docker-compose.yml used to default to. Only ever written for a volume
#: that already exists, i.e. was initialised with it.
LEGACY_DEFAULT = "orgagents"
ROLE = "orgagents"


def _project() -> str:
    return os.environ.get("COMPOSE_PROJECT_NAME") or ROOT.name.lower()


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_suffix(".tmp")
    tmp.write_text(value + "\n", encoding="utf-8")
    # Readable by the container users (postgres 70, designer) through the
    # bind mount Compose makes of a file secret; the directory is private.
    try:
        os.chmod(tmp, 0o644)
    except OSError:
        pass
    os.replace(tmp, path)


def _volume_exists(project: str) -> bool:
    try:
        out = subprocess.run(["docker", "volume", "ls", "-q", "--filter",
                              f"name=^{project}_designer-postgres$"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


def cmd_init(args: argparse.Namespace) -> int:
    if SECRET.exists() and SECRET.read_text(encoding="utf-8").strip():
        print(f"{SECRET.relative_to(ROOT)} exists; leaving it as it is")
        return 0
    given = os.environ.get("ORGAGENTS_POSTGRES_PASSWORD", "")
    if given:
        _write(SECRET, given)
        print(f"wrote {SECRET.relative_to(ROOT)} from $ORGAGENTS_POSTGRES_PASSWORD")
        return 0
    if _volume_exists(args.project):
        _write(SECRET, LEGACY_DEFAULT)
        print(f"WARNING: volume {args.project}_designer-postgres already exists, so its "
              f"database was initialised with the old default password "
              f"'{LEGACY_DEFAULT}'. Wrote that to {SECRET.relative_to(ROOT)} so it keeps "
              "working. Change it now:\n"
              "    python scripts/designer_secrets.py rotate --apply", file=sys.stderr)
        return 0
    _write(SECRET, secrets.token_urlsafe(24))
    print(f"wrote a new random password to {SECRET.relative_to(ROOT)}")
    return 0


def cmd_rotate(args: argparse.Namespace) -> int:
    new = os.environ.get("ORGAGENTS_POSTGRES_NEW_PASSWORD") or secrets.token_urlsafe(24)
    # psql's :'var' quoting, fed on stdin, so the password is never in argv.
    sql = f"\\set pw '{new}'\nALTER ROLE {ROLE} WITH PASSWORD :'pw';\n"
    done = subprocess.run(["docker", "exec", "-i", args.container, "psql", "-v",
                           "ON_ERROR_STOP=1", "-q", "-U", ROLE, "-d", ROLE],
                          input=sql, capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        print(f"ALTER ROLE failed in {args.container}; nothing changed:\n"
              f"{done.stderr.strip()}", file=sys.stderr)
        return 1
    if SECRET.exists():
        _write(SECRET.with_name(SECRET.name + ".previous"),
               SECRET.read_text(encoding="utf-8").strip())
    _write(SECRET, new)
    print(f"password rotated in {args.container}; {SECRET.relative_to(ROOT)} updated")
    if args.apply:
        # $COMPOSE_FILE, when set, names the files (e.g. with an override);
        # otherwise the repository's own.
        files = [] if os.environ.get("COMPOSE_FILE") else \
            ["-f", str(ROOT / "docker-compose.yml")]
        subprocess.run(["docker", "compose", *files, "-p", args.project, "up", "-d",
                        "--no-deps", "--no-build", "--force-recreate", "--wait",
                        "designer"], check=True, cwd=ROOT)
        print("designer recreated with the new secret")
    else:
        print("the running designer still holds the old password in its environment and "
              "keeps its open connections; recreate it to use the new one:\n"
              "    docker compose up -d --no-deps --force-recreate designer")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    print(f"secret file: {'present' if SECRET.exists() else 'missing'} "
          f"({SECRET.relative_to(ROOT)})")
    if SECRET.exists() and SECRET.read_text(encoding="utf-8").strip() == LEGACY_DEFAULT:
        print("WARNING: it holds the old default password; run `rotate --apply`")
    print(f"volume {args.project}_designer-postgres: "
          f"{'exists' if _volume_exists(args.project) else 'none'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=_project(),
                    help="Compose project (default: $COMPOSE_PROJECT_NAME or the "
                         "checkout's directory name)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="write the secret file if it is missing")
    rot = sub.add_parser("rotate", help="change the password in the running database")
    rot.add_argument("--container", default="orgagents-postgres")
    rot.add_argument("--apply", action="store_true",
                     help="and recreate the designer so it uses the new password")
    sub.add_parser("status", help="say what is there")
    args = ap.parse_args()
    return {"init": cmd_init, "rotate": cmd_rotate, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
