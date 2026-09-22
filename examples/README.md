# Examples

One folder per worked organization. Each holds its spec (`*.system.yaml`), its
binding where it has one (`*.binding.yaml`), any end-to-end walk-through script,
and — for the designs that target a cloud — the **generated** output under
`generated/` (checked in so you can read the Terraform and ADK without running a
compile).

## Starting your own

Two routes, and the second is usually faster for a real organization:

1. **From nothing** — `orgagents spec new mycorp` writes a design that is valid
   from the first save, then `orgagents phase mycorp.system.yaml --target local
   --scaffold` prints the blocks that close the gate. Best when your
   organization does not resemble anything below.
2. **From the nearest archetype** — copy the folder whose *shape of control*
   matches yours and rename through it. These are not demos; each is a worked
   answer to one family of problem, and the test suite keeps every one valid:

| If your organization looks like… | Start from | Because its control is |
|---|---|---|
| A distributor / merchant on packaged SaaS | `ayc/` | stock vs. listing, refund vs. receivable |
| A finance function moving money | `northwind/` | segregation of duties over a payment |
| A regulated lender | `meridian/` | three lines of defence; a human decides |
| A consumer brand with a compliance voice | `lumiere/` | what an agent may *say* |
| A marketing function | `northbeam/` | consent as a legal basis |
| A security operations centre | `sentinel/` | multi-sandbox; instructions per agent |
| Clinical / pharma | `helios/` | a large design that passes the gate |
| A multinational bank | `atlas/` | information barriers, every feature |
| "show me everything the spec has" | `acme/` | breadth, not an organization |

`house.platform-policy.yaml` is a shared fabric policy the phase-gate examples
judge against; it belongs to no single design.

`plugins/` is not an organization — it is the **extension** examples: the same
`ayc` design rendered three ways without forking this repository (a template
directory, a Compose overlay, and a third-party Python target packaged as its
own distribution). See [`plugins/README.md`](plugins/README.md).

| Folder | Organization | Generated |
|---|---|---|
| `acme/` | Breadth example — every field the spec has | `generated/terraform-gcp/` |
| `plugins/` | **Not an org** — extending the implementation phase three ways | `generated/nomad-from-templates/`, `generated/acme-onprem/` |
| `northwind/` | CFO finance function; segregation of duties over a payment | — (local binding) |
| `meridian/` | Consumer lender under three lines of defence | — |
| `lumiere/` | Beauty & salon products; control on what an agent may say | — |
| `northbeam/` | Marketing; consent as a legal basis | — |
| `sentinel/` | Security operations centre; multi-sandbox + instructions | — |
| `helios/` | Clinical-stage pharma; large, passes the phase gate | `generated/terraform-gcp/` |
| `atlas/` | Multinational bank; every feature, deployed to Gemini/GCP | `generated/adk/`, `generated/terraform-gcp/` |
| `ayc/` | Salon-furnishings distributor (Shopify + Fishbowl); deepagents to GCP *and* to LangGraph Platform | `generated/adk/`, `generated/terraform-gcp/`, `generated/langgraph/` |

The `generated/` folders are compiler output. Regenerate any of them with, e.g.:

```bash
orgagents compile examples/atlas/atlas.bank.system.yaml \
  --binding examples/atlas/atlas.binding.yaml \
  --target terraform:gcp --out examples/atlas/generated
```
