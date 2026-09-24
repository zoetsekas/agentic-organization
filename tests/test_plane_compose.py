"""Each plane's container assets stay coherent with ADR-0053 and the app.

There is no Docker daemon here, so nothing below proves an image builds or a
stack comes up. What it proves is that the assets say what ADR-0053 decided:
the images each plane runs, pinned rather than floating, per-tenant data stores
on per-tenant volumes, and a zero-network sandbox that really has no network.
The lock file is checked for *coverage*, not for correctness of digests — no
digest has been resolved here and the file says so.
"""
import re
from pathlib import Path

import pytest
import yaml

from orgagents.compiler import compile_system
from orgagents.compiler.base import register_builtin_targets
from orgagents.fabric.tenants import TenantRegistry
from orgagents.spec import load_spec
from orgagents.store import Store

ROOT = Path(__file__).resolve().parents[1]
ADR = (ROOT / "docs" / "decisions"
       / "ADR-0053-the-infrastructure-images-each-plane-runs-on.md").read_text(encoding="utf-8")
LOCK_PATH = ROOT / "docker" / "images.lock"
FABRIC_PATH = ROOT / "docker" / "compose" / "fabric.yml"
FABRIC = yaml.safe_load(FABRIC_PATH.read_text(encoding="utf-8"))
IMAGE_DIR = ROOT / "docker" / "images"
FIRST_PARTY = ("orgagents-designer", "orgagents-fabric", "orgagents-command",
               "orgagents-runtime")


# -- helpers ---------------------------------------------------------------


def _repository(ref: str) -> str:
    """The image name without its tag or digest (a registry host may have a port)."""
    ref = ref.split("@", 1)[0]
    head, _, tail = ref.rpartition(":")
    return head if head and "/" not in tail else ref


def _adr_repositories() -> set[str]:
    """Third-party image names ADR-0053 names, in backticks, anywhere in it."""
    refs = set()
    for token in re.findall(r"`([A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?)`", ADR):
        if "/" not in token and ":" not in token:
            continue  # a bare word, not an image reference
        if token.startswith(FIRST_PARTY) or token.endswith((".py", ".md", ".lock", ".yml")):
            continue
        if ":" in token or token.startswith("gcr.io/"):
            refs.add(_repository(token))
    return refs


def _lock_records() -> list[tuple[str, str, str]]:
    records = []
    for line in LOCK_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ref, digest, plane = line.split()
        records.append((ref, digest, plane))
    return records


def _images(compose: dict) -> list[str]:
    return [s["image"] for s in compose["services"].values() if "image" in s]


def _is_floating(ref: str) -> bool:
    """A reference nobody pinned: no tag at all, or a tag that moves by design."""
    if "@sha256:" in ref:
        return False
    name, _, tail = ref.rpartition(":")
    if not name or "/" in tail:
        return True  # no tag
    return tail == "latest"


@pytest.fixture(scope="module")
def tenant_compose(tmp_path_factory):
    register_builtin_targets()
    tmp = tmp_path_factory.mktemp("tenant")
    spec = load_spec(ROOT / "examples" / "acme" / "acme.system.yaml")
    tenant = TenantRegistry(Store(tmp / "fabric.db")).register(
        id="northwind", name="Northwind", cloud_boundary="proj-northwind")
    result = compile_system(spec, targets=["local"], out_dir=tmp,
                            tenant=tenant.to_ir())[0]
    files = {f.path: f.content for f in result.files}
    return yaml.safe_load(files["docker-compose.yaml"]), files


# -- the fabric plane ------------------------------------------------------


def test_the_fabric_plane_runs_every_service_the_adr_names():
    expected = {"fabric", "command", "postgres", "traefik", "keycloak", "vault",
                "otel-collector", "prometheus", "grafana", "jaeger", "redis"}
    assert expected <= set(FABRIC["services"])


def test_the_fabric_plane_is_its_own_compose_project():
    # A plane per project is what keeps `down` on one from taking another with it.
    assert FABRIC["name"] == "orgagents-fabric"


def test_redis_is_optional_because_the_postgres_path_works_without_it():
    assert FABRIC["services"]["redis"]["profiles"] == ["leases"]
    for name, service in FABRIC["services"].items():
        assert "redis" not in service.get("depends_on", []), name


def test_vault_is_labelled_as_a_dev_convenience_not_a_secret_store():
    text = FABRIC_PATH.read_text(encoding="utf-8")
    assert "-dev" in " ".join(FABRIC["services"]["vault"]["command"])
    assert "NOT a secret store" in text


def test_the_fabric_services_we_build_are_built_here_and_health_checked():
    api = (ROOT / "src" / "orgagents" / "api.py").read_text(encoding="utf-8")
    assert '@app.get("/healthz")' in api
    for name in ("fabric", "command"):
        service = FABRIC["services"][name]
        assert service["build"]["dockerfile"] == f"docker/images/{name}/Dockerfile"
        assert "/healthz" in service["healthcheck"]["test"][-1]


def test_every_fabric_service_declares_a_volume_for_state_it_must_keep():
    for name in ("postgres", "keycloak", "prometheus", "grafana", "fabric"):
        assert FABRIC["services"][name]["volumes"], name
    declared = set(FABRIC["volumes"])
    for service in FABRIC["services"].values():
        for mount in service.get("volumes", []):
            source = str(mount).split(":", 1)[0]
            if source.startswith((".", "/")):
                continue
            assert source in declared


# -- pinning ---------------------------------------------------------------


def test_the_fabric_compose_file_references_no_floating_tag():
    floating = [i for i in _images(FABRIC) if _is_floating(i)]
    assert floating == []


def test_the_generated_tenant_compose_file_references_no_floating_tag(tenant_compose):
    compose, _ = tenant_compose
    floating = [i for i in _images(compose) if _is_floating(i)]
    assert floating == []


def test_every_third_party_image_the_fabric_runs_is_in_the_lock_file():
    locked = {_repository(ref) for ref, _, _ in _lock_records()}
    for image in _images(FABRIC):
        if image.startswith(FIRST_PARTY):
            continue
        assert _repository(image) in locked, image


def test_the_lock_file_covers_exactly_the_images_the_adr_names():
    locked = {_repository(ref) for ref, _, _ in _lock_records()}
    assert locked == _adr_repositories()


def test_the_lock_file_holds_no_invented_digest():
    # Nothing here has been pulled. A digest-shaped string would be a fiction
    # that reads as a supply-chain claim.
    for ref, digest, _ in _lock_records():
        assert digest == "UNRESOLVED" or re.fullmatch(r"sha256:[0-9a-f]{64}", digest), ref


def test_the_lock_file_documents_its_own_format_and_names_the_resolver():
    text = LOCK_PATH.read_text(encoding="utf-8")
    assert "FORMAT" in text and "UNRESOLVED" in text
    resolver = ROOT / "docker" / "resolve-images.sh"
    assert resolver.exists() and "resolve-images.sh" in text


# -- the tenant plane ------------------------------------------------------


def test_a_tenant_gets_its_own_postgres_and_its_own_artifact_store(tenant_compose):
    compose, _ = tenant_compose
    state = compose["services"]["state"]
    artifacts = compose["services"]["artifacts"]
    assert _repository(state["image"]) == "postgres"
    assert _repository(artifacts["image"]) == "chrislusf/seaweedfs"
    # Per-tenant volumes, declared by this project, named for this tenant.
    for service in (state, artifacts):
        source = service["volumes"][0].split(":", 1)[0]
        assert source.startswith("northwind-")
        assert source in compose["volumes"]


def test_a_zero_network_sandbox_gets_no_network_at_all(tenant_compose):
    compose, _ = tenant_compose
    sandboxes = {n: s for n, s in compose["services"].items()
                 if n.startswith("sandbox-")}
    assert sandboxes
    isolated = [s for s in sandboxes.values()
                if s["labels"]["org.agentic.network_posture"] == "none"]
    assert isolated, "the example system has an isolated environment class"
    for service in isolated:
        assert service.get("network_mode") == "none"
        assert "networks" not in service


def test_a_sandbox_image_is_one_the_adr_names(tenant_compose):
    compose, _ = tenant_compose
    named = _adr_repositories()
    for name, service in compose["services"].items():
        if not name.startswith("sandbox-"):
            continue
        assert _repository(service["labels"]["org.agentic.sandbox_image"]) in named


def test_a_sandbox_that_executes_nothing_gets_an_image_that_cannot(tenant_compose):
    _, files = tenant_compose
    dockerfiles = {p: c for p, c in files.items()
                   if p.startswith("docker/Dockerfile.")}
    assert dockerfiles
    for path, content in dockerfiles.items():
        if "distroless" not in content:
            continue
        assert "RUN " not in content, path
        assert "USER nonroot" in content


def test_sandboxes_are_built_not_run(tenant_compose):
    compose, _ = tenant_compose
    for name, service in compose["services"].items():
        if name.startswith("sandbox-"):
            assert service["profiles"] == ["sandboxes"]


# -- the images we build ---------------------------------------------------


IMAGES = {name: (IMAGE_DIR / name / "Dockerfile").read_text(encoding="utf-8")
          for name in ("fabric", "command", "runtime")}
ENTRYPOINTS = {name: (IMAGE_DIR / name / "entrypoint.sh").read_text(encoding="utf-8")
               for name in ("fabric", "command", "runtime")}


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_every_image_we_build_shares_one_base(name):
    assert "FROM python:3.11-slim AS base" in IMAGES[name]


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_no_image_we_build_runs_as_root(name):
    dockerfile = IMAGES[name]
    user = re.search(r"^USER (\w+)$", dockerfile, re.M)
    assert user and user.group(1) != "root"
    assert dockerfile.index(user.group(0)) < dockerfile.index("ENTRYPOINT")


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_every_healthcheck_hits_a_route_the_app_serves(name):
    api = (ROOT / "src" / "orgagents" / "api.py").read_text(encoding="utf-8")
    assert '@app.get("/healthz")' in api
    assert "HEALTHCHECK" in IMAGES[name] and "/healthz" in IMAGES[name]


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_state_lives_on_a_volume_not_an_image_layer(name):
    assert 'VOLUME ["/data"]' in IMAGES[name]
    assert re.search(r"ORGAGENTS_DB=/data/\S+", IMAGES[name])


@pytest.mark.parametrize("name", sorted(ENTRYPOINTS))
def test_no_entrypoint_assumes_an_installed_console_script(name):
    # These images set PYTHONPATH rather than installing the project, so the
    # `orgagents` script does not exist in them.
    entrypoint = ENTRYPOINTS[name]
    assert "python -m orgagents.cli" in entrypoint
    assert not re.search(r"^\s*(exec\s+)?orgagents\s", entrypoint, re.M)


@pytest.mark.parametrize("name", sorted(ENTRYPOINTS))
def test_every_entrypoint_only_uses_flags_the_cli_accepts(name):
    cli = (ROOT / "src" / "orgagents" / "cli.py").read_text(encoding="utf-8")
    for flag in re.findall(r"--[a-z][a-z-]+", ENTRYPOINTS[name]):
        assert f'"{flag}"' in cli, f"{name} passes {flag}, which the CLI does not define"


@pytest.mark.parametrize("name", sorted(IMAGES))
def test_the_web_assets_resolve_where_each_image_puts_them(name):
    # api.py finds its assets relative to its own file (parents[2]/web), so
    # every image must keep /app/src/orgagents/api.py -> /app/web.
    dockerfile = IMAGES[name]
    assert "PYTHONPATH=/app/src" in dockerfile
    assert re.search(r"COPY --chown=\w+:\w+ src/ \./src/", dockerfile)
    assert re.search(r"COPY --chown=\w+:\w+ web/ \./web/", dockerfile)
