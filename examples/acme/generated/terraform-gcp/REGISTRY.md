# Agent registry — acme

Generated from the system spec (spec_version 1.4.0) for target
`terraform:gcp`. This is the fleet inventory: every agent, its owner, its identity,
what it may reach, what wakes it and where it talks to people.

**Lifecycle stage:** development ·
**Owner:** Acme Platform Team ·
**Permission review:** every 90 days ·
**Retire after idle:** 120 days

## Agents

| Agent | Team | Human owner | Identity | Perms | Environment | Triggers | Channels | Budget |
|---|---|---|---|---|---|---|---|---|
| `analyst` | Acme Corp / Finance | Tom Becker +1 | `id-analyst` | 5 | analysis | weekday_flash_report, finance_question | finance_approvals | $1,200/monthly (warn) |
| `ceo` | Acme Corp | Dana Whitfield +1 | `id-ceo` | 3 | reasoning | permission_review | exec_briefing | $4,000/monthly (throttle) |
| `cfo` | Acme Corp / Finance | Priya Raman +2 | `id-cfo` | 6 | documents | month_end_close_pack | finance_approvals, exec_briefing | $1,200/monthly (warn) |
| `cro` | Acme Corp / Revenue | Marco Oliveira +1 | `id-cro` | 4 | reasoning | interactive only | exec_briefing | $4,000/monthly (throttle) |
| `cto` | Acme Corp / Technology | Iris Nakamura +1 | `id-cto` | 6 | reasoning | interactive only | incidents, exec_briefing | $4,000/monthly (throttle) |
| `platform_engineer` | Acme Corp / Technology / Platform Engineering | Jo Adeyemi +1 | `id-platform_engineer` | 4 | build | interactive only | change_review | $4,000/monthly (throttle) |
| `platform_lead` | Acme Corp / Technology / Platform Engineering | Samir Haddad +1 | `id-platform_lead` | 6 | reasoning | interactive only | incidents, change_review | $4,000/monthly (throttle) |
| `reconciler` | Acme Corp / Finance | Ana Silva +1 | `id-reconciler` | 4 | isolated_review | interactive only | finance_approvals | $50/daily (halt) |
| `sre` | Acme Corp / Technology / Platform Engineering | Lena Fischer +2 | `id-sre` | 4 | integration | incident_triage | incidents | $4,000/monthly (throttle) |

## Who each agent answers to

| Agent | Person | Title | Roles | Approves | Channel |
|---|---|---|---|---|---|
| `analyst` | Tom Becker | Senior Analyst | owner | — | finance_approvals |
| `analyst` | Priya Raman | Chief Financial Officer | reviewer, escalation | — | finance_approvals |
| `ceo` | Dana Whitfield | Chief Executive Officer | owner | — | exec_briefing |
| `ceo` | Nadia Okonjo | Chief of Staff | operator, escalation | — | exec_briefing |
| `cfo` | Priya Raman | Chief Financial Officer | owner | — | finance_approvals |
| `cfo` | Ana Silva | Controller | approver | pii_reconciliation | finance_approvals |
| `cfo` | Dana Whitfield | Chief Executive Officer | escalation | — | exec_briefing |
| `cro` | Marco Oliveira | Chief Revenue Officer | owner | — | exec_briefing |
| `cro` | Dana Whitfield | Chief Executive Officer | escalation | — | exec_briefing |
| `cto` | Iris Nakamura | Chief Technology Officer | owner | — | incidents |
| `cto` | Dana Whitfield | Chief Executive Officer | escalation | — | exec_briefing |
| `platform_engineer` | Jo Adeyemi | Senior Engineer | owner | — | change_review |
| `platform_engineer` | Samir Haddad | Staff Engineer | approver, reviewer | code_change | change_review |
| `platform_lead` | Samir Haddad | Staff Engineer | owner | — | change_review |
| `platform_lead` | Iris Nakamura | Chief Technology Officer | approver, escalation | code_change | incidents |
| `reconciler` | Ana Silva | Controller | owner | — | finance_approvals |
| `reconciler` | Priya Raman | Chief Financial Officer | approver, escalation | pii_reconciliation | finance_approvals |
| `sre` | Lena Fischer | SRE Lead | owner, operator | — | incidents |
| `sre` | Samir Haddad | Staff Engineer | escalation | — | incidents |
| `sre` | Iris Nakamura | Chief Technology Officer | escalation, stakeholder | — | incidents |

## People, and what they are on the hook for

| Person | Agents | Pairings |
|---|---|---|
| Ana Silva (ana@acme.example) | 2 | cfo:approver, reconciler:owner |
| Dana Whitfield (dana@acme.example) | 4 | ceo:owner, cfo:escalation, cro:escalation, cto:escalation |
| Iris Nakamura (iris@acme.example) | 3 | cto:owner, platform_lead:approver/escalation, sre:escalation/stakeholder |
| Jo Adeyemi (jo@acme.example) | 1 | platform_engineer:owner |
| Lena Fischer (lena@acme.example) | 1 | sre:owner/operator |
| Marco Oliveira (marco@acme.example) | 1 | cro:owner |
| Nadia Okonjo (nadia@acme.example) | 1 | ceo:operator/escalation |
| Priya Raman (priya@acme.example) | 3 | analyst:reviewer/escalation, cfo:owner, reconciler:approver/escalation |
| Samir Haddad (samir@acme.example) | 3 | platform_engineer:approver/reviewer, platform_lead:owner, sre:escalation |
| Tom Becker (tom@acme.example) | 1 | analyst:owner |

## What each agent may reach

| Agent | Capability | Action | Resource class | Data classes | Approval |
|---|---|---|---|---|---|
| `analyst` | `knowledge_contribute` | write | knowledge_base | public_knowledge | no |
| `analyst` | `warehouse_query` | query | finance_warehouse | finance_internal | no |
| `cfo` | `knowledge_contribute` | write | knowledge_base | public_knowledge | no |
| `cro` | `knowledge_contribute` | write | knowledge_base | public_knowledge | no |
| `cto` | `knowledge_contribute` | write | knowledge_base | public_knowledge | no |
| `platform_engineer` | `code_change` | write | source_repository | engineering_internal | yes |
| `platform_lead` | `knowledge_contribute` | write | knowledge_base | public_knowledge | no |
| `reconciler` | `pii_reconciliation` | query | customer_master | customer_pii | yes |
| `sre` | `incident_signal` | read | telemetry | engineering_internal | no |

## Sub-agents (called as tools)

| Parent | Tool | Kind | Purpose | Capabilities | Returns | Budget |
|---|---|---|---|---|---|---|
| `analyst` | `subagent_topic_research` | research | Gather context on a metric or account before analysing it. | warehouse_query | a cited findings list | 300s |
| `analyst` | `subagent_figure_check` | verify | Check every figure in a draft against the warehouse. | warehouse_query | each figure marked supported, contradicted or unverifiable | 240s |
| `analyst` | `subagent_draft_critic` | critique | Argue the strongest case against a draft analysis. | none | the weakest assumption and why it matters | 180s |
| `cfo` | `subagent_pack_reviewer` | review | Review the close pack against the accounting policy. | none | a list of concrete issues with severity | 300s |
| `platform_engineer` | `subagent_change_reviewer` | review | Review a proposed diff before humans see it. | none | blocking issues, then non-blocking suggestions | 600s |
| `platform_engineer` | `subagent_impact_research` | research | Find what else depends on the code being changed. | none | a list of dependants with evidence | 300s |
| `sre` | `subagent_incident_summary` | summarize | Summarize an incident timeline for the bridge. | none | a timeline with impact, cause and current status | 180s |

## Tools and what they wrap

| Agent | Tool | Wraps | Source | Approval |
|---|---|---|---|---|
| `analyst` | `warehouse_lookup` | capability `warehouse_query` | agent | no |
| `analyst` | `ask_market_research` | endpoint `market_research_desk` | agent | yes |
| `cfo` | `warehouse_lookup` | capability `warehouse_query` | plugin | no |

## Skills and plugins

| Agent | Skills | Plugins |
|---|---|---|
| `analyst` | sql_authoring, close_narrative | — |
| `cfo` | close_narrative | finance_pack |
| `platform_engineer` | incident_response | engineering_pack |
| `platform_lead` | incident_response | — |
| `sre` | incident_response | — |

## External agent endpoints

| Endpoint | Trust | Provides | May be sent | Approval |
|---|---|---|---|---|
| `market_research_desk` | partner | market_sizing, competitor_scan | public_knowledge | yes |

## Memory

| Agent | Session | Long term | Namespaces | Recall | May promote |
|---|---|---|---|---|---|
| `analyst` | yes | yes | query_patterns, finance_lessons | automatic | yes |
| `ceo` | yes | yes | — | on_demand | yes |
| `cfo` | yes | yes | query_patterns, company_facts | on_demand | yes |
| `cro` | yes | yes | query_patterns, company_facts | on_demand | yes |
| `cto` | yes | yes | query_patterns, company_facts | on_demand | yes |
| `platform_engineer` | yes | yes | engineering_lessons | on_demand | yes |
| `platform_lead` | yes | yes | query_patterns, company_facts | on_demand | yes |
| `reconciler` | yes | no | — | on_demand | no |
| `sre` | yes | yes | engineering_lessons | automatic | yes |

| Namespace | Scope | Groups | Data classes | Retention (days) |
|---|---|---|---|---|
| `query_patterns` | protected | finance | finance_internal, public_knowledge | 365 |
| `finance_lessons` | protected | finance | finance_internal | 730 |
| `engineering_lessons` | protected | engineering | engineering_internal | 730 |
| `company_facts` | public | — | public_knowledge | forever |

## Missions (short-lived teams)

| Mission | Objective | Leader | Members | Status | Window | Deliverables |
|---|---|---|---|---|---|---|
| `q4_forecast_rebuild` | Rebuild the Q4 revenue forecast after the EMEA pipeline restatement, a | `cfo` | cfo, analyst, cro | active | 2026-09-15 → 2026-10-31 | 2 |
| `checkout_latency_swat` | Find and remove the cause of p99 checkout latency regressions introduc | `platform_lead` | platform_lead, platform_engineer, sre | active | 2026-09-08 → 2026-10-06 | 2 |

## Models and model policy

| Agent | Bound model | Permitted classes | Cost ceiling | Regions | Approved | Sub-agent model | Fallback |
|---|---|---|---|---|---|---|---|
| `analyst` | claude-sonnet-5 | balanced, long_context | 20.0 | eu-west | not checked | — | — |
| `ceo` | claude-opus-5 | frontier_reasoning | 40.0 | eu-west | not checked | — | — |
| `cfo` | claude-sonnet-5 | balanced | 20.0 | eu-west | not checked | — | — |
| `cro` | claude-sonnet-5 | balanced | 20.0 | eu-west | not checked | — | — |
| `cto` | claude-sonnet-5 | balanced | 20.0 | eu-west | not checked | — | — |
| `platform_engineer` | claude-sonnet-5 | code, balanced | 20.0 | eu-west | not checked | — | — |
| `platform_lead` | claude-sonnet-5 | balanced | 20.0 | eu-west | not checked | — | — |
| `reconciler` | claude-sonnet-5 | balanced | 20.0 | eu-west | not checked | — | — |
| `sre` | claude-sonnet-5 | balanced | 20.0 | eu-west | not checked | — | — |

## What wakes them

| Trigger | Kind | When | Agent | Overlap / catch-up | Max runtime | Delivers to | On failure |
|---|---|---|---|---|---|---|---|
| `weekday_flash_report` | schedule | cron `0 7 * * 1-5` (Europe/London) → governed_data_request | `analyst` | skip / run_once | 900s | exec_briefing | finance_approvals |
| `month_end_close_pack` | schedule | cron `0 6 1 * *` (Europe/London) → cfo | `cfo` | queue / run_once | 3600s | finance_approvals | finance_approvals |
| `incident_triage` | event | on event 'service_health_alert' → sre | `sre` | allow / skip_missed | 600s | incidents | incidents |
| `permission_review` | schedule | cron `0 9 1 */3 *` (Europe/London) → ceo | `ceo` | skip / skip_missed | 900s | exec_briefing | exec_briefing |
| `finance_question` | message | on a message in 'finance_approvals' → analyst | `analyst` | allow / skip_missed | 600s | finance_approvals | finance_approvals |

## Human contact surfaces

| Channel | Provider | Address | Purposes | SLA | Escalation | Forbidden data |
|---|---|---|---|---|---|---|
| `incidents` | slack | #incidents | notify, handoff, report | 15 min | 2 step(s) | customer_pii |
| `finance_approvals` | msteams | Finance / Approvals | approve, ask | 120 min | 2 step(s) | customer_pii |
| `change_review` | slack | #platform-change-review | approve, report | 240 min | 1 step(s) | — |
| `exec_briefing` | smtp | exec-briefing@acme.example | report, notify | — | 0 step(s) | — |

## Declared interaction flows

Beyond the hierarchy: who may consult, notify or escalate to whom.

| From | To | Kind | Why |
|---|---|---|---|
| `analyst` | `sre` | consult | May ask about data pipeline health |
| `cro` | `cfo` | consult | Revenue may consult finance on forecast assumptions. |
| `platform_engineer` | `analyst` | notify | Tells finance when a warehouse schema change lands. |
| `sre` | `cto` | escalate | Raises SEV1 incidents directly |
| `reconciler` | `cfo` | escalate | Raises identity mismatches for the CFO to decide. |

## Grounding sources

| Source | Kind | Provider | Data classes | Citation required |
|---|---|---|---|---|
| `finance_handbook` | wiki | wiki | finance_internal | yes |
| `engineering_runbooks` | document_store | document_store | engineering_internal | yes |
| `company_glossary` | wiki | wiki | public_knowledge | no |

## Promotion gates

| To stage | Requires | Min pass rate | Approvers |
|---|---|---|---|
| staging | owner_assigned, evaluations_passed | 80% | — |
| production | evaluations_passed, human_approval, security_review, cost_within_budget, permissions_reviewed | 100% | priya@acme.example, iris@acme.example |

## Evaluation gate

`evaluations_passed` as of the last recorded run; `not_evaluated` means nobody
has run the declared cases, which is not the same as having failed them.

| Agent | `evaluations_passed` |
|---|---|
| `analyst` | not_evaluated |
| `ceo` | not_evaluated |
| `cfo` | not_evaluated |
| `cro` | not_evaluated |
| `cto` | not_evaluated |
| `platform_engineer` | not_evaluated |
| `platform_lead` | not_evaluated |
| `reconciler` | not_evaluated |
| `sre` | not_evaluated |

## Review flags

- **Agents without a human owner:** none
- **Agents without a budget:** none
- **Agents that never run unattended:** `reconciler`, `cto`, `platform_lead`, `platform_engineer`, `cro`
- **Agents with a single paired human:** none
- **Agents with no long-term memory:** `reconciler`
- **Agents on an unapproved model:** none
- **Agents moved to a fallback model:** none
- **Agents whose model verdict used a stale catalog figure:** none
- **Missions with no end date:** none
- **Compliance frameworks:** SOC2, GDPR
- **Data residency:** europe-west, eu-west, westeurope
- **Redacted from traces:** customer_pii
