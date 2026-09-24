"""The fabric's proxy owns identity, and nothing a client sends survives it
(ADR-0114).

* unit: in trusted-proxy mode the designer reads identity only from the
  header its forward-auth sets, so a client's X-User -- delivered as the proxy
  would deliver it, with the proxy secret attached -- is not believed;
* static: docker/compose/fabric.yml strips every identity header designer/auth.py
  can read, on the entrypoint (so before any router's forward-auth), sets the
  proxy secret there, puts the fabric's /api/ router behind forward-auth, and
  copies back exactly the header the fabric is configured to read.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FABRIC = ROOT / "docker" / "compose" / "fabric.yml"
AUTH_PY = ROOT / "src" / "orgagents" / "designer" / "auth.py"
STRIP = "traefik.http.middlewares.strip-identity.headers.customrequestheaders."


def _compose() -> dict:
    return yaml.safe_load(FABRIC.read_text(encoding="utf-8"))


def _headers_auth_py_reads() -> set[str]:
    """Every header name spelled out in designer/auth.py that could carry an
    identity: the defaults and the none-mode family."""
    text = AUTH_PY.read_text(encoding="utf-8")
    names = set(re.findall(r'"(X-(?:User|Auth-Request|Forwarded)[A-Za-z-]*)"', text))
    assert {"X-User", "X-User-Name", "X-User-Email"} <= names
    return names


# -- unit: the designer reads only the configured header ------------------------

def _proxy_auth(**kw):
    from orgagents.designer.auth import Authenticator
    from orgagents.designer.models import DesignerSettings

    return Authenticator(DesignerSettings(persistence="memory", auth_mode="trusted_proxy"),
                         proxy_secret="p-secret",
                         proxy_user_header="X-Auth-Request-User",
                         proxy_email_header="X-Auth-Request-Email", **kw)


def test_a_client_x_user_through_the_proxy_is_not_believed():
    from orgagents.designer.auth import MissingCredentials

    auth = _proxy_auth()
    # What reaches the designer if a client's X-User survived the proxy: the
    # proxy secret is genuine, but the forward-auth set no identity.
    delivered = {"X-User": "p_coo", "X-User-Name": "COO",
                 "X-Orgagents-Proxy-Secret": "p-secret"}
    with pytest.raises(MissingCredentials):
        auth.authenticate(proxy_secret_header="p-secret",
                          **auth.identity_headers(delivered))
    # With the forward-auth's header, that one wins and X-User is ignored.
    delivered["X-Auth-Request-User"] = "alice"
    who = auth.authenticate(proxy_secret_header="p-secret",
                            **auth.identity_headers(delivered))
    assert who.user_id == "alice"


def test_the_configured_header_is_what_the_http_app_reads(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from orgagents.api import create_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORGAGENTS_DESIGNER_AUTH", "trusted_proxy")
    monkeypatch.setenv("ORGAGENTS_PROXY_SECRET", "p-secret")
    monkeypatch.setenv("ORGAGENTS_PROXY_USER_HEADER", "X-Auth-Request-User")
    client = TestClient(create_app(str(tmp_path / "d.db")))
    forged = client.get("/api/designer/whoami", headers={
        "X-User": "p_coo", "X-Orgagents-Proxy-Secret": "p-secret"})
    assert forged.status_code == 401
    real = client.get("/api/designer/whoami", headers={
        "X-User": "p_coo", "X-Auth-Request-User": "alice",
        "X-Orgagents-Proxy-Secret": "p-secret"})
    assert real.status_code == 200 and real.json()["user_id"] == "alice"


# -- static: the proxy strips before it authenticates ---------------------------

def test_the_proxy_strips_every_identity_header_auth_py_reads():
    compose = _compose()
    labels = compose["services"]["traefik"]["labels"]
    stripped = {k[len(STRIP):] for k, v in labels.items()
                if k.startswith(STRIP) and v == ""}
    fabric_env = compose["services"]["fabric"]["environment"]
    configured = {fabric_env[k] for k in ("ORGAGENTS_PROXY_USER_HEADER",
                                          "ORGAGENTS_PROXY_NAME_HEADER",
                                          "ORGAGENTS_PROXY_EMAIL_HEADER")}
    missing = (_headers_auth_py_reads() | configured) - stripped
    assert not missing, f"fabric.yml does not strip {sorted(missing)}"
    # The secret header is overwritten with the proxy's value, not passed on.
    assert labels[STRIP + "X-Orgagents-Proxy-Secret"].startswith("${ORGAGENTS_PROXY_SECRET")


def test_the_strip_runs_before_the_forward_auth_and_the_router():
    compose = _compose()
    command = compose["services"]["traefik"]["command"]
    # Entrypoint middlewares run before any router's own middlewares.
    assert "--entrypoints.web.http.middlewares=strip-identity@docker" in command
    fabric = compose["services"]["fabric"]
    labels = fabric["labels"]
    assert labels["traefik.http.routers.fabric.middlewares"] == "authn@docker"
    assert "/api/" in labels["traefik.http.routers.fabric.rule"]
    # Every other router to the fabric serves only the health probe.
    for key, rule in labels.items():
        if key.endswith(".rule") and key != "traefik.http.routers.fabric.rule":
            assert "/api" not in rule, key
    traefik = compose["services"]["traefik"]["labels"]
    copied = traefik["traefik.http.middlewares.authn.forwardauth.authResponseHeaders"].split(",")
    assert fabric["environment"]["ORGAGENTS_PROXY_USER_HEADER"] in copied
    assert traefik["traefik.http.middlewares.authn.forwardauth.trustForwardHeader"] == "false"
    stripped = {k[len(STRIP):] for k in traefik if k.startswith(STRIP)}
    assert set(copied) <= stripped, "a header forward-auth sets must be stripped from clients first"
    assert fabric["environment"]["ORGAGENTS_DESIGNER_AUTH"].endswith("trusted_proxy}")
