---
id: ADR-0084
title: Placement becomes a VPC with per-placement subnets and identity-scoped firewall
status: Accepted
version: 1.0.0
date: 2026-09-22
updated: 2026-09-22
deciders: [Platform Architecture, Security Engineering]
consulted: [Runtime Engineering]
informed: [All engineering]
scope: [compiler]
workstreams: [WS-003, WS-028]
supersedes: []
superseded_by: []
related: [ADR-0012, ADR-0015, ADR-0069, ADR-0050, ADR-0082]
tags: [security, network, terraform, gcp, placement]
---

# ADR-0084: Placement becomes a VPC with per-placement subnets and identity-scoped firewall

## Context
The placement model (ADR-0069) resolves, for every design, which agents share
a sandbox, which placements may reach which, and why — a shared channel, a
declared flow, or the manager chain. The IR carries all of it: `placements`
(unit × environment, with their agents) and `placement_rules` (the permitted
cross-placement paths, each tagged with its justification).

The `terraform:gcp` target dropped it. It emitted a Cloud Run service and a
service account per agent, and project-level IAM bindings — real identity, but
it wrote the network posture as a *label* on the service and created no VPC, no
subnet, no firewall. Its own `MAPPING.md` listed `network_boundary →
google_compute_network` in the table and then emitted no such resource. So the
isolation the design promised — teams apart by default, talking only over
official channels — existed in the IR and nowhere in the generated Google
Cloud. A reviewer reading the output would see "network-posture = none" on a
service that could in fact reach anything the project could.

## Decision
The Terraform targets translate the placement model into real network
isolation, in a new `network.tf`, when the provider profile declares a network
mapping (GCP does today):

- **one VPC** per system;
- **one subnet per placement** (unit × sandbox), each with its own CIDR;
- a **default-deny** firewall between placements, then **one allow rule per
  placement rule** the IR resolved — and only those. Because the placement
  rules *are* the declared channels, flows and manager chain, "teams talk only
  through official channels" becomes exactly the set of allow rules; everything
  else is denied;
- an **egress-deny** on every `none`-posture sandbox, so an agent in the clean
  room, the deal room or the financial-crime enclave cannot call home.

The allow rules are scoped to the agents' **service accounts**, not to IP
ranges. The workload identities in `iam.tf` (ADR-0015) are what a placement
rule is really about — "the AML investigator's identity may reach the auditor's
identity because they share the control-room channel" — so the network
boundary and the identity are one fact, and a rule reads as the sentence it
came from. Each sandbox job attaches to its placement's subnet, so the
firewall governs the workload rather than describing it.

A provider profile without a network mapping emits a `network.tf` that says,
in the file, that isolation was **not** generated — never a silent gap.

## Scope
The Terraform target's generation only. It reads `placements` and
`placement_rules` from the IR, which already existed; it adds no spec field and
changes no other target. GCP gets the mapping in this ADR; AWS and Azure
profiles declare none yet and say so in their output.

## Implementation
**Implemented for GCP.** `NetworkProfile` on the provider profile names the
VPC, subnet and firewall resources and whether firewall rules can be scoped to
service accounts (GCP: yes). `_network` emits the VPC, subnets, the default
deny, an intra-placement allow, one allow per placement rule (SA→SA, commented
with the rule's reason and `via`), and an egress deny per closed sandbox.

## Timeline
Phase 6, with the multinational example that exercises it.

## Advantages
- The isolation the design draws is the isolation the cloud enforces, not a
  label describing one.
- Identity-scoped rules mean the network policy and the IAM identities cannot
  drift: a rule names the same service accounts the bindings create.
- Every allow rule carries the reason it exists, so a reviewer sees *why* two
  teams may talk, which is the audit question.

## Disadvantages
- A firewall rule cannot express a **hostname** egress allowlist; it works on
  IP ranges and service accounts. An `allowlist`-posture sandbox still needs an
  egress proxy or Cloud NAT with an FQDN policy, wired in `overlays/`.
  `network.tf` names which sandboxes that applies to rather than pretending the
  firewall covers it.
- CIDRs are allocated deterministically from `10.8.0.0/8`; a design with a
  colliding corporate range overrides them in `overlays/`.
- AWS and Azure now visibly lack what GCP has, until their profiles gain a
  network mapping.

## Alternatives considered
- **Tag-based firewall rules.** Network tags are mutable and unauthenticated;
  service-account scoping ties the rule to the workload identity, which is the
  thing the placement rule is about.
- **One subnet, security groups only.** Collapses the per-placement blast
  radius the model is built on; a subnet per placement is the honest mapping.
- **Leave it to the landing zone.** What the target did before. It made the
  isolation unverifiable from the output, which is the defect.

## Verification
Tests assert: a VPC and one subnet per placement are emitted; a default-deny
firewall exists; each `placement_rule` produces exactly one allow rule scoped
to the right source and target service accounts; a `none`-posture sandbox gets
an egress deny; a design whose placements have no cross-rule gets no allow
rules beyond the intra-placement ones; and the FQDN-allowlist limitation is
stated in the file.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-22 | Accepted. The GCP target emits a VPC, per-placement subnets and identity-scoped firewall rules implementing the placement model; closed sandboxes get an egress deny; the hostname-allowlist gap is documented, not hidden. |
