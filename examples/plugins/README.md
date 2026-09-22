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
