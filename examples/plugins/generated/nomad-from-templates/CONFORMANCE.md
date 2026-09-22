# What this target cannot enforce

Generated for `ayc`, target `template` (your templates, from
`examples/plugins/nomad-templates`).

This target renders **your** templates. It does not know what they say, so it
cannot promise anything about what they carry — and a report that implied
otherwise would be the lie every other target's conformance page exists to
prevent (ADR-0073).

## What was rendered

- `README.md.tmpl`
- `inventory.csv.tmpl`
- `jobs/{agent}.nomad.tmpl`

## Placeholders left as written

`string.Template.safe_substitute` leaves an unknown `$name` untouched rather
than failing the compile, because it is usually a shell variable rather than a
mistake. If one of these is a typo, this is where you find it.

None — every `$placeholder` resolved.

## What you are responsible for

| Construct | Who enforces it |
|---|---|
| Roles, permissions, mandates | this platform's harness — never your output |
| Separation of duties | the phase gate, at compile time |
| Approvals and who may give them | this platform's harness |
| Data classes and egress | this platform's harness |
| Anything your templates emit | you |

`system.ir.json` beside this file is the whole resolved design — permissions,
identities, placements, routing — if a template needs more than the
placeholders expose.

## When to stop using this target

If you find yourself wanting loops, conditionals or logic, you have outgrown
`string.Template`. Write a Python target and register it under the
`orgagents.targets` entry point (docs/PLUGINS.md); it gets the same IR with
none of these limits.
