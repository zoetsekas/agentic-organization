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
