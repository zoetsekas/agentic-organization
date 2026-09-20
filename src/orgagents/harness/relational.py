"""Relational-database access for agents, exposed as an MCP server.

The agent never receives a DSN. It calls three tools — ``list_tables``,
``describe_table`` and ``query`` — and every call is checked against the
`RelationalGrant` attached to its harness: allowed statement classes, reachable
schemas/tables, row limits and column masking.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Optional

from ..models import RelationalGrant
from .mcp import InProcessServer

_STATEMENT_RE = re.compile(r"^\s*(\w+)", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(attach|pragma|vacuum|grant|revoke|copy\s+.*\s+from\s+program)\b", re.IGNORECASE
)
_DDL = {"create", "alter", "drop", "truncate"}


class SQLPolicyError(PermissionError):
    """Raised when a statement violates the agent's relational grant."""


def statement_class(sql: str) -> str:
    m = _STATEMENT_RE.match(sql or "")
    if not m:
        raise SQLPolicyError("empty statement")
    verb = m.group(1).lower()
    if verb in ("with",):
        return "select"
    return "ddl" if verb in _DDL else verb


def referenced_tables(sql: str) -> set[str]:
    """Best-effort extraction of table names after FROM/JOIN/INTO/UPDATE."""
    return {
        t.lower().strip('"`[]')
        for t in re.findall(
            r"\b(?:from|join|into|update)\s+([A-Za-z_][\w\.\"`\[\]]*)", sql, re.IGNORECASE
        )
    }


class RelationalMCP:
    """A policy-enforcing relational MCP server bound to one connection.

    `connect` defaults to SQLite so the reference implementation runs with no
    external services; pass a DB-API connection factory for Postgres et al.
    """

    def __init__(
        self,
        grant: RelationalGrant,
        dsn: str,
        *,
        connect: Optional[Any] = None,
    ) -> None:
        self.grant = grant
        self.dsn = dsn
        self._connect = connect or (lambda: sqlite3.connect(dsn))

    # -- policy ------------------------------------------------------------

    def check(self, sql: str) -> None:
        if _FORBIDDEN.search(sql):
            raise SQLPolicyError("statement uses a forbidden construct")
        if sql.count(";") > 1 or (";" in sql and not sql.strip().endswith(";")):
            raise SQLPolicyError("multi-statement SQL is not allowed")
        cls = statement_class(sql)
        if cls not in self.grant.allowed_statements:
            raise SQLPolicyError(
                f"statement class '{cls}' not permitted by grant "
                f"'{self.grant.connection_name}'"
            )
        if self.grant.tables:
            allowed = {t.lower().split(".")[-1] for t in self.grant.tables}
            used = {t.split(".")[-1] for t in referenced_tables(sql)}
            illegal = used - allowed
            if illegal:
                raise SQLPolicyError(f"tables outside grant: {sorted(illegal)}")

    def _mask(self, columns: list[str], rows: list[tuple]) -> list[dict[str, Any]]:
        masked = {c.lower() for c in self.grant.masked_columns}
        out = []
        for row in rows:
            out.append(
                {
                    col: ("***" if col.lower() in masked else val)
                    for col, val in zip(columns, row)
                }
            )
        return out

    # -- tools -------------------------------------------------------------

    def list_tables(self) -> list[str]:
        """List tables reachable under this grant."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                "ORDER BY name"
            )
            names = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
        if self.grant.tables:
            allowed = {t.lower().split(".")[-1] for t in self.grant.tables}
            names = [n for n in names if n.lower() in allowed]
        return names

    def describe_table(self, table: str) -> list[dict[str, Any]]:
        """Return column name/type for one table."""
        if table not in self.list_tables():
            raise SQLPolicyError(f"table '{table}' is not reachable under this grant")
        conn = self._connect()
        try:
            cur = conn.execute(f'PRAGMA table_info("{table}")')
            return [
                {"name": r[1], "type": r[2], "nullable": not r[3], "pk": bool(r[5])}
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    def query(self, sql: str, params: Optional[list[Any]] = None) -> dict[str, Any]:
        """Run a policy-checked statement and return masked, row-limited results."""
        self.check(sql)
        conn = self._connect()
        try:
            cur = conn.execute(sql, params or [])
            columns = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(self.grant.row_limit) if columns else []
            truncated = bool(columns) and len(rows) == self.grant.row_limit
            if statement_class(sql) != "select":
                conn.commit()
            return {
                "columns": columns,
                "rows": self._mask(columns, rows),
                "row_count": len(rows),
                "truncated": truncated,
                "connection": self.grant.connection_name,
            }
        finally:
            conn.close()

    # -- MCP wiring --------------------------------------------------------

    def as_mcp_server(self, name: Optional[str] = None) -> InProcessServer:
        server = InProcessServer(name or f"db_{self.grant.connection_name}")
        server.tool("list_tables", "List tables reachable under the grant")(
            lambda: self.list_tables()
        )
        server.tool("describe_table", "Describe a table's columns")(
            lambda table: self.describe_table(table)
        )
        server.tool("query", "Run a policy-checked SQL statement")(
            lambda sql, params=None: self.query(sql, params)
        )
        return server
