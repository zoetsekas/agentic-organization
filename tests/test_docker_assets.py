"""The designer's own container assets stay coherent with the application.

There is no Docker daemon in this environment, so none of this proves the
image builds. What it does prove is that the assets do not drift from the
application they run: an entrypoint calling a flag the CLI dropped, or a
healthcheck hitting a route that no longer exists, would otherwise be found
only by whoever tried to deploy it.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text()
ENTRYPOINT = (ROOT / "docker" / "entrypoint.sh").read_text()
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())


def test_the_healthcheck_hits_a_route_the_app_serves():
    api = (ROOT / "src" / "orgagents" / "api.py").read_text()
    assert '@app.get("/healthz")' in api
    assert "/healthz" in DOCKERFILE
    assert "/healthz" in COMPOSE["services"]["designer"]["healthcheck"]["test"][-1]


def test_the_entrypoint_only_uses_flags_the_cli_accepts():
    cli = (ROOT / "src" / "orgagents" / "cli.py").read_text()
    for flag in re.findall(r"--[a-z][a-z-]+", ENTRYPOINT):
        assert f'"{flag}"' in cli, f"entrypoint passes {flag}, which the CLI does not define"


def test_the_entrypoint_does_not_assume_an_installed_console_script():
    # The image installs dependencies and sets PYTHONPATH rather than
    # installing the project, so the `orgagents` script does not exist in it.
    assert "python -m orgagents.cli" in ENTRYPOINT
    assert not re.search(r"^\s*(exec\s+)?orgagents\s", ENTRYPOINT, re.M)


def test_the_web_assets_resolve_where_the_image_puts_them():
    # api.py finds the UI relative to its own file (parents[2]/web), so the
    # image must preserve that layout: /app/src/orgagents/api.py -> /app/web.
    api = (ROOT / "src" / "orgagents" / "api.py").read_text()
    assert "parents[2]" in api and '/ "web"' in api
    assert "PYTHONPATH=/app/src" in DOCKERFILE
    assert "COPY --chown=designer:designer src/ ./src/" in DOCKERFILE
    assert "COPY --chown=designer:designer web/ ./web/" in DOCKERFILE


def test_the_container_does_not_run_as_root():
    assert "USER designer" in DOCKERFILE
    assert DOCKERFILE.index("USER designer") < DOCKERFILE.index("ENTRYPOINT")


def test_state_lives_on_a_volume_not_an_image_layer():
    designer = COMPOSE["services"]["designer"]
    assert "designer-data:/data" in designer["volumes"]
    assert designer["environment"]["ORGAGENTS_DB"].startswith("/data/")
    assert "designer-data" in COMPOSE["volumes"]


def test_seeding_is_opt_in_and_never_overwrites():
    assert 'ORGAGENTS_SEED:-0' in ENTRYPOINT
    assert '! -f "$DB"' in ENTRYPOINT


def test_the_scheduler_is_opt_in():
    # An unattended agent loop should be something you asked for.
    assert COMPOSE["services"]["scheduler"]["profiles"] == ["scheduler"]


def test_the_build_context_excludes_local_databases():
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert "*.db" in ignored
    assert ".git" in ignored


# -- the designer's PostgreSQL (ADR-0113) -------------------------------------

def _locked(repository: str) -> str:
    for line in (ROOT / "docker" / "images.lock").read_text().splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 2 and parts[0].split(":", 1)[0] == repository:
            return f"{parts[0]}@{parts[1]}"
    raise AssertionError(f"{repository} is not in docker/images.lock")


def test_postgres_is_pinned_to_the_lock_files_digest():
    image = COMPOSE["services"]["postgres"]["image"]
    assert "@sha256:" in image
    assert image == _locked("postgres")


def test_postgres_is_not_published_and_is_hardened():
    pg = COMPOSE["services"]["postgres"]
    assert "ports" not in pg, "only the designer reaches it (ADR-0114)"
    assert pg["read_only"] is True
    assert pg["cap_drop"] == ["ALL"] and "cap_add" not in pg
    assert "no-new-privileges:true" in pg["security_opt"]
    assert pg["user"] == "70:70"
    assert pg["pids_limit"]
    assert "pg_isready" in pg["healthcheck"]["test"]
    assert "designer-postgres:/var/lib/postgresql/data" in pg["volumes"]
    assert "designer-postgres" in COMPOSE["volumes"]


def test_the_designer_waits_for_postgres_and_is_told_where_it_is():
    designer = COMPOSE["services"]["designer"]
    assert designer["depends_on"]["postgres"]["condition"] == \
        "service_healthy"
    url = designer["environment"]["ORGAGENTS_DATABASE_URL"]
    assert url.startswith("postgresql://") and "@postgres:5432/" in url


def test_the_database_password_is_a_secret_never_a_default():
    """ADR-0114 v1.2: no `orgagents` fallback anywhere; both services read the
    Compose secret, which a script generates and Compose requires."""
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert ":-orgagents}" not in text and "POSTGRES_PASSWORD:" not in text
    designer = COMPOSE["services"]["designer"]
    pg = COMPOSE["services"]["postgres"]
    url = designer["environment"]["ORGAGENTS_DATABASE_URL"]
    assert url == "postgresql://orgagents@postgres:5432/orgagents"      # no password
    assert "orgagents_postgres_password" in designer["secrets"]
    assert "orgagents_postgres_password" in pg["secrets"]
    assert pg["environment"]["POSTGRES_PASSWORD_FILE"] == \
        "/run/secrets/orgagents_postgres_password"
    assert designer["environment"]["ORGAGENTS_DATABASE_PASSWORD_FILE"] == \
        "/run/secrets/orgagents_postgres_password"
    assert COMPOSE["secrets"]["orgagents_postgres_password"]["file"] == \
        "./.secrets/orgagents_postgres_password"
    assert "ORGAGENTS_DATABASE_PASSWORD_FILE" in ENTRYPOINT and "PGPASSWORD" in ENTRYPOINT
    assert ".secrets/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".secrets" in (ROOT / ".dockerignore").read_text(encoding="utf-8")


def test_designer_secrets_init_keeps_an_existing_database_working(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("designer_secrets",
                                                  ROOT / "scripts" / "designer_secrets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "SECRET", tmp_path / ".secrets" / "pw")
    monkeypatch.delenv("ORGAGENTS_POSTGRES_PASSWORD", raising=False)
    args = type("A", (), {"project": "p"})()
    # An existing volume was initialised with the old default: keep it, warn.
    monkeypatch.setattr(mod, "_volume_exists", lambda project: True)
    mod.cmd_init(args)
    assert mod.SECRET.read_text().strip() == "orgagents"
    # A fresh install gets a random one; an existing file is never replaced.
    mod.SECRET.unlink()
    monkeypatch.setattr(mod, "_volume_exists", lambda project: False)
    mod.cmd_init(args)
    first = mod.SECRET.read_text().strip()
    assert len(first) >= 24 and first != "orgagents"
    mod.cmd_init(args)
    assert mod.SECRET.read_text().strip() == first
    mod.SECRET.unlink()
    monkeypatch.setenv("ORGAGENTS_POSTGRES_PASSWORD", "chosen")
    mod.cmd_init(args)
    assert mod.SECRET.read_text().strip() == "chosen"


def test_existing_sqlite_designs_move_once_at_start():
    assert "db migrate-from-sqlite" in ENTRYPOINT and "--once" in ENTRYPOINT
    assert "sqlalchemy" in DOCKERFILE and "psycopg" in DOCKERFILE
