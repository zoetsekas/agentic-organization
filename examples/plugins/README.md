# Extending the implementation phase

A design is vendor-neutral on purpose (ADR-0002). The **implementation** phase
is where vendors live, and all of it is pluggable — you should never need to
fork this repository to deploy somewhere it does not already know about.

There are three ways in. They are the same seam at different prices, and the
cheapest one that does the job is the right one.

| Route | Cost | Use when | Example here |
|---|---|---|---|
| **Templates** | no Python | you need a different *output format* | `nomad-templates/` |
| **Overlays** | no Python | you need to tweak a *generated stack* | `compose-overlay/` |
| **A Python target** | a distribution | you need logic, or a real integration | `acme-onprem/` |

Everything below is run against the [`ayc`](../ayc) design, so you can compare
the same organization rendered three ways. The output of the first and third is
checked in under `generated/`.

---

## 1. Templates — bring your own format

`nomad-templates/` is a directory of `*.tmpl` files. No Python, no packaging.

```bash
orgagents compile examples/ayc/ayc.system.yaml \
  --binding examples/plugins/plugins.binding.yaml \
  --target template --template-dir examples/plugins/nomad-templates \
  --out build
```

→ [`generated/nomad-from-templates/`](generated/nomad-from-templates)

```
nomad-templates/
  README.md.tmpl             ->  README.md            (rendered once)
  inventory.csv.tmpl         ->  inventory.csv        (rendered once)
  jobs/{agent}.nomad.tmpl    ->  jobs/<id>.nomad      (once per agent)
```

A path containing `{agent}` renders **once per agent**. That is how iteration is
expressed, because substitution is `string.Template` (`$name`) with no loops or
conditionals by design — see ADR-0092 for why there is no template engine here.

An unknown `$placeholder` is left exactly as written rather than failing the
compile (`$HOME` in a shell fragment is legitimate), and every one is listed in
the generated `CONFORMANCE.md`, so a genuine typo stays findable.

## 2. Overlays — tweak a generated stack

`compose-overlay/10-local-dev.yaml` is a Compose overlay for the `local`
target: it swaps an image, mounts source for live reload and adds a sidecar the
design knows nothing about.

```bash
orgagents compile examples/ayc/ayc.system.yaml --target local --out build
cp examples/plugins/compose-overlay/10-local-dev.yaml build/local/overlays/
cd build/local && make overlays && make up
```

The generated `Makefile` merges every `overlays/*.yaml` over the stack in sorted
order, so the `10-` prefix decides when it applies. The compiler creates
`overlays/` and then never reads or writes inside it, so a regeneration cannot
lose your work.

**The idiom differs per target, and each generated stack says which it uses** in
its own `overlays/README.md`. For Terraform the answer is *not* this folder:
Terraform auto-loads `*.tf` from the root module and never descends into a
subdirectory, so your `.tf` belongs beside the generated ones.

## 3. A Python target — a real integration

`acme-onprem/` is a complete third-party distribution: Acme deploys to their
own on-prem Nomad cluster behind their own gateway, and orgagents knows nothing
about any of it.

```toml
# acme-onprem/pyproject.toml
[project.entry-points."orgagents.targets"]
"acme:onprem" = "acme_onprem.target:AcmeOnPremTarget"
```

```bash
pip install ./examples/plugins/acme-onprem      # the whole installation step
orgagents providers                             # now lists acme:onprem [plugin]
orgagents compile examples/ayc/ayc.system.yaml \
  --binding examples/plugins/plugins.binding.yaml \
  --target acme:onprem --out build
```

→ [`generated/acme-onprem/`](generated/acme-onprem)

Read [`acme-onprem/src/acme_onprem/target.py`](acme-onprem/src/acme_onprem/target.py)
as the reference. It demonstrates the four rules:

1. **It consumes the IR only** — permissions, identities, placements and routing
   are already resolved, so it never re-derives one. A target that imported
   `orgagents.spec` would be refused by the platform's own test suite
   (ADR-0005).
2. **It publishes a `descriptor`** — which is what lets the designer offer the
   right fields for this target instead of guessing. It claims
   `instructions, model, subagents, tools` and pointedly *not* `interrupt_on`,
   because Nomad has nowhere to route an approval.
3. **It writes a conformance report** — Acme's cluster cannot hold a mandate or
   a separation of duties, and the output says so per agent, in the job file
   itself:

   ```
   # Carried by the design and NOT enforced by Nomad.
   #   - mandate: may decide ['publish_product']
   #   - approval required for ['product_publishing'] — Nomad has nowhere to route it
   #   - sandbox 'storefront_ops' (network allowlist)
   ```

4. **It leaves the operator room** — generated files are rewritten each compile;
   anything hand-written beside them is never touched.

## One distribution, three registries

`acme-onprem/` extends **all three** seams ADR-0091 opened, which is the
realistic case: a vendor ships how they deploy, the framework they run, and the
cloud they run it on, together.

```toml
[project.entry-points."orgagents.targets"]
"acme:onprem"  = "acme_onprem.target:AcmeOnPremTarget"

[project.entry-points."orgagents.runtime_adapters"]
acme_framework = "acme_onprem.runtime:build"

[project.entry-points."orgagents.provider_profiles"]
acme_cloud     = "acme_onprem.cloud:ACME_CLOUD"
```

### A runtime the `Runtime` enum can never name

`runtime.py` registers `acme_framework`. The enum is closed and always will be;
the registry is keyed by the **string** an enum member carries, which leaves
room for an id that ships elsewhere.

Its descriptor is the interesting part. Acme's framework *transfers control*
between agents and never returns — a handoff, not a sub-agent call — so it
claims `handoffs` and pointedly **not** `subagents`, `planning` or
`interrupt_on`:

```
orgagents providers --feature handoffs   # openai_agents_sdk, acme_framework
orgagents providers --feature planning   # langchain_deepagents
```

Claiming `interrupt_on` here would put a human-in-the-loop field in front of
somebody whose runtime cannot pause. That is why the vocabulary is fixed and a
word outside it is refused at construction.

### A cloud, as `terraform:acme_cloud`

`cloud.py` is a `ProviderProfile` for Acme's private OpenStack. A profile is a
set of claims about what a cloud can express, and the valuable ones say what it
**cannot**:

| Field | Acme's value | What it produces |
|---|---|---|
| `coarse_actions` | `approve`, `administer` | a "Coarsened permissions" table in `MAPPING.md` — these grants are *broader* than the spec asked |
| `network=None` | no VPC profile | a `network.tf` stating the isolation was **not** generated |
| `resources` omits `knowledge_index`, … | no equivalent exists | those listed under "Unmapped neutral resources", and the skipped block says so in place |
| `action_roles` | Keystone roles | its own IAM mapping — the built-in table cannot know about a cloud shipped elsewhere |

Declaring a weakness is the profile doing its job. A cloud that quietly claimed
full granularity would produce a mapping report that reads as enforced and is
not.

> **Two gaps this example found.** Writing it surfaced that `ACTION_ROLES` was a
> second hardcoded per-provider table (so `register_provider_profile()` alone
> was not enough to add a cloud), and that the emitters indexed `resources`
> directly — so a profile that *honestly* omitted a resource crashed the
> compile, even though `MAPPING.md` had always had a section for exactly that.
> Both are fixed: a profile now carries its own `action_roles`, and an unmapped
> resource produces a note where the block would have gone.

## Binding a plugin target

`plugins.binding.yaml` binds both the template target and `acme:onprem`. A
plugin target is bound exactly like a built-in one — the spec never names a
vendor, and the binding names the runtime and model:

```yaml
targets:
  - target: acme:onprem          # a plugin's id, not ours
    runtime:
      adapter: langchain_deepagents
    model: {provider: anthropic, model: claude-sonnet-5}
```

Without an entry for a target you still get a compile; the neutral defaults
stand in. That is why an unbound run writes `ORGAGENTS_RUNTIME = "echo"`.

## What no extension can do

None of these can widen what an agent is allowed to do. Permissions, mandates,
separations and approvals resolve once from the spec into the IR, long before a
target or an overlay sees anything. A target may *fail to carry* a control — and
must then say so in its conformance report — but it cannot grant one. A control
you could add in a template or edit away in a compose file was never a control.

## See also

- [`docs/PLUGINS.md`](../../docs/PLUGINS.md) — the authoring contract
- `orgagents providers` — what is installed and what each one supports
- ADR-0091 (the extension seam), ADR-0092 (templates and overlays)
