"""A real PostgreSQL for the tests that need one (ADR-0113).

Started once per session as a Docker container from the image pinned in
`docker/images.lock`, published on a random port of 127.0.0.1 only, its data
on a tmpfs. Every test gets its own database, cloned from a template the
migrations were applied to once, so tests do not see each other's rows.

Where Docker (or the `postgres` extra) is unavailable the tests are skipped
with that reason. They are never run against SQLite instead: a different
dialect would prove nothing about this one.

`ORGAGENTS_TEST_DATABASE_URL` points the tests at an existing server instead
(a CI service container); the user it names must be allowed to create
databases.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Iterator, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = "orgagents_template"


def pinned_postgres() -> str:
    """The postgres image as ADR-0053 pins it: `repository:tag@digest`."""
    for line in (ROOT / "docker" / "images.lock").read_text(encoding="utf-8").splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 2 and parts[0].startswith("postgres:") \
                and parts[1].startswith("sha256:"):
            return f"{parts[0]}@{parts[1]}"
    raise LookupError("postgres is not pinned in docker/images.lock")


def _why_not() -> Optional[str]:
    try:
        import psycopg  # noqa: F401
        import sqlalchemy  # noqa: F401
    except ImportError:
        return "the postgres extra (sqlalchemy, psycopg) is not installed"
    if os.environ.get("ORGAGENTS_TEST_DATABASE_URL"):
        return None
    if not shutil.which("docker"):
        return "Docker is not available to start PostgreSQL"
    try:
        probe = subprocess.run(["docker", "info", "--format", "{{.OSType}}"],
                               capture_output=True, check=True, timeout=20,
                               text=True)
    except Exception:
        return "the Docker daemon is not reachable to start PostgreSQL"
    # GitHub's windows-latest has Docker, but a Windows-containers daemon
    # that cannot run the Linux postgres image.
    if probe.stdout.strip() != "linux":
        return f"the Docker daemon runs {probe.stdout.strip() or 'unknown'} " \
               "containers, not the Linux postgres image"
    return None


def _wait(url: str, seconds: float = 60) -> None:
    import psycopg
    deadline = time.monotonic() + seconds
    while True:
        try:
            with psycopg.connect(url, connect_timeout=2) as c:
                c.execute("SELECT 1")
            return
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@pytest.fixture(scope="session")
def postgres_server() -> Iterator[str]:
    """The server's admin URL (`postgresql://…/postgres`)."""
    reason = _why_not()
    if reason:
        pytest.skip(reason)
    given = os.environ.get("ORGAGENTS_TEST_DATABASE_URL")
    if given:
        _wait(given)
        yield given
        return
    name = f"orgagents-test-pg-{uuid.uuid4().hex[:8]}"
    run = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name,
         "-e", "POSTGRES_USER=orgagents", "-e", "POSTGRES_PASSWORD=orgagents",
         "-e", "POSTGRES_DB=postgres",
         "-p", "127.0.0.1::5432", "--tmpfs", "/var/lib/postgresql/data",
         pinned_postgres(), "-c", "fsync=off",
         "-c", "synchronous_commit=off", "-c", "full_page_writes=off"],
        capture_output=True, text=True, timeout=300)
    if run.returncode != 0:
        pytest.skip(f"could not start PostgreSQL in Docker: "
                    f"{run.stderr.strip()[:200]}")
    try:
        port = subprocess.run(["docker", "port", name, "5432/tcp"],
                              capture_output=True, text=True, check=True,
                              timeout=30).stdout.split()[0].rsplit(":", 1)[1]
        url = f"postgresql://orgagents:orgagents@127.0.0.1:{port}/postgres"
        _wait(url)
        # The entrypoint restarts the server once after initdb; wait for the
        # second start too.
        time.sleep(1)
        _wait(url)
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True,
                       timeout=60)


def _with_db(url: str, db: str) -> str:
    return url.rsplit("/", 1)[0] + "/" + db


def _admin(url: str, sql: str) -> None:
    import psycopg
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(sql)


@pytest.fixture(scope="session")
def postgres_template(postgres_server: str) -> str:
    """A database with every committed migration applied, to clone."""
    from orgagents.persistence.store import engine, migrate
    _admin(postgres_server, f'DROP DATABASE IF EXISTS "{TEMPLATE}"')
    _admin(postgres_server, f'CREATE DATABASE "{TEMPLATE}"')
    eng = engine(_with_db(postgres_server, TEMPLATE))
    migrate(eng)
    eng.dispose()
    return TEMPLATE


def fresh_database(server: str, template: Optional[str] = None) -> str:
    name = f"t_{uuid.uuid4().hex[:12]}"
    _admin(server, f'CREATE DATABASE "{name}"' +
           (f' TEMPLATE "{template}"' if template else ""))
    return _with_db(server, name)


def drop_database(server: str, url: str) -> None:
    name = url.rsplit("/", 1)[1]
    _admin(server, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture()
def postgres_url(postgres_server: str, postgres_template: str
                 ) -> Iterator[str]:
    """A database of this test's own, migrated."""
    url = fresh_database(postgres_server, postgres_template)
    try:
        yield url
    finally:
        drop_database(postgres_server, url)


@pytest.fixture()
def empty_postgres_url(postgres_server: str) -> Iterator[str]:
    """A database of this test's own, with nothing in it."""
    url = fresh_database(postgres_server)
    try:
        yield url
    finally:
        drop_database(postgres_server, url)
