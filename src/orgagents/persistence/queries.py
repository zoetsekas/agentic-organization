"""The questions the relational schema must answer (ADR-0113 1.1.0, Q1–Q5).

The SQL is the ADR's, verbatim; `tests/test_persistence_queries.py` runs each
against PostgreSQL and checks it against the answer computed from the spec
in Python. A schema change that breaks one is a breaking change to the ADR.
`:rev` is a revision id; `head()` finds a design's head revision.
"""
from __future__ import annotations

from typing import Any, Optional

#: Q1: which agents rely on data class :x?
Q1 = """
SELECT w.id FROM data.data_dependency dd
  JOIN organisation.worker w ON w.row_id = dd.owner_row
  JOIN data.data_class dc   ON dc.row_id = dd.data_class__row
 WHERE dd.revision_id = :rev AND dc.id = :x ORDER BY w.id
"""

#: Q2: who may decide :y? (every team, agent, person or mission whose
#: mandate names it)
Q2 = """
SELECT e.uml_type, e.id FROM authority.mandate__decisions md
  JOIN authority.decision d ON d.row_id = md.target_row
  JOIN core.element e       ON e.row_id = md.owner_row
 WHERE md.revision_id = :rev AND d.id = :y ORDER BY e.uml_type, e.id
"""

#: Q3: which capabilities are deployed on server :z, and on which target?
Q3 = """
SELECT c.id, t.target FROM deployment.capability_binding cb
  JOIN deployment.server s    ON s.row_id = cb.server__row
  JOIN access.capability c    ON c.row_id = cb.capability__row
  JOIN deployment.target t    ON t.row_id = cb.owner_row
 WHERE cb.revision_id = :rev AND s.id = :z ORDER BY c.id, t.target
"""

#: Q4: every element of UML type :t, across every design at its head.
Q4 = """
SELECT m.model_id, e.id, e.name FROM core.element e
  JOIN core.model m ON m.head_revision = e.revision_id
 WHERE e.uml_type = :t ORDER BY m.model_id, e.id
"""

#: Q5: which agents hold capability :c, directly or through a role?
Q5 = """
SELECT w.id FROM organisation.worker__capabilities wc
  JOIN organisation.agent a  ON a.row_id = wc.owner_row
  JOIN organisation.worker w ON w.row_id = a.row_id
  JOIN access.capability c   ON c.row_id = wc.target_row
 WHERE wc.revision_id = :rev AND c.id = :c
UNION
SELECT w.id FROM organisation.role_assignment ra
  JOIN organisation.agent a  ON a.row_id = ra.owner_row
  JOIN organisation.worker w ON w.row_id = a.row_id
  JOIN organisation.role__capabilities rc ON rc.owner_row = ra.role__row
  JOIN access.capability c   ON c.row_id = rc.target_row
 WHERE ra.revision_id = :rev AND c.id = :c
ORDER BY 1
"""

QUERIES = {"q1": Q1, "q2": Q2, "q3": Q3, "q4": Q4, "q5": Q5}


def head(conn: Any, model_id: str) -> Optional[int]:
    from sqlalchemy import text
    return conn.execute(text(
        "SELECT head_revision FROM core.model WHERE model_id = :m"),
        {"m": model_id}).scalar()


def run(conn: Any, name: str, **params: Any) -> list[tuple]:
    """Run one of the ADR's queries. `model=<id>` stands for `rev=` its
    head revision."""
    from sqlalchemy import text
    if "model" in params:
        params["rev"] = head(conn, params.pop("model"))
    elif "rev" in params:
        params["rev"] = int(params["rev"])
    return [tuple(r) for r in conn.execute(text(QUERIES[name.lower()]),
                                           params)]
