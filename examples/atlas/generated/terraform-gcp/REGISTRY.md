# Agent registry — atlas

Generated from the system spec (spec_version 1.3.0) for target
`terraform:gcp`. This is the fleet inventory: every agent, its owner, its identity,
what it may reach, what wakes it and where it talks to people.

**Lifecycle stage:** development ·
**Owner:** Atlas Platform Engineering ·
**Permission review:** every 90 days ·
**Retire after idle:** 180 days

## Agents

| Agent | Team | Human owner | Identity | Perms | Environment | Triggers | Channels | Budget |
|---|---|---|---|---|---|---|---|---|
| `aml_agent` | Atlas Global / Financial Crime | Kwame Asante +1 | `id-aml_agent` | 4 | fincrime_enclave, banking_ops | interactive only | control_room_escalations | $50,000/monthly (throttle) |
| `audit_agent` | Atlas Global / Internal Audit | Beatriz Costa +1 | `id-audit_agent` | 2 | audit_room | interactive only | control_room_escalations | $50,000/monthly (throttle) |
| `banker_agent` | Atlas Global / Corporate and Investment Bank / Investment Banking | Sanjay Iyer +1 | `id-banker_agent` | 3 | deal_room | interactive only | — | $50,000/monthly (throttle) |
| `cib_head_agent` | Atlas Global / Corporate and Investment Bank | Priya Anand +1 | `id-cib_head_agent` | 1 | — | interactive only | — | $50,000/monthly (throttle) |
| `consumer_head_agent` | Atlas Global / Consumer Bank | Marcus Webb +1 | `id-consumer_head_agent` | 1 | — | interactive only | — | $50,000/monthly (throttle) |
| `cro_agent` | Atlas Global / Risk | Lena Fischer +1 | `id-cro_agent` | 5 | risk_analytics | premarket_risk_pack | risk_desk | $50,000/monthly (throttle) |
| `group_ceo_agent` | Atlas Global | Ingrid Haas | `id-group_ceo_agent` | 1 | — | interactive only | — | $50,000/monthly (throttle) |
| `loan_officer_agent` | Atlas Global / Consumer Bank / Retail Lending | Marcus Webb +1 | `id-loan_officer_agent` | 4 | clean_room, banking_ops | interactive only | — | $50,000/monthly (throttle) |
| `markets_head_agent` | Atlas Global / Corporate and Investment Bank / Markets | Priya Anand +1 | `id-markets_head_agent` | 1 | — | interactive only | risk_desk | $15,000/monthly (warn) |
| `platform_engineer_agent` | Atlas Global / Technology | Yusuf Kaya +1 | `id-platform_engineer_agent` | 2 | build, deploy_runner | interactive only | — | $50,000/monthly (throttle) |
| `research_analyst_agent` | Atlas Global / Corporate and Investment Bank / Markets / Research | Tomás Rivera +1 | `id-research_analyst_agent` | 2 | research_desk | interactive only | — | $50,000/monthly (throttle) |
| `trader_agent` | Atlas Global / Corporate and Investment Bank / Markets / Trading Desk | Priya Anand +1 | `id-trader_agent` | 3 | trading_floor | interactive only | — | $50,000/monthly (throttle) |

## Who each agent answers to

| Agent | Person | Title | Roles | Approves | Channel |
|---|---|---|---|---|---|
| `aml_agent` | Kwame Asante | Money Laundering Reporting Officer | owner | — | — |
| `aml_agent` | Beatriz Costa | Head of Internal Audit | approver | — | — |
| `audit_agent` | Beatriz Costa | Head of Internal Audit | owner | — | — |
| `audit_agent` | Ingrid Haas | Group Chief Executive | approver | — | — |
| `banker_agent` | Sanjay Iyer | Head of Investment Banking | owner | — | — |
| `banker_agent` | Beatriz Costa | Head of Internal Audit | approver | — | — |
| `cib_head_agent` | Priya Anand | Head of Markets | owner | — | — |
| `cib_head_agent` | Ingrid Haas | Group Chief Executive | approver | — | — |
| `consumer_head_agent` | Marcus Webb | Head of Consumer Bank | owner | — | — |
| `consumer_head_agent` | Ingrid Haas | Group Chief Executive | approver | — | — |
| `cro_agent` | Lena Fischer | Chief Risk Officer | owner | — | — |
| `cro_agent` | Beatriz Costa | Head of Internal Audit | approver | — | — |
| `group_ceo_agent` | Ingrid Haas | Group Chief Executive | owner | — | — |
| `loan_officer_agent` | Marcus Webb | Head of Consumer Bank | owner | — | — |
| `loan_officer_agent` | Lena Fischer | Chief Risk Officer | approver | — | — |
| `markets_head_agent` | Priya Anand | Head of Markets | owner | — | — |
| `markets_head_agent` | Lena Fischer | Chief Risk Officer | approver | — | — |
| `platform_engineer_agent` | Yusuf Kaya | Chief Technology Officer | owner | — | — |
| `platform_engineer_agent` | Beatriz Costa | Head of Internal Audit | approver | — | — |
| `research_analyst_agent` | Tomás Rivera | Head of Research | owner | — | — |
| `research_analyst_agent` | Priya Anand | Head of Markets | approver | — | — |
| `trader_agent` | Priya Anand | Head of Markets | owner | — | — |
| `trader_agent` | Lena Fischer | Chief Risk Officer | approver | — | — |

## People, and what they are on the hook for

| Person | Agents | Pairings |
|---|---|---|
| Beatriz Costa (beatriz.costa@atlas.example) | 5 | aml_agent:approver, audit_agent:owner, banker_agent:approver, cro_agent:approver, platform_engineer_agent:approver |
| Ingrid Haas (ingrid.haas@atlas.example) | 4 | audit_agent:approver, cib_head_agent:approver, consumer_head_agent:approver, group_ceo_agent:owner |
| Kwame Asante (kwame.asante@atlas.example) | 1 | aml_agent:owner |
| Lena Fischer (lena.fischer@atlas.example) | 4 | cro_agent:owner, loan_officer_agent:approver, markets_head_agent:approver, trader_agent:approver |
| Marcus Webb (marcus.webb@atlas.example) | 2 | consumer_head_agent:owner, loan_officer_agent:owner |
| Priya Anand (priya.anand@atlas.example) | 4 | cib_head_agent:owner, markets_head_agent:owner, research_analyst_agent:approver, trader_agent:owner |
| Sanjay Iyer (sanjay.iyer@atlas.example) | 1 | banker_agent:owner |
| Tomás Rivera (tomas.rivera@atlas.example) | 1 | research_analyst_agent:owner |
| Yusuf Kaya (yusuf.kaya@atlas.example) | 1 | platform_engineer_agent:owner |

## What each agent may reach

| Agent | Capability | Action | Resource class | Data classes | Approval |
|---|---|---|---|---|---|
| `aml_agent` | `aml_triage` | write | fincrime_platform | aml_case | yes |
| `aml_agent` | `transaction_trace` | query | crm | account_data | no |
| `audit_agent` | `audit_testing` | query | audit_platform | audit_workpapers | no |
| `banker_agent` | `deal_advisory` | write | deal_platform | mnpi | yes |
| `banker_agent` | `wall_crossing` | write | control_room | mnpi | yes |
| `cro_agent` | `breach_override` | approve | risk_system | risk_models, trade_data | yes |
| `cro_agent` | `credit_approval` | approve | credit_system | risk_models | yes |
| `cro_agent` | `limit_setting` | write | risk_system | risk_models, trade_data | yes |
| `cro_agent` | `risk_analytics_run` | query | risk_system | risk_models, market_data | no |
| `loan_officer_agent` | `customer_lookup` | query | crm | customer_pii, account_data | no |
| `loan_officer_agent` | `identity_verification` | query | kyc | customer_pii | no |
| `loan_officer_agent` | `loan_origination` | write | los | account_data, customer_pii | yes |
| `platform_engineer_agent` | `production_deploy` | administer | deploy_pipeline | public_knowledge | yes |
| `research_analyst_agent` | `research_publishing` | publish | research_platform | research_output, market_data | yes |
| `trader_agent` | `position_query` | query | oms | trade_data | no |
| `trader_agent` | `trade_execution` | write | oms | trade_data, market_data | yes |

## Sub-agents (called as tools)

| Parent | Tool | Kind | Purpose | Capabilities | Returns | Budget |
|---|---|---|---|---|---|---|
| `trader_agent` | `subagent_blotter_reconciler` | verify | Reconcile the blotter against the position feed. | position_query | a list of reconciliation breaks | 300s |

## Tools and what they wrap

| Agent | Tool | Wraps | Source | Approval |
|---|---|---|---|---|
| — |

## Skills and plugins

| Agent | Skills | Plugins |
|---|---|---|
| — |

## External agent endpoints

| Endpoint | Trust | Provides | May be sent | Approval |
|---|---|---|---|---|
| — |

## Memory

| Agent | Session | Long term | Namespaces | Recall | May promote |
|---|---|---|---|---|---|
| `aml_agent` | yes | yes | — | on_demand | yes |
| `audit_agent` | yes | yes | — | on_demand | yes |
| `banker_agent` | yes | yes | — | on_demand | yes |
| `cib_head_agent` | yes | yes | — | on_demand | yes |
| `consumer_head_agent` | yes | yes | — | on_demand | yes |
| `cro_agent` | yes | yes | — | on_demand | yes |
| `group_ceo_agent` | yes | yes | — | on_demand | yes |
| `loan_officer_agent` | yes | yes | — | on_demand | yes |
| `markets_head_agent` | yes | yes | — | on_demand | yes |
| `platform_engineer_agent` | yes | yes | control_lessons | on_demand | yes |
| `research_analyst_agent` | yes | yes | — | on_demand | yes |
| `trader_agent` | yes | yes | — | on_demand | yes |

| Namespace | Scope | Groups | Data classes | Retention (days) |
|---|---|---|---|---|
| `control_lessons` | protected | risk | public_knowledge | 730 |

## Missions (short-lived teams)

| Mission | Objective | Leader | Members | Status | Window | Deliverables |
|---|---|---|---|---|---|---|
| `apac_conduct_exam` | Respond to the APAC regulator's conduct examination of the trading bus | `audit_agent` | audit_agent, cro_agent, markets_head_agent | active | 2026-10-01 → 2026-12-15 | 2 |

## Models and model policy

| Agent | Bound model | Permitted classes | Cost ceiling | Regions | Approved | Sub-agent model | Fallback |
|---|---|---|---|---|---|---|---|
| `aml_agent` | gemini-2.5-pro | balanced | — | europe-west1, us-central1 | not checked | — | — |
| `audit_agent` | gemini-2.5-pro | balanced | — | europe-west1, us-central1 | not checked | — | — |
| `banker_agent` | gemini-2.5-pro | balanced | — | europe-west1, us-central1 | not checked | — | — |
| `cib_head_agent` | gemini-2.5-pro | balanced | — | europe-west1, us-central1 | not checked | — | — |
| `consumer_head_agent` | gemini-2.5-pro | balanced | — | europe-west1, us-central1 | not checked | — | — |
| `cro_agent` | gemini-2.5-pro | balanced | — | europe-west1, us-central1 | not checked | — | — |
| `group_ceo_agent` | gemini-2.5-pro | balanced | — | europe-west1, us-central1 | not checked | — | — |
| `loan_officer_agent` | gemini-2.5-pro | balanced | — | europe-west1, us-central1 | not checked | — | — |
| `markets_head_agent` | gemini-2.5-pro | balanced | — | europe-west1, us-central1 | not checked | — | — |
| `platform_engineer_agent` | gemini-2.5-pro | balanced | — | europe-west1, us-central1 | not checked | — | — |
| `research_analyst_agent` | gemini-2.5-pro | balanced | — | europe-west1, us-central1 | not checked | — | — |
| `trader_agent` | gemini-2.5-pro | balanced | — | europe-west1, us-central1 | not checked | — | — |

## What wakes them

| Trigger | Kind | When | Agent | Overlap / catch-up | Max runtime | Delivers to | On failure |
|---|---|---|---|---|---|---|---|
| `premarket_risk_pack` | schedule | cron `0 6 * * 1-5` (Europe/London) → morning_risk_pack | `cro_agent` | skip / skip_missed | 900s | risk_desk | — |

## Human contact surfaces

| Channel | Provider | Address | Purposes | SLA | Escalation | Forbidden data |
|---|---|---|---|---|---|---|
| `risk_desk` | google_chat | spaces/risk | notify | — | 0 step(s) | — |
| `control_room_escalations` | google_chat | spaces/control-room | ask, notify | — | 0 step(s) | — |

## Declared interaction flows

Beyond the hierarchy: who may consult, notify or escalate to whom.

| From | To | Kind | Why |
|---|---|---|---|
| `trader_agent` | `cro_agent` | escalate |  |
| `research_analyst_agent` | `markets_head_agent` | notify |  |
| `aml_agent` | `consumer_head_agent` | notify |  |
| `loan_officer_agent` | `cro_agent` | consult |  |
| `banker_agent` | `cib_head_agent` | escalate |  |
| `audit_agent` | `cro_agent` | consult |  |

## Grounding sources

| Source | Kind | Provider | Data classes | Citation required |
|---|---|---|---|---|
| `policy_library` | document_store | document_store | public_knowledge | yes |

## Promotion gates

| To stage | Requires | Min pass rate | Approvers |
|---|---|---|---|
| production | evaluations_passed, owner_assigned | 100% | — |

## Evaluation gate

`evaluations_passed` as of the last recorded run; `not_evaluated` means nobody
has run the declared cases, which is not the same as having failed them.

| Agent | `evaluations_passed` |
|---|---|
| `aml_agent` | not_evaluated |
| `audit_agent` | not_evaluated |
| `banker_agent` | not_evaluated |
| `cib_head_agent` | not_evaluated |
| `consumer_head_agent` | not_evaluated |
| `cro_agent` | not_evaluated |
| `group_ceo_agent` | not_evaluated |
| `loan_officer_agent` | not_evaluated |
| `markets_head_agent` | not_evaluated |
| `platform_engineer_agent` | not_evaluated |
| `research_analyst_agent` | not_evaluated |
| `trader_agent` | not_evaluated |

## Review flags

- **Agents without a human owner:** none
- **Agents without a budget:** none
- **Agents that never run unattended:** `group_ceo_agent`, `consumer_head_agent`, `loan_officer_agent`, `cib_head_agent`, `markets_head_agent`, `trader_agent`, `research_analyst_agent`, `banker_agent`, `aml_agent`, `audit_agent`, `platform_engineer_agent`
- **Agents with a single paired human:** `group_ceo_agent`
- **Agents with no long-term memory:** none
- **Agents on an unapproved model:** none
- **Agents moved to a fallback model:** none
- **Agents whose model verdict used a stale catalog figure:** none
- **Missions with no end date:** none
- **Compliance frameworks:** SOC2, GDPR, MiFID_II, BASEL_III
- **Data residency:** europe-west1, us-central1, asia-southeast1
- **Redacted from traces:** customer_pii, mnpi, aml_case
