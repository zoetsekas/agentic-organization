# Agent registry — ayc

Generated from the system spec (spec_version 1.3.0) for target
`terraform:gcp`. This is the fleet inventory: every agent, its owner, its identity,
what it may reach, what wakes it and where it talks to people.

**Lifecycle stage:** development ·
**Owner:** AYC Operations ·
**Permission review:** every 90 days ·
**Retire after idle:** 180 days

## Agents

| Agent | Team | Human owner | Identity | Perms | Environment | Triggers | Channels | Budget |
|---|---|---|---|---|---|---|---|---|
| `ap_agent` | AYC / Operations / Finance | Beverly +1 | `id-ap_agent` | 2 | finance_ops | interactive only | — | $6,000/monthly (throttle) |
| `ar_agent` | AYC / Operations / Finance | Sherry +1 | `id-ar_agent` | 2 | finance_ops | interactive only | — | $6,000/monthly (throttle) |
| `buyer_agent` | AYC / Operations / Purchasing | Katy +1 | `id-buyer_agent` | 4 | warehouse_ops | interactive only | — | $6,000/monthly (throttle) |
| `ceo_agent` | AYC | Philip | `id-ceo_agent` | 1 | — | interactive only | — | $6,000/monthly (throttle) |
| `cgo_agent` | AYC / Growth | Lea +1 | `id-cgo_agent` | 1 | — | interactive only | growth_desk | $6,000/monthly (throttle) |
| `coo_agent` | AYC / Operations | Clark +1 | `id-coo_agent` | 1 | — | interactive only | ops_desk | $6,000/monthly (throttle) |
| `cs_agent` | AYC / Growth / Customer Service | Jacob +1 | `id-cs_agent` | 3 | storefront_ops | interactive only | — | $6,000/monthly (throttle) |
| `ecommerce_agent` | AYC / Growth / E-Commerce and Web | Ping +1 | `id-ecommerce_agent` | 4 | storefront_ops, warehouse_ops | nightly_stock_reconcile | growth_desk | $6,000/monthly (throttle) |
| `inventory_agent` | AYC / Operations / Inventory Audit | Liang +1 | `id-inventory_agent` | 3 | warehouse_ops | interactive only | — | $6,000/monthly (throttle) |
| `marketing_agent` | AYC / Growth / Marketing | Lea +1 | `id-marketing_agent` | 3 | studio | interactive only | — | $6,000/monthly (throttle) |
| `software_agent` | AYC / Operations / IT | Clark +1 | `id-software_agent` | 2 | build | interactive only | — | $6,000/monthly (throttle) |
| `warehouse_agent` | AYC / Operations / Warehouse and Shipping | Andrew +1 | `id-warehouse_agent` | 3 | warehouse_ops | interactive only | ops_desk | $6,000/monthly (throttle) |

## Who each agent answers to

| Agent | Person | Title | Roles | Approves | Channel |
|---|---|---|---|---|---|
| `ap_agent` | Beverly | Accounts Payable | owner | — | — |
| `ap_agent` | Clark | Chief Operating Officer | approver | — | — |
| `ar_agent` | Sherry | Accounts Receivable | owner | — | — |
| `ar_agent` | Clark | Chief Operating Officer | approver | — | — |
| `buyer_agent` | Katy | Buyer | owner | — | — |
| `buyer_agent` | Clark | Chief Operating Officer | approver | — | — |
| `ceo_agent` | Philip | Chief Executive Officer | owner | — | — |
| `cgo_agent` | Lea | Chief Growth Officer | owner | — | — |
| `cgo_agent` | Philip | Chief Executive Officer | approver | — | — |
| `coo_agent` | Clark | Chief Operating Officer | owner | — | — |
| `coo_agent` | Philip | Chief Executive Officer | approver | — | — |
| `cs_agent` | Jacob | Offline Sales and Customer Service | owner | — | — |
| `cs_agent` | Sherry | Accounts Receivable | approver | — | — |
| `ecommerce_agent` | Ping | E-Commerce and Web | owner | — | — |
| `ecommerce_agent` | Lea | Chief Growth Officer | approver | — | — |
| `inventory_agent` | Liang | Inventory Audit and Cycle Count | owner | — | — |
| `inventory_agent` | Clark | Chief Operating Officer | approver | — | — |
| `marketing_agent` | Lea | Chief Growth Officer | owner | — | — |
| `marketing_agent` | Ping | E-Commerce and Web | approver | — | — |
| `software_agent` | Clark | Chief Operating Officer | owner | — | — |
| `software_agent` | Philip | Chief Executive Officer | approver | — | — |
| `warehouse_agent` | Andrew | Warehouse and Shipping Lead | owner | — | — |
| `warehouse_agent` | Liang | Inventory Audit and Cycle Count | approver | — | — |

## People, and what they are on the hook for

| Person | Agents | Pairings |
|---|---|---|
| Andrew (andrew@ayc.example) | 1 | warehouse_agent:owner |
| Beverly (beverly@ayc.example) | 1 | ap_agent:owner |
| Clark (clark@ayc.example) | 6 | ap_agent:approver, ar_agent:approver, buyer_agent:approver, coo_agent:owner, inventory_agent:approver, software_agent:owner |
| Jacob (jacob@ayc.example) | 1 | cs_agent:owner |
| Katy (katy@ayc.example) | 1 | buyer_agent:owner |
| Lea (lea@ayc.example) | 3 | cgo_agent:owner, ecommerce_agent:approver, marketing_agent:owner |
| Liang (liang@ayc.example) | 2 | inventory_agent:owner, warehouse_agent:approver |
| Philip (philip@ayc.example) | 4 | ceo_agent:owner, cgo_agent:approver, coo_agent:approver, software_agent:approver |
| Ping (ping@ayc.example) | 2 | ecommerce_agent:owner, marketing_agent:approver |
| Sherry (sherry@ayc.example) | 2 | ar_agent:owner, cs_agent:approver |

## What each agent may reach

| Agent | Capability | Action | Resource class | Data classes | Approval |
|---|---|---|---|---|---|
| `ap_agent` | `invoice_payment` | write | accounting | financial_records | yes |
| `ar_agent` | `receivable_management` | write | accounting | financial_records | yes |
| `buyer_agent` | `goods_receiving` | write | fishbowl | inventory_data, supplier_data | yes |
| `buyer_agent` | `purchase_ordering` | write | fishbowl | supplier_data, inventory_data | yes |
| `buyer_agent` | `stock_check` | query | fishbowl | inventory_data | no |
| `cs_agent` | `order_query` | query | shopify | order_data | no |
| `cs_agent` | `refund_processing` | write | shopify | order_data, customer_pii | yes |
| `ecommerce_agent` | `order_query` | query | shopify | order_data | no |
| `ecommerce_agent` | `product_publishing` | write | shopify | product_content | yes |
| `ecommerce_agent` | `stock_check` | query | fishbowl | inventory_data | no |
| `inventory_agent` | `inventory_adjustment` | write | fishbowl | inventory_data | yes |
| `inventory_agent` | `stock_check` | query | fishbowl | inventory_data | no |
| `marketing_agent` | `content_publishing` | publish | cms | product_content, public_knowledge | no |
| `marketing_agent` | `promotion_run` | write | shopify | product_content | yes |
| `software_agent` | `software_deploy` | administer | deploy_pipeline | public_knowledge | yes |
| `warehouse_agent` | `order_fulfillment` | write | fishbowl | inventory_data, order_data | yes |
| `warehouse_agent` | `order_query` | query | shopify | order_data | no |

## Sub-agents (called as tools)

| Parent | Tool | Kind | Purpose | Capabilities | Returns | Budget |
|---|---|---|---|---|---|---|
| `ecommerce_agent` | `subagent_stock_verifier` | verify | Confirm a SKU has sellable stock before it is listed. | stock_check | an on-hand quantity and location | 300s |

## Tools and what they wrap

| Agent | Tool | Wraps | Source | Approval |
|---|---|---|---|---|
| `ecommerce_agent` | `stock_lookup` | capability `stock_check` | plugin | no |

## Skills and plugins

| Agent | Skills | Plugins |
|---|---|---|
| `ecommerce_agent` | listing_copy | commerce_pack |

## External agent endpoints

| Endpoint | Trust | Provides | May be sent | Approval |
|---|---|---|---|---|
| — |

## Memory

| Agent | Session | Long term | Namespaces | Recall | May promote |
|---|---|---|---|---|---|
| `ap_agent` | yes | yes | — | on_demand | yes |
| `ar_agent` | yes | yes | — | on_demand | yes |
| `buyer_agent` | yes | yes | — | on_demand | yes |
| `ceo_agent` | yes | yes | — | on_demand | yes |
| `cgo_agent` | yes | yes | — | on_demand | yes |
| `coo_agent` | yes | yes | — | on_demand | yes |
| `cs_agent` | yes | yes | — | on_demand | yes |
| `ecommerce_agent` | yes | yes | — | on_demand | yes |
| `inventory_agent` | yes | yes | — | on_demand | yes |
| `marketing_agent` | yes | yes | merchandising_lessons | on_demand | yes |
| `software_agent` | yes | yes | merchandising_lessons | on_demand | yes |
| `warehouse_agent` | yes | yes | — | on_demand | yes |

| Namespace | Scope | Groups | Data classes | Retention (days) |
|---|---|---|---|---|
| `merchandising_lessons` | protected | commerce | public_knowledge | 365 |

## Missions (short-lived teams)

| Mission | Objective | Leader | Members | Status | Window | Deliverables |
|---|---|---|---|---|---|---|
| `peak_season_readiness` | Get the catalog, stock and promotions ready for the peak salon-refit s | `cgo_agent` | cgo_agent, ecommerce_agent, inventory_agent, marketing_agent | active | 2026-10-01 → 2026-12-15 | 2 |

## Models and model policy

| Agent | Bound model | Permitted classes | Cost ceiling | Regions | Approved | Sub-agent model | Fallback |
|---|---|---|---|---|---|---|---|
| `ap_agent` | gemini-2.5-pro | balanced | — | us-central1 | not checked | — | — |
| `ar_agent` | gemini-2.5-pro | balanced | — | us-central1 | not checked | — | — |
| `buyer_agent` | gemini-2.5-pro | balanced | — | us-central1 | not checked | — | — |
| `ceo_agent` | gemini-2.5-pro | balanced | — | us-central1 | not checked | — | — |
| `cgo_agent` | gemini-2.5-pro | balanced | — | us-central1 | not checked | — | — |
| `coo_agent` | gemini-2.5-pro | balanced | — | us-central1 | not checked | — | — |
| `cs_agent` | gemini-2.5-pro | balanced | — | us-central1 | not checked | — | — |
| `ecommerce_agent` | gemini-2.5-pro | balanced | — | us-central1 | not checked | — | — |
| `inventory_agent` | gemini-2.5-pro | balanced | — | us-central1 | not checked | — | — |
| `marketing_agent` | gemini-2.5-pro | balanced | — | us-central1 | not checked | — | — |
| `software_agent` | gemini-2.5-pro | balanced | — | us-central1 | not checked | — | — |
| `warehouse_agent` | gemini-2.5-pro | balanced | — | us-central1 | not checked | — | — |

## What wakes them

| Trigger | Kind | When | Agent | Overlap / catch-up | Max runtime | Delivers to | On failure |
|---|---|---|---|---|---|---|---|
| `nightly_stock_reconcile` | schedule | cron `0 2 * * *` (America/Los_Angeles) → listing_readiness | `ecommerce_agent` | skip / skip_missed | 900s | ops_desk | — |

## Human contact surfaces

| Channel | Provider | Address | Purposes | SLA | Escalation | Forbidden data |
|---|---|---|---|---|---|---|
| `ops_desk` | google_chat | spaces/ops | notify | — | 0 step(s) | — |
| `growth_desk` | google_chat | spaces/growth | ask, notify | — | 0 step(s) | — |

## Declared interaction flows

Beyond the hierarchy: who may consult, notify or escalate to whom.

| From | To | Kind | Why |
|---|---|---|---|
| `ecommerce_agent` | `inventory_agent` | consult |  |
| `warehouse_agent` | `inventory_agent` | escalate |  |
| `cs_agent` | `ar_agent` | notify |  |
| `buyer_agent` | `ap_agent` | notify |  |
| `marketing_agent` | `ecommerce_agent` | consult |  |

## Grounding sources

| Source | Kind | Provider | Data classes | Citation required |
|---|---|---|---|---|
| `product_specs` | document_store | document_store | public_knowledge | yes |

## Promotion gates

| To stage | Requires | Min pass rate | Approvers |
|---|---|---|---|
| production | evaluations_passed, owner_assigned | 100% | — |

## Evaluation gate

`evaluations_passed` as of the last recorded run; `not_evaluated` means nobody
has run the declared cases, which is not the same as having failed them.

| Agent | `evaluations_passed` |
|---|---|
| `ap_agent` | not_evaluated |
| `ar_agent` | not_evaluated |
| `buyer_agent` | not_evaluated |
| `ceo_agent` | not_evaluated |
| `cgo_agent` | not_evaluated |
| `coo_agent` | not_evaluated |
| `cs_agent` | not_evaluated |
| `ecommerce_agent` | not_evaluated |
| `inventory_agent` | not_evaluated |
| `marketing_agent` | not_evaluated |
| `software_agent` | not_evaluated |
| `warehouse_agent` | not_evaluated |

## Review flags

- **Agents without a human owner:** none
- **Agents without a budget:** none
- **Agents that never run unattended:** `ceo_agent`, `coo_agent`, `buyer_agent`, `warehouse_agent`, `inventory_agent`, `ap_agent`, `ar_agent`, `software_agent`, `cgo_agent`, `cs_agent`, `marketing_agent`
- **Agents with a single paired human:** `ceo_agent`
- **Agents with no long-term memory:** none
- **Agents on an unapproved model:** none
- **Agents moved to a fallback model:** none
- **Agents whose model verdict used a stale catalog figure:** none
- **Missions with no end date:** none
- **Compliance frameworks:** SOC2, PCI_DSS
- **Data residency:** us-central1
- **Redacted from traces:** customer_pii
