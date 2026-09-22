# Writing a plugin

The design phase is vendor-neutral on purpose (ADR-0002). The **implementation**
phase is where vendors live, and all of it is pluggable: a deployment target, an
agent framework, a cloud. You add one by shipping a distribution with an entry
point — never by forking this package (ADR-0091).

```bash
orgagents providers                     # what is installed, and what it supports
orgagents providers --feature handoffs  # who supports one thing
orgagents providers --format json       # what the designer reads
```

## Three ways in, cheapest first

| You want | Do this | Cost |
|---|---|---|
| A different output format | **Templates** — a directory of `*.tmpl` | no Python |
| A different stack from a generated one | **Overlays** — see the `overlays/README.md` beside the output | no Python |
| Anything with logic | **A Python target** under an entry point | a package |

### Templates — no Python at all

```bash
orgagents compile acme.system.yaml --target template \
    --template-dir ./my-templates --out build
```

Every `*.tmpl` is rendered against the IR and written with the suffix stripped.
A path containing `{agent}` renders **once per agent** — that is how iteration
is expressed, because substitution is `string.Template` (`$name`) and has no
loops or conditionals by design.

```
my-templates/
  README.md.tmpl              -> README.md          (rendered once)
  jobs/{agent}.nomad.tmpl     -> jobs/<id>.nomad     (once per agent)
```

```hcl
job "$agent_id" {
  # $agent_description   team: $team
  # approval required for: $requires_approval_for
}
```

An unknown `$placeholder` is left exactly as written — `$HOME` in a shell
fragment is legitimate — and every one is listed in the generated
`CONFORMANCE.md`, so a genuine typo is still findable. `system.ir.json` is
written alongside, so a template is never the ceiling.

If you find yourself wanting logic, you have outgrown this; write a Python
target below.

## The three groups

| You are adding | Entry-point group | You implement |
|---|---|---|
| A deployment target (new output) | `orgagents.targets` | `id`, `describe()`, `generate(ir)` |
| An agent framework | `orgagents.runtime_adapters` | a `RuntimeAdapter` subclass with `_run()` |
| A cloud for Terraform | `orgagents.provider_profiles` | a `ProviderProfile` |

```toml
# your pyproject.toml
[project.entry-points."orgagents.targets"]
"acme:onprem" = "acme_orgagents.target:OnPremTarget"
```

An entry resolves to the implementation itself, or to a zero-argument factory
returning one. `pip install` is the whole installation step.

## Declare what you support

Publish a `descriptor` so the designer offers the right fields for your
provider instead of guessing:

```python
from orgagents.plugins import ProviderDescriptor

class OnPremTarget:
    id = "acme:onprem"

    def descriptor(self):
        return ProviderDescriptor(
            id=self.id, title="Acme on-prem", kind="target",
            summary="Generates Nomad jobs for an Acme cluster.",
            supports=frozenset({"instructions", "tools", "model",
                                "subagents", "interrupt_on"}),
        )

    def describe(self): ...
    def generate(self, ir): ...
```

`supports` is drawn from a fixed vocabulary (`orgagents.plugins.FEATURES`);
claiming a word the platform does not know is refused at construction. It is a
**claim about behaviour**: if you say `interrupt_on`, your output must really
pause for a human, because a designer will offer the field and a reader will
believe it. Where you cannot carry something, say so in a conformance report
rather than dropping it silently (ADR-0073).

## The contract a target may rely on

- You receive the **IR** — permissions, identities, placements and routing
  already resolved once — and return `GeneratedFile`s. You never re-derive a
  permission, which is what keeps every target agreeing.
- You **must not import `orgagents.spec`**. A test enforces it (ADR-0005).
- `GeneratedFile.preserve_if_exists` writes a file once and never again;
  `merge_additive` makes it engineer-owned, so regeneration only ever appends
  a missing function and never rewrites an implementation (ADR-0089).
- Anything the operator hand-writes that you did not generate is left alone —
  the engine only writes its own files and refuses to clobber an edited one.

## House rules

- **Registration is additive.** Your id may not shadow a built-in unless you
  pass `replace=True`, because silently changing what `terraform:gcp` means is
  not a thing a plugin should do.
- **A broken plugin is not fatal.** If your entry point fails to import, it is
  skipped and named in `orgagents providers`, not swallowed and not fatal.
- **Order does not matter.** Built-ins and plugins load independently; whichever
  arrives first, you end up with both.

## Adding a cloud

```python
import dataclasses
from orgagents.compiler.targets.terraform import (
    PROFILES, TerraformTarget, register_provider_profile)

register_provider_profile(
    dataclasses.replace(PROFILES["gcp"], id="openstack", display="OpenStack"))
# the target is then `terraform:openstack`
```
