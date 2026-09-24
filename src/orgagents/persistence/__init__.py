"""The model in PostgreSQL (ADR-0113).

* `relational` — the schema, generated from the UML profiles: the plan, its
  structure, DDL and migration diffs;
* `mapper` — a validated spec or binding to rows and back, walking the same
  plan;
* `migrations` — the committed, forward-only migrations and how they are
  generated and applied;
* `store` — the database: connecting, applying migrations, writing and
  reading revisions, the documents table the designer keeps its other
  records in, and the queries ADR-0113 names.

SQLAlchemy and psycopg are an optional dependency (`pip install
orgagents[postgres]`); only `store` imports them.
"""
