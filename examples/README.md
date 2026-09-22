# Examples

One folder per worked organization. Each holds its spec (`*.system.yaml`), its
binding where it has one (`*.binding.yaml`), any end-to-end walk-through script,
and — for the designs that target a cloud — the **generated** output under
`generated/` (checked in so you can read the Terraform and ADK without running a
compile).

`house.platform-policy.yaml` is a shared fabric policy the phase-gate examples
judge against; it belongs to no single design.

| Folder | Organization | Generated |
|---|---|---|
| `acme/` | Breadth example — every field the spec has | `generated/terraform-gcp/` |
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
