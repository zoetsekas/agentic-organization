"""What both MCP servers share: who is calling, how the API is reached, and
how the servers are run (ADR-0115).

The servers are *clients of the public API*, not a second way into the
store. Every tool call becomes an `/api/v1` request carrying the caller's
identity, so the designer's own authenticator, service layer, RBAC and audit
log decide — exactly as they do for the canvas (ADR-0018). An MCP server that
opened the store itself would need its own copy of every permission check,
and two copies of a check are two things that can disagree.

Identity:

* **stdio** — one person per process: `ORGAGENTS_USER` (or `ORGAGENTS_TOKEN`
  when the designer runs in `oidc` mode). No identity, no server.
* **streamable HTTP** — many callers per process, so every request must carry
  `Authorization: Bearer <token>`, and the token is mapped to a user by a
  table the operator wrote (`ORGAGENTS_MCP_TOKENS`). A request without a
  known token is refused with 401 before it reaches MCP at all. The server
  then speaks to the designer *as its trusted proxy* (`none` or
  `trusted_proxy` mode). It refuses to serve HTTP in front of an `oidc`
  designer: forwarding a caller's token to another audience is token
  passthrough, which MCP forbids, and minting one is not this server's job.
"""
from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

import anyio

from ..client import ApiError, DesignerClient, RuntimeClient, _Base

T = TypeVar("T")
C = TypeVar("C", bound=_Base)


class ConfigurationError(RuntimeError):
    """The server was asked to run without what it needs to know who acts."""


def load_token_map(spec: str) -> dict[str, str]:
    """`ORGAGENTS_MCP_TOKENS`: a JSON object `{token: user}`, a path to a file
    holding one, or `token=user,token2=user2`."""
    spec = (spec or "").strip()
    if not spec:
        return {}
    if spec.startswith("{"):
        data = json.loads(spec)
    elif Path(spec).is_file():
        data = json.loads(Path(spec).read_text(encoding="utf-8"))
    else:
        data = dict(part.split("=", 1) for part in spec.split(",") if "=" in part)
    tokens = {str(k).strip(): str(v).strip() for k, v in data.items()}
    short = [u for t, u in tokens.items() if len(t) < 16]
    if short:
        # A guessable token is an identity anybody can claim.
        raise ConfigurationError(
            f"MCP bearer tokens must be at least 16 characters (users: {short})")
    return tokens


@dataclass
class ServerConfig:
    """How to reach the API and who is acting. Built from env + CLI flags."""

    transport: str = "stdio"                 # stdio | http
    api_url: str = "http://127.0.0.1:8000"
    db: str = ""                             # in-process API over this store
    auth_mode: str = "none"                  # the *designer's* auth mode
    user: str = ""
    token: str = ""
    proxy_secret: str = ""
    http_tokens: dict[str, str] = field(default_factory=dict)
    allow_mutations: bool = False            # runtime server only

    @classmethod
    def from_env(cls, **overrides: Any) -> "ServerConfig":
        env = os.environ
        config = cls(
            api_url=env.get("ORGAGENTS_API_URL", "http://127.0.0.1:8000"),
            auth_mode=env.get("ORGAGENTS_AUTH_MODE")
            or env.get("ORGAGENTS_DESIGNER_AUTH") or "none",
            user=env.get("ORGAGENTS_USER", ""),
            token=env.get("ORGAGENTS_TOKEN", ""),
            proxy_secret=env.get("ORGAGENTS_PROXY_SECRET", ""),
            http_tokens=load_token_map(env.get("ORGAGENTS_MCP_TOKENS", "")),
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(config, key, value)
        return config

    def check(self) -> None:
        """Refuse to start without a way to name the caller."""
        if self.auth_mode not in ("none", "trusted_proxy", "oidc"):
            raise ConfigurationError(f"unknown auth mode {self.auth_mode!r}")
        if self.transport == "stdio":
            if self.auth_mode == "oidc" and not self.token:
                raise ConfigurationError(
                    "the designer is in oidc mode: set ORGAGENTS_TOKEN to your id token")
            if self.auth_mode != "oidc" and not self.user:
                raise ConfigurationError(
                    "set ORGAGENTS_USER to the designer user this server acts as")
        elif self.transport == "http":
            if self.auth_mode == "oidc":
                raise ConfigurationError(
                    "streamable HTTP is not offered in front of an oidc designer "
                    "(it would be token passthrough); run one stdio server per "
                    "user with ORGAGENTS_TOKEN")
            if not self.http_tokens:
                raise ConfigurationError(
                    "streamable HTTP needs ORGAGENTS_MCP_TOKENS: every caller "
                    "presents a bearer token that names a designer user")
        else:
            raise ConfigurationError(f"unknown transport {self.transport!r}")
        if self.auth_mode == "trusted_proxy" and not self.proxy_secret:
            raise ConfigurationError(
                "the designer is in trusted_proxy mode: set ORGAGENTS_PROXY_SECRET "
                "so this server can speak as its proxy")


def bearer_of(authorization: str) -> str:
    scheme, _, value = (authorization or "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def user_for_token(tokens: dict[str, str], token: str) -> Optional[str]:
    """Constant-time lookup: compare against every token, not a dict probe."""
    found = None
    for known, user in tokens.items():
        if hmac.compare_digest(known.encode(), (token or "").encode()):
            found = user
    return found


class Backend:
    """Makes an API client for one caller, against a URL or an in-process app.

    In-process (`--db`) builds the very same FastAPI app `orgagents serve`
    runs and calls it through Starlette's test transport — no port, the same
    routes. It is how the tests run and how a single user can point Claude at
    a local store without starting the designer.
    """

    def __init__(self, config: ServerConfig, app: Any = None) -> None:
        self.config = config
        self._app = app
        self._http: Any = None

    def _transport(self) -> Any:
        if self._http is not None:
            return self._http
        if self._app is None and self.config.db:
            from ..api import create_app
            self._app = create_app(self.config.db)
        if self._app is not None:
            from fastapi.testclient import TestClient
            # client host 127.0.0.1 so an address-scoped trusted proxy
            # (ORGAGENTS_PROXY_SOURCES) recognises the in-process caller.
            self._http = TestClient(self._app, base_url="http://mcp.local",
                                    client=("127.0.0.1", 50000),
                                    raise_server_exceptions=False)
        return self._http

    def client(self, cls: type[C], user: str) -> C:
        c = self.config
        return cls(c.api_url, user=user, auth_mode=c.auth_mode,  # type: ignore[arg-type]
                   token=c.token if c.auth_mode == "oidc" else "",
                   proxy_secret=c.proxy_secret, http=self._transport())


def caller(ctx: Any, config: ServerConfig) -> str:
    """The designer user this tool call acts for."""
    if config.transport != "http":
        return config.user
    request = getattr(getattr(ctx, "request_context", None), "request", None)
    headers = getattr(request, "headers", None)
    user = user_for_token(config.http_tokens,
                          bearer_of(headers.get("authorization", "") if headers else ""))
    if not user:
        # The gate refuses these before MCP sees them; this is the second
        # lock on the same door, for a transport that skipped the gate.
        from mcp.server.fastmcp.exceptions import ToolError
        raise ToolError("unauthenticated: no known bearer token on this request")
    return user


async def call(fn: Callable[[], T]) -> T:
    """Run a blocking API call off the event loop, turning a refusal into a
    tool error that says what the platform said."""
    from mcp.server.fastmcp.exceptions import ToolError
    try:
        return await anyio.to_thread.run_sync(fn)
    except ApiError as e:
        kind = {401: "unauthenticated", 403: "permission denied",
                404: "not found", 409: "conflict", 422: "refused"}.get(
                    e.status, "error")
        detail = e.detail if isinstance(e.detail, str) else json.dumps(e.detail)
        raise ToolError(f"{kind} ({e.status}): {detail}") from e


class BearerGate:
    """ASGI middleware for streamable HTTP: no known bearer token, no MCP."""

    def __init__(self, app: Any, tokens: dict[str, str]) -> None:
        self.app = app
        self.tokens = tokens

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode()
                       for k, v in scope.get("headers", [])}
            if not user_for_token(self.tokens,
                                  bearer_of(headers.get("authorization", ""))):
                body = json.dumps({"error": "unauthorized",
                                   "detail": "a bearer token is required"}).encode()
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def http_app(server: Any, config: ServerConfig) -> Any:
    """The streamable-HTTP ASGI app behind the bearer gate."""
    return BearerGate(server.streamable_http_app(), config.http_tokens)


def serve(server: Any, config: ServerConfig, *, host: str = "127.0.0.1",
          port: int = 8765) -> None:
    config.check()
    if config.transport == "stdio":
        server.run("stdio")
        return
    import uvicorn
    uvicorn.run(http_app(server, config), host=host, port=port,
                log_level="warning")


__all__ = ["ServerConfig", "ConfigurationError", "Backend", "BearerGate",
           "caller", "call", "http_app", "serve", "load_token_map",
           "DesignerClient", "RuntimeClient"]
