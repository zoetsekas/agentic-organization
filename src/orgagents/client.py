"""A small typed Python client for the public API (`/api/v1`, ADR-0115).

It is a peer of the UI, not a back door (ADR-0018): it calls the same routes,
so the same service layer, RBAC, validation and audit log apply to it. It
never sends a role — only an identity, in the form the designer's auth mode
expects (ADR-0047, ADR-0114):

``none``           single-user local: ``X-User`` names who is acting
``trusted_proxy``  this client *is* the proxy: ``X-User`` plus the proxy
                   secret the designer was started with
``oidc``           ``Authorization: Bearer <id token>``; headers are ignored

    from orgagents.client import DesignerClient
    with DesignerClient("http://127.0.0.1:8000", user="alice") as dc:
        design = dc.create_design(dc.list_workspaces()[0]["id"], "demo")
        print(dc.validate(design["id"])["findings"])

Errors come back as `ApiError` with the HTTP status and the server's detail,
so a 403 reads as the refusal it is rather than as a `KeyError` later.
"""
from __future__ import annotations

import os
from typing import Any, Literal, Mapping, Optional, TypedDict

import httpx

AuthMode = Literal["none", "trusted_proxy", "oidc"]
DEFAULT_URL = "http://127.0.0.1:8000"
PROXY_SECRET_HEADER = "X-Orgagents-Proxy-Secret"


class ApiError(Exception):
    """A non-2xx answer. `status` is the HTTP code; `detail` the server's."""

    def __init__(self, status: int, detail: Any, *, method: str = "",
                 path: str = "") -> None:
        self.status = status
        self.detail = detail
        self.method = method
        self.path = path
        super().__init__(f"{method} {path} -> {status}: {detail}")

    @property
    def forbidden(self) -> bool:
        return self.status == 403

    @property
    def conflict(self) -> bool:
        return self.status == 409


class Finding(TypedDict, total=False):
    severity: str
    code: str
    where: str
    message: str
    component: str
    issue_id: str
    title: str


class Validation(TypedDict, total=False):
    ok: bool
    errors: list[str]
    warnings: list[str]
    findings: list[Finding]


class DesignSummary(TypedDict, total=False):
    id: str
    workspace_id: str
    name: str
    description: str
    status: str
    version: int


class OperationResult(TypedDict, total=False):
    accepted: bool
    effects: list[str]
    violations: list[dict[str, str]]
    incomplete: list[dict[str, str]]
    spec: dict[str, Any]


class SaveOutcome(TypedDict, total=False):
    status: str                    # saved | merged | conflict | stale
    message: str
    base_version: int
    current_version: int
    record: Optional[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    merged_spec: Optional[dict[str, Any]]


class Preflight(TypedDict, total=False):
    ok: bool
    stage: str
    refusals: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    files: list[str]
    target: str
    version: int


class _Base:
    def __init__(self, base_url: str = DEFAULT_URL, *,
                 user: str = "", auth_mode: AuthMode = "none",
                 token: str = "", proxy_secret: str = "",
                 display_name: str = "", email: str = "",
                 prefix: str = "/api/v1", timeout: float = 30.0,
                 http: Optional[httpx.Client] = None) -> None:
        if auth_mode not in ("none", "trusted_proxy", "oidc"):
            raise ValueError(f"unknown auth mode {auth_mode!r}")
        if auth_mode == "oidc" and not token:
            raise ValueError("oidc mode needs a bearer token")
        if auth_mode == "trusted_proxy" and not (user and proxy_secret):
            raise ValueError("trusted_proxy mode needs a user and the proxy secret")
        self.auth_mode = auth_mode
        self.user = user
        self.prefix = prefix.rstrip("/")
        self._headers = self._identity_headers(
            user=user, token=token, proxy_secret=proxy_secret,
            display_name=display_name, email=email)
        # An injected client (a FastAPI TestClient, say) is used as is and
        # not closed by us; one we create is ours to close.
        self._owns = http is None
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)

    def _identity_headers(self, *, user: str, token: str, proxy_secret: str,
                          display_name: str, email: str) -> dict[str, str]:
        if self.auth_mode == "oidc":
            # X-User is ignored by an OIDC designer; not sending it keeps a
            # reader of the traffic from thinking it counts.
            return {"Authorization": f"Bearer {token}"}
        headers = {"X-User": user or "anonymous"}
        if display_name:
            headers["X-User-Name"] = display_name
        if email:
            headers["X-User-Email"] = email
        if self.auth_mode == "trusted_proxy":
            headers[PROXY_SECRET_HEADER] = proxy_secret
        return headers

    @classmethod
    def from_env(cls, *, http: Optional[httpx.Client] = None, **overrides: Any):
        """Configure from the same variables the CLI and MCP servers read:
        ORGAGENTS_API_URL, ORGAGENTS_USER, ORGAGENTS_AUTH_MODE (defaults to
        ORGAGENTS_DESIGNER_AUTH, then `none`), ORGAGENTS_TOKEN,
        ORGAGENTS_PROXY_SECRET."""
        env = os.environ
        kwargs: dict[str, Any] = {
            "base_url": env.get("ORGAGENTS_API_URL", DEFAULT_URL),
            "user": env.get("ORGAGENTS_USER", ""),
            "auth_mode": env.get("ORGAGENTS_AUTH_MODE")
            or env.get("ORGAGENTS_DESIGNER_AUTH") or "none",
            "token": env.get("ORGAGENTS_TOKEN", ""),
            "proxy_secret": env.get("ORGAGENTS_PROXY_SECRET", ""),
        }
        kwargs.update(overrides)
        return cls(http=http, **kwargs)

    # -- plumbing ------------------------------------------------------------

    def request(self, method: str, path: str, *,
                params: Optional[Mapping[str, Any]] = None,
                json: Any = None) -> Any:
        url = f"{self.prefix}{path}"
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        response = self._http.request(method, url, params=clean or None,
                                      json=json, headers=self._headers)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except Exception:
                detail = response.text
            raise ApiError(response.status_code, detail, method=method, path=url)
        if not response.content:
            return None
        return response.json()

    def close(self) -> None:
        if self._owns:
            self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class DesignerClient(_Base):
    """The designer API: workspaces, designs, validation, model operations,
    review and the publish request."""

    # -- identity and workspaces --------------------------------------------

    def whoami(self) -> dict[str, Any]:
        return self.request("GET", "/designer/whoami")

    def list_workspaces(self) -> list[dict[str, Any]]:
        return self.request("GET", "/designer/workspaces")

    def create_workspace(self, name: str, description: str = "") -> dict[str, Any]:
        return self.request("POST", "/designer/workspaces",
                            json={"name": name, "description": description})

    def add_member(self, workspace_id: str, user_id: str,
                   role: str = "viewer") -> dict[str, Any]:
        return self.request("POST", f"/designer/workspaces/{workspace_id}/members",
                            json={"user_id": user_id, "role": role})

    # -- designs ---------------------------------------------------------------

    def list_designs(self, workspace_id: Optional[str] = None) -> list[DesignSummary]:
        return self.request("GET", "/designer/systems",
                            params={"workspace_id": workspace_id})

    def get_design(self, design_id: str) -> dict[str, Any]:
        """The stored design with its locks, the caller's role and permissions,
        and its validation."""
        return self.request("GET", f"/designer/systems/{design_id}")

    def create_design(self, workspace_id: str, name: str, *,
                      description: str = "",
                      spec: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return self.request("POST", "/designer/systems", json={
            "workspace_id": workspace_id, "name": name,
            "description": description, "spec": spec})

    def save_design(self, design_id: str, *, spec: Optional[dict[str, Any]] = None,
                    base_version: Optional[int] = None, message: str = "",
                    name: Optional[str] = None, description: Optional[str] = None,
                    status: Optional[str] = None,
                    layout: Optional[dict[str, Any]] = None,
                    strategy: Optional[str] = None) -> SaveOutcome:
        """Save. `base_version` is the version the edit started from: a save
        against a newer stored version is merged or refused (409), never
        silently overwritten (ADR-0033)."""
        body = {"spec": spec, "base_version": base_version, "message": message,
                "name": name, "description": description, "status": status,
                "layout": layout, "strategy": strategy}
        return self.request("PUT", f"/designer/systems/{design_id}",
                            json={k: v for k, v in body.items() if v is not None})

    def validate(self, design_id: str) -> Validation:
        """The validator's findings for the stored design, each with its
        catalogued issue id (`OA-1201`)."""
        return self.get_design(design_id)["validation"]

    def revisions(self, design_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.request("GET", f"/designer/systems/{design_id}/revisions",
                            params={"limit": limit})

    def diff(self, design_id: str, from_version: Optional[int] = None,
             to_version: Optional[int] = None) -> dict[str, Any]:
        return self.request("GET", f"/designer/systems/{design_id}/diff",
                            params={"from": from_version, "to": to_version})

    # -- the model ---------------------------------------------------------------

    def issue_codes(self) -> list[dict[str, Any]]:
        return self.request("GET", "/designer/issue-codes")

    def issue_code(self, code: str) -> dict[str, Any]:
        """By name (`team_without_leader`) or number (`OA-1201`)."""
        return self.request("GET", f"/designer/issue-codes/{code}")

    def metamodel(self) -> dict[str, Any]:
        return self.request("GET", "/designer/metamodel")

    def apply_operation(self, spec: dict[str, Any],
                        operation: dict[str, Any]) -> OperationResult:
        """One model operation on a draft (ADR-0102/0103). Stateless: nothing
        is stored; save the returned `spec` to keep it."""
        return self.request("POST", "/designer/operations",
                            json={"spec": spec, "request": operation})

    # -- review and publish ------------------------------------------------

    def preflight(self, design_id: str, target: str = "local") -> Preflight:
        """Validate and compile the stored design into a discarded directory."""
        return self.request("POST", f"/designer/systems/{design_id}/preflight",
                            json={"target": target})

    def request_publish(self, design_id: str, tenant_id: str,
                        target: str = "local") -> dict[str, Any]:
        """Ask the fabric to run the stored design. Needs `system.publish`;
        creates a deployment in `requested` and deploys nothing itself."""
        return self.request("POST", f"/designer/systems/{design_id}/publish",
                            json={"tenant_id": tenant_id, "target": target})


class RuntimeClient(_Base):
    """The running platform: sessions, traces, operations, catalogs, agents."""

    def list_agents(self, org_unit_id: Optional[str] = None) -> list[dict[str, Any]]:
        return self.request("GET", "/agents", params={"org_unit_id": org_unit_id})

    def list_sessions(self, agent_id: Optional[str] = None,
                      limit: int = 50) -> list[dict[str, Any]]:
        return self.request("GET", "/sessions",
                            params={"agent_id": agent_id, "limit": limit})

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self.request("GET", f"/sessions/{session_id}")

    def session_events(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return self.request("GET", f"/sessions/{session_id}/events",
                            params={"limit": limit})

    def session_trace(self, session_id: str) -> dict[str, Any]:
        return self.request("GET", f"/sessions/{session_id}/trace")

    def metrics(self) -> dict[str, Any]:
        return self.request("GET", "/ops/metrics")

    def alerts(self, include_acknowledged: bool = False) -> list[dict[str, Any]]:
        return self.request("GET", "/ops/alerts",
                            params={"include_acknowledged": include_acknowledged})

    def agent_health(self, agent_id: str) -> dict[str, Any]:
        return self.request("GET", f"/ops/health/{agent_id}")

    def search_catalogs(self, q: str = "", kind: Optional[str] = None,
                        environment: str = "development") -> list[dict[str, Any]]:
        return self.request("GET", "/catalogs",
                            params={"q": q, "kind": kind, "environment": environment})

    def catalog_entry(self, entry_id: str) -> dict[str, Any]:
        return self.request("GET", f"/catalogs/{entry_id}")

    def search_marketplace(self, q: str = "", kind: Optional[str] = None,
                           limit: int = 50) -> list[dict[str, Any]]:
        return self.request("GET", "/catalog",
                            params={"q": q, "kind": kind, "limit": limit})

    # -- mutating: callers decide whether to offer these -------------------

    def run_agent(self, agent_id: str, prompt: str, *,
                  session_id: Optional[str] = None,
                  created_by: str = "client") -> dict[str, Any]:
        return self.request("POST", f"/agents/{agent_id}/run", json={
            "prompt": prompt, "session_id": session_id, "created_by": created_by})

    def resume_session(self, session_id: str, response: str,
                       actor: str = "human") -> dict[str, Any]:
        return self.request("POST", f"/sessions/{session_id}/resume",
                            json={"response": response, "actor": actor})

    def ack_alert(self, alert_id: str) -> dict[str, Any]:
        return self.request("POST", f"/ops/alerts/{alert_id}/ack")


__all__ = ["ApiError", "DesignerClient", "RuntimeClient", "AuthMode",
           "Finding", "Validation", "DesignSummary", "OperationResult",
           "SaveOutcome", "Preflight"]
