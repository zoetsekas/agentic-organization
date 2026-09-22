# Agent registry — helios

Generated from the system spec (spec_version 1.4.0) for target
`terraform:gcp`. This is the fleet inventory: every agent, its owner, its identity,
what it may reach, what wakes it and where it talks to people.

**Lifecycle stage:** development ·
**Owner:** Helios Platform Engineering ·
**Permission review:** every 90 days ·
**Retire after idle:** 180 days

## Agents

| Agent | Team | Human owner | Identity | Perms | Environment | Triggers | Channels | Budget |
|---|---|---|---|---|---|---|---|---|
| `bioinfo_agent` | Helios Therapeutics / Research and Development / Bioinformatics | Aabir Sarkar +1 | `id-bioinfo_agent` | 3 | phi_enclave, hpc | interactive only | — | $20,000/monthly (throttle) |
| `biostat_agent` | Helios Therapeutics / Clinical Development / Biostatistics | Helena Brandt +1 | `id-biostat_agent` | 3 | clinical_analysis, phi_enclave | interactive only | — | $20,000/monthly (throttle) |
| `ceo_agent` | Helios Therapeutics | Yuki Tanaka | `id-ceo_agent` | 1 | — | interactive only | exec_briefing | $20,000/monthly (throttle) |
| `cmo_agent` | Helios Therapeutics / Clinical Development | Helena Brandt +1 | `id-cmo_agent` | 1 | — | interactive only | clinical_escalations, exec_briefing | $20,000/monthly (throttle) |
| `commercial_agent` | Helios Therapeutics / Commercial | Diego Marchetti +1 | `id-commercial_agent` | 5 | commercial_analysis | interactive only | — | $20,000/monthly (throttle) |
| `cso_agent` | Helios Therapeutics / Research and Development | Aabir Sarkar +1 | `id-cso_agent` | 1 | — | interactive only | exec_briefing | $8,000/monthly (warn) |
| `discovery_agent` | Helios Therapeutics / Research and Development / Discovery | Aabir Sarkar +1 | `id-discovery_agent` | 3 | discovery_lab | interactive only | — | $20,000/monthly (throttle) |
| `process_agent` | Helios Therapeutics / Manufacturing / Process Development | Sofia Delgado +1 | `id-process_agent` | 3 | gmp_ops | interactive only | — | $20,000/monthly (throttle) |
| `pv_agent` | Helios Therapeutics / Clinical Development / Pharmacovigilance | Olumide Bakare +1 | `id-pv_agent` | 3 | safety_review | interactive only | clinical_escalations | $20,000/monthly (throttle) |
| `qa_agent` | Helios Therapeutics / Manufacturing / Quality Assurance | Rahul Menon +1 | `id-qa_agent` | 3 | gmp_ops | interactive only | — | $20,000/monthly (throttle) |
| `regulatory_agent` | Helios Therapeutics / Regulatory Affairs | Claire Ferrand +1 | `id-regulatory_agent` | 2 | regulatory_review | interactive only | — | $20,000/monthly (throttle) |
| `trial_manager_agent` | Helios Therapeutics / Clinical Development / Trial Operations | Helena Brandt +1 | `id-trial_manager_agent` | 3 | clinical_analysis, phi_enclave | interactive only | — | $20,000/monthly (throttle) |
| `vp_mfg_agent` | Helios Therapeutics / Manufacturing | Sofia Delgado +1 | `id-vp_mfg_agent` | 1 | — | interactive only | — | $20,000/monthly (throttle) |

## Who each agent answers to

| Agent | Person | Title | Roles | Approves | Channel |
|---|---|---|---|---|---|
| `bioinfo_agent` | Aabir Sarkar | Chief Scientific Officer | owner | — | — |
| `bioinfo_agent` | Helena Brandt | Chief Medical Officer | approver | — | — |
| `biostat_agent` | Helena Brandt | Chief Medical Officer | owner | — | — |
| `biostat_agent` | Olumide Bakare | Head of Pharmacovigilance | approver | — | — |
| `ceo_agent` | Yuki Tanaka | Chief Executive Officer | owner | — | — |
| `cmo_agent` | Helena Brandt | Chief Medical Officer | owner | — | — |
| `cmo_agent` | Yuki Tanaka | Chief Executive Officer | approver | — | — |
| `commercial_agent` | Diego Marchetti | Chief Commercial Officer | owner | — | — |
| `commercial_agent` | Yuki Tanaka | Chief Executive Officer | approver | — | — |
| `cso_agent` | Aabir Sarkar | Chief Scientific Officer | owner | — | — |
| `cso_agent` | Yuki Tanaka | Chief Executive Officer | approver | — | — |
| `discovery_agent` | Aabir Sarkar | Chief Scientific Officer | owner | — | — |
| `discovery_agent` | Yuki Tanaka | Chief Executive Officer | approver | — | — |
| `process_agent` | Sofia Delgado | VP Manufacturing | owner | — | — |
| `process_agent` | Rahul Menon | Quality Assurance Director | approver | — | — |
| `pv_agent` | Olumide Bakare | Head of Pharmacovigilance | owner | — | — |
| `pv_agent` | Helena Brandt | Chief Medical Officer | approver | — | — |
| `qa_agent` | Rahul Menon | Quality Assurance Director | owner | — | — |
| `qa_agent` | Sofia Delgado | VP Manufacturing | approver | — | — |
| `regulatory_agent` | Claire Ferrand | Head of Regulatory Affairs | owner | — | — |
| `regulatory_agent` | Helena Brandt | Chief Medical Officer | approver | — | — |
| `trial_manager_agent` | Helena Brandt | Chief Medical Officer | owner | — | — |
| `trial_manager_agent` | Olumide Bakare | Head of Pharmacovigilance | approver | — | — |
| `vp_mfg_agent` | Sofia Delgado | VP Manufacturing | owner | — | — |
| `vp_mfg_agent` | Rahul Menon | Quality Assurance Director | approver | — | — |

## People, and what they are on the hook for

| Person | Agents | Pairings |
|---|---|---|
| Aabir Sarkar (aabir.sarkar@helios.example) | 3 | bioinfo_agent:owner, cso_agent:owner, discovery_agent:owner |
| Claire Ferrand (claire.ferrand@helios.example) | 1 | regulatory_agent:owner |
| Diego Marchetti (diego.marchetti@helios.example) | 1 | commercial_agent:owner |
| Helena Brandt (helena.brandt@helios.example) | 6 | bioinfo_agent:approver, biostat_agent:owner, cmo_agent:owner, pv_agent:approver, regulatory_agent:approver, trial_manager_agent:owner |
| Olumide Bakare (olumide.bakare@helios.example) | 3 | biostat_agent:approver, pv_agent:owner, trial_manager_agent:approver |
| Rahul Menon (rahul.menon@helios.example) | 3 | process_agent:approver, qa_agent:owner, vp_mfg_agent:approver |
| Sofia Delgado (sofia.delgado@helios.example) | 3 | process_agent:owner, qa_agent:approver, vp_mfg_agent:owner |
| Yuki Tanaka (yuki.tanaka@helios.example) | 5 | ceo_agent:owner, cmo_agent:approver, commercial_agent:approver, cso_agent:approver, discovery_agent:approver |

## What each agent may reach

| Agent | Capability | Action | Resource class | Data classes | Approval |
|---|---|---|---|---|---|
| `bioinfo_agent` | `genomics_pipeline` | invoke | hpc_cluster | genomic_phi | yes |
| `bioinfo_agent` | `model_training` | write | hpc_cluster | research_ip | yes |
| `biostat_agent` | `statistical_analysis` | query | stats | trial_data, genomic_phi | no |
| `biostat_agent` | `unblinding` | write | randomization | trial_data | yes |
| `commercial_agent` | `label_review` | approve | regulatory_portal | regulatory_dossier, public_knowledge | yes |
| `commercial_agent` | `market_analysis` | query | market_db | market_data | no |
| `commercial_agent` | `medical_content` | approve | content | public_knowledge | yes |
| `commercial_agent` | `pricing` | write | market_db | market_data | yes |
| `discovery_agent` | `compound_screen` | invoke | screening | research_ip | no |
| `discovery_agent` | `research_publish` | publish | publications | research_ip, public_knowledge | yes |
| `process_agent` | `batch_quarantine` | write | mes | batch_records | no |
| `process_agent` | `batch_release` | approve | mes | batch_records | yes |
| `pv_agent` | `ae_intake` | write | safety_db | adverse_events | yes |
| `pv_agent` | `safety_reporting` | write | safety_db | adverse_events, regulatory_dossier | yes |
| `qa_agent` | `batch_quarantine` | write | mes | batch_records | no |
| `qa_agent` | `deviation_signoff` | approve | qms | batch_records | yes |
| `regulatory_agent` | `dossier_authoring` | write | regulatory_portal | regulatory_dossier | yes |
| `trial_manager_agent` | `enrollment` | write | ctms | trial_data, genomic_phi | yes |
| `trial_manager_agent` | `protocol_authoring` | write | ctms | trial_data, regulatory_dossier | yes |

## Sub-agents (called as tools)

| Parent | Tool | Kind | Purpose | Capabilities | Returns | Budget |
|---|---|---|---|---|---|---|
| `trial_manager_agent` | `subagent_eligibility_screener` | extract | Check a candidate against the protocol's inclusion criteria. | enrollment | a per-criterion eligibility verdict | 300s |

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
| `bioinfo_agent` | yes | yes | — | on_demand | yes |
| `biostat_agent` | yes | yes | — | on_demand | yes |
| `ceo_agent` | yes | yes | — | on_demand | yes |
| `cmo_agent` | yes | yes | — | on_demand | yes |
| `commercial_agent` | yes | yes | trial_lessons, manufacturing_lessons | on_demand | yes |
| `cso_agent` | yes | yes | — | on_demand | yes |
| `discovery_agent` | yes | yes | trial_lessons, manufacturing_lessons | on_demand | yes |
| `process_agent` | yes | yes | — | on_demand | yes |
| `pv_agent` | yes | yes | — | on_demand | yes |
| `qa_agent` | yes | yes | — | on_demand | yes |
| `regulatory_agent` | yes | yes | — | on_demand | yes |
| `trial_manager_agent` | yes | yes | — | on_demand | yes |
| `vp_mfg_agent` | yes | yes | — | on_demand | yes |

| Namespace | Scope | Groups | Data classes | Retention (days) |
|---|---|---|---|---|
| `trial_lessons` | protected | clinical | public_knowledge | 730 |
| `manufacturing_lessons` | protected | manufacturing | public_knowledge | 730 |

## Missions (short-lived teams)

| Mission | Objective | Leader | Members | Status | Window | Deliverables |
|---|---|---|---|---|---|---|
| `helios_301_readout` | After database lock, run the pre-registered primary analysis of the pi | `cmo_agent` | cmo_agent, trial_manager_agent, biostat_agent, pv_agent, regulatory_agent | active | 2026-10-01 → 2027-01-31 | 2 |

## Models and model policy

| Agent | Bound model | Permitted classes | Cost ceiling | Regions | Approved | Sub-agent model | Fallback |
|---|---|---|---|---|---|---|---|
| `bioinfo_agent` | claude-opus-5 | balanced | — | any | not checked | — | — |
| `biostat_agent` | claude-opus-5 | balanced | — | any | not checked | — | — |
| `ceo_agent` | claude-opus-5 | balanced | — | any | not checked | — | — |
| `cmo_agent` | claude-opus-5 | balanced | — | any | not checked | — | — |
| `commercial_agent` | claude-opus-5 | balanced | — | any | not checked | — | — |
| `cso_agent` | claude-opus-5 | balanced | — | any | not checked | — | — |
| `discovery_agent` | claude-opus-5 | balanced | — | any | not checked | — | — |
| `process_agent` | claude-opus-5 | balanced | — | any | not checked | — | — |
| `pv_agent` | claude-opus-5 | balanced | — | any | not checked | — | — |
| `qa_agent` | claude-opus-5 | balanced | — | any | not checked | — | — |
| `regulatory_agent` | claude-opus-5 | balanced | — | any | not checked | — | — |
| `trial_manager_agent` | claude-opus-5 | balanced | — | any | not checked | — | — |
| `vp_mfg_agent` | claude-opus-5 | balanced | — | any | not checked | — | — |

## What wakes them

| Trigger | Kind | When | Agent | Overlap / catch-up | Max runtime | Delivers to | On failure |
|---|---|---|---|---|---|---|---|
| — |

## Human contact surfaces

| Channel | Provider | Address | Purposes | SLA | Escalation | Forbidden data |
|---|---|---|---|---|---|---|
| `clinical_escalations` | slack | #clinical-escalations | ask, notify | — | 0 step(s) | — |
| `exec_briefing` | smtp | exec-briefing@helios.example | notify | — | 0 step(s) | — |

## Declared interaction flows

Beyond the hierarchy: who may consult, notify or escalate to whom.

| From | To | Kind | Why |
|---|---|---|---|
| `trial_manager_agent` | `pv_agent` | escalate |  |
| `bioinfo_agent` | `discovery_agent` | notify |  |
| `biostat_agent` | `cmo_agent` | consult |  |
| `process_agent` | `qa_agent` | escalate |  |
| `regulatory_agent` | `commercial_agent` | consult |  |
| `pv_agent` | `cmo_agent` | notify |  |

## Grounding sources

| Source | Kind | Provider | Data classes | Citation required |
|---|---|---|---|---|
| — |

## Promotion gates

| To stage | Requires | Min pass rate | Approvers |
|---|---|---|---|
| production | evaluations_passed | 100% | — |

## Evaluation gate

`evaluations_passed` as of the last recorded run; `not_evaluated` means nobody
has run the declared cases, which is not the same as having failed them.

| Agent | `evaluations_passed` |
|---|---|
| `bioinfo_agent` | not_evaluated |
| `biostat_agent` | not_evaluated |
| `ceo_agent` | not_evaluated |
| `cmo_agent` | not_evaluated |
| `commercial_agent` | not_evaluated |
| `cso_agent` | not_evaluated |
| `discovery_agent` | not_evaluated |
| `process_agent` | not_evaluated |
| `pv_agent` | not_evaluated |
| `qa_agent` | not_evaluated |
| `regulatory_agent` | not_evaluated |
| `trial_manager_agent` | not_evaluated |
| `vp_mfg_agent` | not_evaluated |

## Review flags

- **Agents without a human owner:** none
- **Agents without a budget:** none
- **Agents that never run unattended:** `ceo_agent`, `cso_agent`, `discovery_agent`, `bioinfo_agent`, `cmo_agent`, `trial_manager_agent`, `biostat_agent`, `pv_agent`, `vp_mfg_agent`, `process_agent`, `qa_agent`, `regulatory_agent`, `commercial_agent`
- **Agents with a single paired human:** `ceo_agent`
- **Agents with no long-term memory:** none
- **Agents on an unapproved model:** none
- **Agents moved to a fallback model:** none
- **Agents whose model verdict used a stale catalog figure:** none
- **Missions with no end date:** none
- **Compliance frameworks:** none declared
- **Data residency:** unrestricted
- **Redacted from traces:** nothing
