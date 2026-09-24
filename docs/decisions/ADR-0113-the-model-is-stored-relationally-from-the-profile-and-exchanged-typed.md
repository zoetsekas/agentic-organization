---
id: ADR-0113
title: The model is stored in relational tables generated from the UML profiles, and exchanged as YAML or JSON in which every element names its UML type
status: Accepted
version: 1.1.0
date: 2026-09-23
updated: 2026-09-24
deciders: [Platform Architecture]
consulted: [Designer, Compiler, Data Governance, Operations]
informed: [All engineering]
scope: [spec, designer, persistence, compiler, docs]
workstreams: [WS-032]
supersedes: []
superseded_by: []
related: [ADR-0004, ADR-0031, ADR-0033, ADR-0042, ADR-0043, ADR-0101, ADR-0104, ADR-0112]
tags: [persistence, relational, postgres, export, yaml, json, uml, interchange]
---

# ADR-0113: The model is stored in relational tables generated from the UML profiles, and exchanged as YAML or JSON in which every element names its UML type

## Context
The designer's "relational" backend (ADR-0031) is a document store: each saved
design is one JSON body in a single `documents` table (`orgagents.store`). The
database cannot answer "which agents rely on `order_data`", enforce that a
mandate names a decision that exists, or let another system read the model
without parsing our document format. The model is relational in nature — its
elements have identity, typed properties and typed relationships with
multiplicities — and since ADR-0112 every one of them is declared in a UML
profile.

The YAML and JSON files are the other half: they are how a design is
exchanged, reviewed in Git and loaded by the CLI and compiler. Today an element
in them is identified only by *where* it sits (`organization.teams[].members[]`
is an agent), so a file fragment, a diff hunk or an exported element on its
own does not say what it is.

## Decision
1. **The relational schema is generated from the UML profiles.** It is never
   written by hand, so it cannot drift from the model:

   | UML (ADR-0112) | Relational |
   |---|---|
   | Profile | a PostgreSQL schema (`organisation`, `authority`, `data`, ...) |
   | Stereotype | a table in its profile's schema (`organisation.agent`, `authority.mandate`, ...), one row per element, with `model_id`, `id`, `version` |
   | Attribute typed by a primitive | a typed column (`NOT NULL` when the multiplicity's lower bound is 1) |
   | Attribute typed by an Enumeration | a column with a `CHECK` against the literals, and an enumeration table for readers |
   | Attribute typed by a DataType (`Budget`, `WorkingHours`) | the DataType's own columns, prefixed, when single-valued; a child table when multi-valued |
   | Association / dependency / usage to one | a foreign key |
   | Association / dependency / usage to many | a link table with an `ordinal` |
   | Composition | a foreign key to the owner with `ON DELETE CASCADE` |
   | Association class (`DataDependency`) | a link table with the association class's own columns |
   | Generalization | the specific table references the general one (table per class) |

   Every element row also appears in a single **`element`** index (`id`,
   `model_id`, `uml_type`, `metaclass`, `profile`, `name`) so any element can
   be found by id without knowing its table.

2. **PostgreSQL is the relational store, in every environment.** One
   dialect, so the DDL can use what PostgreSQL does well — `CHECK`
   constraints, deferrable foreign keys, `jsonb` for the few free-form maps
   the spec allows, and schemas: one PostgreSQL schema per UML profile
   (`organisation.agent`, `authority.mandate`, `deployment.server`), so the
   profile split of ADR-0112 is visible in the database too. Locally it runs
   as a container beside the designer in `docker-compose.yml` (pinned per
   ADR-0053, data on a volume); the designer connects through
   `ORGAGENTS_DATABASE_URL`. SQLite is not a relational backend: the
   `memory` and `filesystem` modes remain for single-user and test use.
   Schema changes are **forward-only migrations generated from the profile
   diff** (ADR-0042), checked in, and applied at start.

3. **Versions, locks and audit keep their meaning.** A saved revision is a
   snapshot of the model's rows (`revision_id` on every row, copy-on-save);
   optimistic concurrency (ADR-0033) and the audit log (ADR-0043) are
   unchanged. The document store remains as the backend for the `memory` and
   `filesystem` persistence modes.

4. **YAML and JSON carry a `type` on every element**, naming its UML type as
   `<Profile>::<Stereotype>` — `Organisation::Agent`, `Authority::Mandate`,
   `Data::DataClass`, `Deployment::Server`. Values that are UML DataTypes
   carry their type the same way (`Assurance::Budget`); enumeration literals
   and primitives do not. The root carries `type: Core::Model` and the profile
   versions it was written against:

   ```yaml
   type: Core::Model
   profiles: {Core: 1.0.0, Organisation: 1.0.0, Authority: 1.0.0, Data: 1.0.0}
   organization:
     type: Organisation::Organization
     id: ayc
     teams:
       - type: Organisation::Team
         id: operations
         members:
           - type: Organisation::Agent
             id: buyer_agent
             mandate:
               type: Authority::Mandate
               decisions: [raise_po]
   ```

   JSON is the same structure. Field names, nesting and ids are unchanged, so
   an exported file is the spec format with types added, not a new format.

5. **Loading is strict about types that are present and tolerant of absent
   ones.** A `type` that disagrees with the position it sits in (an
   `Organisation::Team` inside `members`) is an error with a numbered issue
   code; a file with no `type` fields — every file written before this ADR —
   loads as before, and the types are inferred from position. `orgagents
   export` always writes them.

6. **Round trip is the contract.** YAML → database → YAML and JSON → database
   → JSON reproduce the input's elements, properties, relationships and order,
   with `type` added where it was missing. A published JSON Schema for the
   typed format is generated from the profiles alongside the DDL.

## The queries the schema must serve (1.1.0)
An architecture review asked whether full normalisation earns its cost. It
does only if the tables answer questions a JSON body cannot, so those
questions are written down here and each one is a test
(`tests/test_persistence_queries.py`) that runs the SQL below against a real
PostgreSQL. A schema change that breaks one of them is a breaking change to
this ADR. `:rev` is a revision (`core.revision.revision_id`); the head of a
design is `core.model.head_revision`.

| # | Question | Tables it reads |
|---|---|---|
| Q1 | Which agents rely on data class X? | `data.data_dependency` → `organisation.worker`, `data.data_class` |
| Q2 | Who may decide Y? (every team, agent, person or mission whose mandate names decision Y) | `authority.mandate__decisions` → `authority.decision`, `core.element` |
| Q3 | Which capabilities reach server Z, and on which target? | `deployment.capability_binding` → `deployment.server`, `access.capability`, `deployment.target` |
| Q4 | All elements of UML type T across every design (at their heads) | `core.element` → `core.model` |
| Q5 | Which agents hold capability C, directly or through a role they play? | `organisation.worker__capabilities`, `organisation.role_assignment`, `organisation.role__capabilities` |

```sql
-- Q1: agents that rely on data class :x
SELECT w.id FROM data.data_dependency dd
  JOIN organisation.worker w ON w.row_id = dd.owner_row
  JOIN data.data_class dc   ON dc.row_id = dd.data_class__row
 WHERE dd.revision_id = :rev AND dc.id = :x ORDER BY w.id;

-- Q2: who may decide :y
SELECT e.uml_type, e.id FROM authority.mandate__decisions md
  JOIN authority.decision d ON d.row_id = md.target_row
  JOIN core.element e       ON e.row_id = md.owner_row
 WHERE md.revision_id = :rev AND d.id = :y ORDER BY e.uml_type, e.id;

-- Q3: capabilities deployed on server :z
SELECT c.id, t.target FROM deployment.capability_binding cb
  JOIN deployment.server s    ON s.row_id = cb.server__row
  JOIN access.capability c    ON c.row_id = cb.capability__row
  JOIN deployment.target t    ON t.row_id = cb.owner_row
 WHERE cb.revision_id = :rev AND s.id = :z ORDER BY c.id, t.target;

-- Q4: every element of UML type :t, across designs
SELECT m.model_id, e.id, e.name FROM core.element e
  JOIN core.model m ON m.head_revision = e.revision_id
 WHERE e.uml_type = :t ORDER BY m.model_id, e.id;

-- Q5: agents holding capability :c, directly or through a role
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
ORDER BY 1;
```

### How the mapping is made concrete (1.1.0)
Decision 1's table, as generated (`orgagents.persistence.relational`):

- **Row identity.** Every row has a `row_id` from one sequence
  (`core.row_seq`) and the `revision_id` of the snapshot it belongs to
  (`ON DELETE CASCADE`, so pruning a revision is one delete). An element row
  also carries `model_id` and `version`, and its `row_id` is its key in
  `core.element`. A specialisation's row shares its general row's `row_id`
  (`organisation.agent.row_id` → `organisation.worker.row_id`).
- **Ownership.** A part (a composition, a multi-valued DataType or an
  association-class instance) has `owner_row`, `owner_field` (its dotted path
  inside the owner, so `lifecycle.evaluations` and `memory.namespaces` are
  told apart) and `ordinal`. The foreign key goes to the owner's table when
  there is one owner table, and to `core.element` when a part can sit in
  several (`organisation.role_assignment` under a team, an agent or a
  mission), always `ON DELETE CASCADE`.
- **A reference keeps what was written and what it resolved to.** A
  reference is two columns: the id as written (`data_class`), which the round
  trip reads, and the row it resolves to (`data_class__row`), a deferrable
  foreign key to the target's table — or to `core.element` when the target
  is abstract (Principal, Resource). An id that resolves to nothing (a design
  mid-edit) keeps its text and a null row; the validator, not the database,
  owns the rule that it must resolve (ADR-0065's checks already run on every
  save). A to-many reference is a link table named after its owner class and
  field (`authority.mandate__decisions`) with the same two columns
  (`target_id`, `target_row`) and an `ordinal`.
- **Single-valued DataTypes** are flattened into the owner with `__`
  between path segments (`mandate__enforcement__enforced_by`); an optional
  one adds a `<path>__set` boolean. Identifiers longer than PostgreSQL's 63
  bytes are shortened with a stable hash suffix.
- **Primitives and enumerations.** Multi-valued primitives are PostgreSQL
  arrays; an enumeration is `text` with a `CHECK` against its literals (an
  array one with `<@`), and every enumeration has a reader table
  (`<schema>.enum_<name>`). `Map` and `Any` are `json`, not `jsonb` as
  Decision 2 first said: `jsonb` reorders an object's keys, and the round
  trip (Decision 6) is the contract. A reader casts (`labels::jsonb`) to
  query inside one; drafts and layouts are `json` for the same reason.
- **Written-as-is parts.** The activity graph (`_AsWritten`, ADR-0110) keeps
  which fields were written and its extra keys (`fields_set`, `extra`), so a
  workflow round-trips to the same document.
- **What is not a model.** The designer stores drafts. A revision whose spec
  the model refuses — or that holds keys the model would drop — is stored
  whole in `core.revision.draft_spec` and has no element rows; the same holds
  for a binding. Nothing an author saved is ever lost to normalisation.
- **Designer records.** Systems, revisions' layout, workspaces, locks,
  settings and the audit log live in the same database (`designer.system`,
  `designer.revision`, and the unchanged document table as
  `designer.documents`) so one PostgreSQL holds the whole designer. Without
  `ORGAGENTS_DATABASE_URL` the `relational` setting keeps the SQLite document
  store it had, for tests and single-user use; the designer container sets
  the URL.
- **Migrations.** `orgagents db generate-migration` diffs the previous
  generated schema (`migrations/schema.json`) with the profiles' and writes
  the next forward-only `NNNN_*.sql`; `orgagents db migrate` (and every
  start) applies what is missing under an advisory lock. A test applies the
  committed migrations to an empty database and compares the catalogue with
  the generated DDL applied directly.
- **Existing designs.** `orgagents db migrate-from-sqlite <designer.db>`
  copies every workspace, system, revision, lock, setting and audit event
  from the SQLite document store into PostgreSQL, reading the SQLite file
  only. The container runs it once on first start against the volume's
  `designer.db` (recorded in `designer.imports`, so never twice).
- **Scoped ids.** Some ids are unique only inside their owner (a server in
  its target, a step in its workflow). When an id names several elements of
  the kinds a reference may point at, the one nearest the reference in the
  ownership tree is its row; a tie leaves the row empty.
- **Not yet built (1.1.0).** The designer UI's *Export…* action and typed
  files in its Import dialog are served by the API
  (`GET /api/designer/systems/{id}/export`, `POST /api/designer/import`)
  but not yet wired into `web/`. The example files are not rewritten with
  types: untyped input is valid, and `orgagents export` produces the typed
  form on demand.

## Scope
Designer persistence (a new `relational` backend replacing the document-body
one), the spec loader and a new exporter, the CLI (`export`, `import`,
`db migrate`), the generated DDL, migrations and JSON Schema, and the example
files (regenerated with types). The binding is stored and exported the same
way through the Deployment profile. Designer-application records —
workspaces, members, locks, audit — keep their current tables.

## Implementation
- `orgagents.persistence.relational`: DDL generator from the profiles;
  a mapper that writes a validated `SystemSpec`/`Binding` to rows and reads
  rows back, both driven by the profile's properties and relationships, so a
  new stereotype needs no mapper code.
- SQLAlchemy Core over `psycopg` 3; migrations generated by diffing the DDL
  of the previous and current profile versions, applied at start.
- `docker-compose.yml` gains a pinned `postgres` service with a health check
  and a volume; the designer waits for it and defaults its persistence to
  `relational` when `ORGAGENTS_DATABASE_URL` is set.
- Tests run against a real PostgreSQL started in Docker (a session fixture);
  where Docker is unavailable they are skipped with that reason, never faked
  with SQLite.
- `orgagents.spec.exchange`: `dump(spec, format="yaml"|"json", typed=True)`
  and a loader pass that checks and strips `type` before the Pydantic model
  sees the document.
- API: `GET /api/designer/systems/{id}/export?format=yaml|json` and an import
  that accepts both typed and untyped files; the designer's Import dialog and
  a new *Export…* action use them.
- Tests: round trip on every example in `examples/`; generated DDL matches the
  profile (every stereotype has a table, every relationship a key or link
  table); a mistyped element is refused with its issue code; exporting a model
  twice produces identical files; a query test
  ("agents that rely on `order_data`") reads the tables directly.

## Timeline
After ADR-0112 closes the profile gap: the generator can only map what the
profiles declare. ADR-0112's completeness test is therefore a precondition,
and this ADR's DDL test becomes its second enforcer.

## Advantages
- The model is queryable, constrained and reportable with plain SQL, and other
  systems (catalogues, BI, audit) can read it without our code.
- One declaration — the profile — now drives drawing, checking, tracing,
  storage, the JSON Schema and the exchange format.
- An exported element, a Git diff hunk or a fragment in a ticket says what it
  is.
- One database engine to operate, test and tune; PostgreSQL is already the
  state store in the local stack (ADR-0109).

## Disadvantages
- A normalised schema is more work to keep fast than one JSON body: saving a
  large design writes many rows, and a revision snapshot copies them.
- The designer now needs a database server even on a workstation (a
  container, started by Compose).
- Typed files are longer; the `type` lines add noise to hand-written YAML
  (they are optional on input for that reason).

## Alternatives considered
- **Keep the JSON document store** — rejected: not queryable or constrained,
  which is what was asked for.
- **A generic element/property/relationship (EAV) schema** — rejected as the
  primary store: schema-stable, but every query is a self-join and nothing is
  constrained. The `element` index keeps its one advantage (find anything by
  id).
- **SQLite as well as PostgreSQL** — rejected: two dialects to test for no
  gain once the designer already runs in Docker.
- **A graph database** — rejected for the same reason ADR-0099 declined one:
  the model is small, relational and already a graph in the IR; a relational
  store is what operators already run.
- **A `kind` field with our own names** — rejected: the request, and ADR-0112,
  are that the type is the UML type from the profile.

## Verification
Every example design round-trips through PostgreSQL to identical typed YAML
and JSON; the generated DDL has a table for every stereotype and a
key or link table for every relationship in the profiles; an untyped legacy
file loads and exports typed; a type that contradicts its position is
refused with a catalogued issue code.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.1.0 | 2026-09-24 | The queries the schema must serve (Q1–Q5), each held by a test against PostgreSQL; how the mapping is made concrete (row identity, ownership, references that keep the written id and the resolved row, flattening, drafts, designer records in the same database, generated migrations, migration of existing SQLite designs). The root type is `Core::Model`, and `Organisation::Organization` is the organisation stereotype's name. |
| 1.0.0 | 2026-09-23 | Accepted. |
