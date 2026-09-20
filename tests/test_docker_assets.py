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
