"""The generated Docker stack is validated by Docker's own parser.

`docker compose config` resolves, interpolates and schema-checks a Compose
file without needing a daemon, so this is a real gate rather than an assertion
about our own output written by us: if Docker would reject the file, this
fails. It is still not a smoke test — nothing is pulled or started (WS-006 M4).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from orgagents.compiler import compile_system
from orgagents.spec import load_binding, load_spec

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme.system.yaml"
BINDING = ROOT / "examples" / "acme.binding.yaml"

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="the docker CLI is not installed; `docker compose config` cannot run",
)


def _compose_files(out: Path) -> list[Path]:
    return sorted(
        p for p in out.rglob("*.y*ml")
        if p.name.startswith("docker-compose") or p.name == "compose.yaml"
    )


def _config(path: Path) -> subprocess.CompletedProcess:
    # --no-interpolate keeps an unset ${VAR} from failing the parse: the
    # generated stack ships an env template, and a missing value is the
    # operator's business, not a generation defect.
    return subprocess.run(
        ["docker", "compose", "-f", str(path), "config", "--no-interpolate"],
        capture_output=True, text=True, cwd=str(path.parent),
        env={**os.environ, "COMPOSE_PROJECT_NAME": "validation"},
    )


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("local-target")
    compile_system(
        load_spec(str(EXAMPLE)),
        targets=["local"],
        out_dir=out,
        binding=load_binding(str(BINDING)),
    )
    return out


def test_the_local_target_emits_a_compose_file(generated):
    assert _compose_files(generated), "the local target generated no Compose file"


def test_docker_accepts_every_generated_compose_file(generated):
    for path in _compose_files(generated):
        result = _config(path)
        assert result.returncode == 0, (
            f"docker rejected {path.name}:\n{result.stderr}"
        )


def test_every_service_pins_an_image_or_builds_one(generated):
    # A floating tag means a rebuild can silently change what runs (ADR-0053).
    import yaml

    for path in _compose_files(generated):
        doc = yaml.safe_load(path.read_text()) or {}
        for name, service in (doc.get("services") or {}).items():
            image = service.get("image")
            if image is None:
                assert "build" in service, f"{name} neither builds nor names an image"
                continue
            assert ":" in image or "@" in image, (
                f"{name} uses '{image}', an unpinned floating tag"
            )
