---
id: ADR-0106
title: Workspaces are managed, designs import from disk, and every form says what it needs and what is wrong, under the field
status: Accepted
version: 1.0.0
date: 2026-09-23
updated: 2026-09-23
deciders: [Platform Architecture]
consulted: [Design]
informed: [All engineering]
scope: [designer]
workstreams: [WS-003]
supersedes: []
superseded_by: []
related: [ADR-0082, ADR-0101, ADR-0103, ADR-0105]
tags: [designer, forms, workspaces, import, accessibility]
---

# ADR-0106: Workspaces are managed, designs import from disk, and every form says what it needs and what is wrong, under the field

## Context
Four things a user reported:
a workspace could be created (once, automatically) but not renamed, deleted or
added; a design could be opened only from the shipped examples, not from a file
of one's own; dropping a team or an agent from the palette into an environment
did not deploy it; and forms either did nothing on a bad submit (the
organisation form returned silently without a name) or reported a sentence in
a banner the reader had to map back to a field. Properties also showed
comma-separated text boxes for references — because the pickers read the
collections from where they were before ADR-0101 moved them, found nothing, and
fell back to text.

## Decision
- **Workspaces** are created, renamed and deleted from the header. Deleting is
  the owner's act, names what the workspace holds, asks for its name, and
  deletes its organisations only when that is ticked in so many words; the API
  refuses otherwise. A name is required and unique among one's workspaces.
- **Import from file** opens a `*.system.yaml` (or JSON) from the author's disk
  into a chosen workspace, parsed and migrated like `load_spec`, stored in the
  current layout, and laid out like an example; `orgagents examples import
  <path>` does the same from a shell.
- **Dropping into an environment** deploys: an agent or sub-agent dropped from
  the palette into an environment's box is deployed there; a team dropped or
  dragged in deploys every agent in it and its sub-teams, and dragged out
  undeploys them (a team is not deployed; its agents are, ADR-0082).
- **Forms** share one kit: required fields are marked `*` and the form says what
  the mark means; a submit checks every field and writes the reason under each
  one that is wrong, outlined; editing a field clears its error; a server
  refusal names its fields (`422 {message, fields}`) and lands under them the
  same way. Every form in the designer uses it — organisation, workspace,
  delete, import, members, catalog entry, agent — and Properties marks its
  required fields and puts a refused edit's reason under the field.
- **Reference fields** in Properties are searchable pickers of what the design
  has, derived from the model's link rules for any field that holds a
  relationship; only what exists can be chosen.

## Scope
Designer UI and the designer API; no model change.

## Implementation
- `designer/service.py`: `FieldErrors`, `update_workspace`, `delete_workspace`,
  `import_system`; repositories gain `delete_workspace`.
- `api.py`: `PUT/DELETE /api/designer/workspaces/{id}`, `POST /api/designer/import`;
  `_guard` answers `FieldErrors` with `422 {message, fields}`.
- `web/app.js`: `formKit`; workspace and import dialogs.
- `web/canvas.js`: `deployables`/`deploymentRequests`; pickers from link rules.

## Timeline
Delivered with this ADR.

## Advantages
- A person is told, where they are looking, what is missing.
- Designs move between installations as files.

## Disadvantages
- A deleted workspace cannot be restored; the audit log records it.

## Alternatives considered
- **Soft delete of workspaces.** Deferred: the organisations inside have
  revision history; a workspace itself holds only its name and members.

## Verification
- `tests/test_workspaces_and_import.py`.
- `scripts/interaction_check.py` section 12: team and palette drops into an
  environment deploy; Properties pickers; workspace create/rename/delete with
  errors under fields; import with and without a file; the organisation form's
  required name.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-23 | Accepted. |
