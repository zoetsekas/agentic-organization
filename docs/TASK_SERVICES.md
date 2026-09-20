# Task services: giving agents work somebody assigned

Researched September 2026. The platform has triggers, channels and approval
routing (ADR-0026), but no object a human can create, hand to an agent, watch
and close. This document evaluates existing work-tracking services we could
integrate rather than build, against the eight criteria that actually decide
it. It is not an ADR and makes no decision; it makes a recommendation and
argues against it.

One constraint on the research itself: this environment's egress proxy blocks
`plane.so`, `developers.plane.so` and `www.openproject.org`. Plane's and
OpenProject's own documentation could therefore only be read through search
result extracts and through their GitHub repositories (`opf/openproject`
carries its docs in-tree, which is how the OpenProject API and webhook claims
below were verified). Every claim sourced only from a search extract is marked
as such, and section 6 lists what stayed unverified.

---

## 1. The shortlist at a glance

| | **Plane** | **OpenProject** | **Taiga** | **Vikunja** | **GitLab (issues)** | **Gitea (issues)** | **Redmine** | **Kaneo** | **Huly** |
|---|---|---|---|---|---|---|---|---|---|
| **API** | REST, versioned `/api/v1/`, documented | REST HAL+JSON, API v3 + OpenAPI 3.1, SCIM | REST, documented, broad (epics→wiki) | REST + OpenAPI/Swagger | REST **and** GraphQL, the most complete here | REST, swagger-generated, 34 issue ops | REST, stable for a decade | REST, plus a built-in MCP endpoint | Typed API *client*, not a documented HTTP API |
| **Auth** | API key (PAT); OAuth for apps | Basic-auth API key, OAuth 2.0 | Bearer token + application tokens | JWT + API tokens | PAT, project/group tokens, **service-account tokens** | PAT, OAuth2/PKCE, OIDC | API key / basic auth | OAuth 2.1 + PKCE, API key bearer, device auth | Client credentials via API client |
| **Rate limits** | 60 req/min per key, `X-RateLimit-*` headers; self-host tunable | not documented | not documented | not documented | documented, configurable self-managed | configurable | none by default | not documented | not documented |
| **MCP** | **Official** `makeplane/plane-mcp-server` — 30 tools / 204 ops | **Official but Enterprise-only** | none found | none found | official GitLab MCP exists (see §4) | community only | community only | **first-party**, built into the product | community `dearlordylord/huly-mcp` |
| **Webhooks** | Yes, workspace-level, with filters | Yes, core (work packages, comments, time, attachments) | Yes, signed, with diffs | Yes | Yes, extensive | Yes, 4 scopes incl. system-wide | Native from 7.0; plugins before | Plugin/event architecture incl. generic webhooks | GitHub-integration webhooks only |
| **Custom fields** | Custom properties **per work item type**, over the API | Custom fields core; **workflows** core, some display paid | Per-project custom fields on all object types | Limited | Custom fields + configurable types = **Premium/Ultimate** | Labels only, no custom fields found | Custom fields + per-tracker workflow, core | Minimal | Rich, but undocumented externally |
| **Self-host / licence** | Docker Compose + Helm; **AGPL-3.0** community, closed commercial edition | Docker/Helm; GPL-3.0 core + proprietary Enterprise add-ons | Docker; **MPL-2.0**, no paid tier | Docker; AGPL-3.0 (+ hosted Pro) | Omnibus/Helm; MIT core + proprietary EE | Docker; MIT | Docker/packages; GPL-2.0 | Docker/K8s; **MIT** | Docker Compose; EPL-2.0 |
| **Non-human principal** | **Yes — bot users, assignable, non-billable** | Placeholder users are assignable but **Enterprise** and cannot log in | No bot concept found | No bot concept found | **Yes — service accounts, Premium/Ultimate** | Any user can be a bot; no distinct type | Any user can be a bot | API keys per account | not documented |
| **Multi-tenancy** | Multiple workspaces per instance; unlimited/isolated workspaces marketed as Enterprise Grid | **No native multi-tenancy** — instance per tenant | Instance per tenant | Instance per tenant | Groups, but one identity domain | Organizations | Instance per tenant | Workspaces | Workspaces |
| **Maturity** | 59.7k★, AGPL, very active | long-lived, ~monthly releases, 17.8 on 2 Sep 2026 | 6.10.1 on 6 May 2026; code moving, Docker issues stale | 5.5k★, 15.7k commits, active | enterprise-grade | active | 7.0.1 on 26 Aug 2026 | 9.1k★ MIT, ~3k commits, young | 27.7k★, EPL-2.0, active |

Not on the shortlist, with evidence:

- **Focalboard — dormant as a product.** Mattermost stopped bundling it on
  15 September 2023 and staff "are no longer actively reviewing or merging pull
  requests"; the plugin moved to `mattermost/mattermost-plugin-boards` and the
  standalone repository is community-maintained with an open *Call for
  Maintainers* issue (#5038). Commits continue; a roadmap does not. Rejected.
- **Wekan — alive but not trustworthy as an integration target.** Release
  cadence is extreme (v11.89 on 20 September 2026, near-daily), but the
  failure mode that matters is webhooks: an independent 2026 audit reports
  webhooks that "fire but don't arrive", problems disabling and deleting them,
  and that "not all Wekan activities create outgoing webhook events". A task
  service whose events we cannot trust forces us back to polling, which is the
  thing we are trying to avoid. Rejected, not for death but for reliability.
- **Leantime — alive, wrong shape.** v3.9.8 on 8 July 2026, and the release
  notes even mention an MCP endpoint. But its model is goals/milestones and
  non-technical project management, and its API is the weakest of the
  candidates for the create/assign/transition/close loop. Not rejected on
  maturity; rejected on fit.
- **"Tracker"-style** we read as the Linear-shaped category: Huly's Tracker is
  the self-hostable member of it, and is covered below. Linear itself is SaaS
  with no self-hosting and is therefore out under ADR-0050's tenant boundary.

---

## 2. The serious candidates

### Plane — the only one that already has our problem

**What it is.** An AGPL-3.0 Jira alternative, 59.7k stars, Django + React,
Docker Compose and Kubernetes self-hosting. Work items, cycles, modules,
intake, custom work item types with typed custom properties.

**What it gives us.** Three things nothing else on this list gives together.
First, an **official MCP server** — `makeplane/plane-mcp-server`, vendor-owned,
rebuilt in Python on FastMCP, "30 tools, one per Plane resource, covering 204
operations", and it explicitly supports self-hosted instances via
`PLANE_BASE_URL`. Second, **custom properties attached to work item types**, so
"which agent, which session, which mission, which approval" becomes a typed
schema on an `Agent Task` type rather than a convention in a description field,
and the schema is readable over the API. Third — and this is the decisive one —
**a first-class non-human principal**: installing an agent via the OAuth consent
flow "creates a bot user for the agent in that workspace", the bot user ID comes
back in the installation details, the agent is @-mentionable in comments and
"you can add an agent as an assignee, same as any teammate", and agents "do not
count as billable users".

**What it would cost us.** The licence is AGPL-3.0, which matters the moment we
offer a tenant a hosted Plane: if we modify it and serve it, we publish the
modifications. Integrating over the API without modifying it does not trigger
that, but any patch does, and we should assume we will want patches.

The sharper cost is the edition boundary, and there is direct evidence of where
it sits. PR #9399 on `makeplane/plane` added API-provisionable service accounts
(a bot user of type `SERVICE`, a workspace membership and a workspace-scoped
token, created atomically, with interactive login blocked). It was opened
11 July 2026 and **closed unmerged** on 17 September 2026, with the maintainer
saying verbatim: *"Service accounts touch the account and permission model,
which we keep on the commercial side, so it isn't a change we can take into the
community edition."* So the agent-identity story is real, but the *programmatic
provisioning* of agent identities — exactly what a compiler that generates N
agents per tenant needs — is a declared commercial boundary. There is a
community fork (`Liewzheng/planebot`) carrying the patch; depending on a fork of
a vendor's account model is not a plan.

Rate limit: 60 requests per minute per key, with `X-RateLimit-Remaining` and
`X-RateLimit-Reset` headers. Self-hosted instances expose
`env.api_key_rate_limit`, though issue #9034 reports the Helm value not
overriding the default — so treat 60/min as the working assumption.

### OpenProject — the governance story, with a paywall in the wrong place

**What it is.** A long-lived GPL-3.0 project-management server with a
HAL+JSON REST API v3, an OpenAPI 3.1 spec in-tree, SCIM, and BCF. Releases are
roughly monthly (17.8 on 2 September 2026).

**What it gives us.** The most conservative, best-documented API of the
shortlist, with an explicit compatibility promise: *"We strive to maintain
backward compatibility with this API in our stable OpenProject releases
whenever possible."* Custom fields and per-type workflows are core. Webhooks
are core and cover "updating or creating projects, work packages, work package
comments, time entries and attachments", scoped per project. SCIM means agent
identities could in principle be provisioned the same way human ones are.

**What it would cost us.** The paywall lands on precisely our two hardest
criteria. The **MCP server is an Enterprise add-on**, available only on
Enterprise cloud or Enterprise on-premises with a Professional, Premium or
Corporate plan — 17.8 extended it to create and update work packages, add
comments and manage relations, which is the useful half, and it is the half we
cannot have on the community edition. **Placeholder users** — the nearest thing
to an assignable non-human — are likewise an Enterprise add-on, cannot be added
to groups, cannot hold global roles, and by construction have no login, so a
placeholder cannot *act*; it can only be pointed at. **SSO (SAML and OIDC) is an
Enterprise add-on.** And there is **no native multi-tenancy**: the feature
request (#54282) is open, the third-party companies plugin "tends to cause
errors in most admin modules", so under ADR-0050 it is one instance per tenant.

The honest summary: OpenProject's community edition is a good ticket system and
a poor agent substrate; its Enterprise edition is a good agent substrate whose
price scales with something other than our usage.

### Taiga — clean, permissive, and alone

MPL-2.0 with no paid tier, which is the friendliest licence here — no AGPL
publication duty, no feature paywall. The REST API is broad (projects, sprints,
user stories, issues, tasks, epics, wiki, webhooks, membership), webhooks are
signed POSTs carrying diffs and the acting user, and **per-project custom fields
exist on epics, stories, tasks and issues**, stored as JSON so new fields need
no migration. That covers criteria 1, 3, 4 and 5 well.

It fails on the two newest criteria. **No MCP server was found, official or
credible community.** And nothing in the documentation describes a bot or
service principal — an agent would hold a human-shaped account. Maturity is
mixed rather than dead: 6.10.1 shipped 6 May 2026 and `taiga-back` and
`taiga-front` saw commits in September 2026, but `taiga-docker` carries open
bugs from December 2025 and March 2026, and the `kaleidos-ventures/taiga`
repository has **no published releases at all**, which makes "which repository
is the product" a live question.

### GitLab issues as a task backend — the most capable, at the wrong altitude

Best-in-class on the mechanical criteria: REST *and* GraphQL, the deepest
webhook surface, and genuine **service accounts** that "do not use a licensed
seat" — a real non-human principal that can own a token, be an assignee and act
as itself. That is criterion 6 answered properly.

The costs are two. **Custom fields and configurable work item types are Premium
or Ultimate**, self-managed included; on the MIT-licensed core we would be back
to encoding agent/session/mission in labels and description text, which is the
failure we are trying to design out. And service accounts are Premium/Ultimate
too. Second, GitLab is not multi-tenant in ADR-0050's sense — groups are an
authorization tree inside one identity domain, not an isolation boundary — so
this is an instance per tenant, of the heaviest thing on the list.

### Gitea issues — the cheap option, and honestly cheap

MIT, small, fast, self-hosted, with a versioned swagger API (34 issue
operations), OAuth2/PKCE and OIDC, and webhooks at four scopes including
*system* webhooks for all eligible instance activity. If our tenants already run
Gitea, using it costs almost nothing.

But issues have **labels and milestones, no custom fields** — we found no
custom-field support — so every agent-specific attribute becomes a label
convention or a machine-readable comment block. There is no bot principal type;
a bot is a user you decided is a bot. No official MCP server. It is a reasonable
*secondary* backend, not a primary one.

### Redmine — the boring one that keeps shipping

GPL-2.0, releasing across four parallel branches (7.0.1, 6.1.4 and 6.0.11 all
tagged 26 August 2026 — this is not a dormant project). Custom fields on nearly
every object and per-tracker workflows are core and free, which is more than
GitLab or OpenProject give away. Webhooks are **native from 7.0**; before that
they were plugins, and plugin quality varies. Against it: no official MCP
server, no bot principal, no multi-tenancy, and a UI that will not persuade a
human to live in it.

### Kaneo — the most interesting young option

MIT, 9.1k stars, ~3k commits, Docker and Kubernetes. Uniquely, **MCP is built
into the product**: an HTTP endpoint at `/api/mcp` with OAuth 2.1 + PKCE, an
API-key bearer path, and an `@kaneo/mcp` stdio package using device
authorization — a first-party MCP surface with a real authorization model, which
is better than most vendors manage. A plugin architecture carries task events to
Slack, Discord, Telegram, GitHub, Gitea and generic webhooks.

Against it: it is young and deliberately minimal — "all you need, nothing you
don't" — and we could not confirm custom fields, custom states or any
non-human principal beyond per-account API keys. Minimalism is the wrong
property for criterion 4. Worth revisiting in a year; not a foundation today.

### Huly — powerful, undocumented at the integration seam

27.7k stars, EPL-2.0, Docker Compose self-hosting via `huly-selfhost`, and its
Tracker is the closest self-hostable thing to Linear. But integration-wise it
offers a **typed API client**, not a documented HTTP API; the only webhooks we
could find are for its GitHub integration; the MCP server is community
(`dearlordylord/huly-mcp`); and some cloud features are deliberately excluded
from self-hosting. Too much of the integration surface is unverifiable to build
a tenant-facing dependency on.

---

## 3. Recommendation

**Integrate Plane, self-hosted, one instance per tenant, with our own
task-mapping layer in front of it — and design that layer so the backend is
replaceable.**

The reasoning is narrow. Six of the eight criteria are met by several
candidates; only two separate them. Criterion 2 (MCP) and criterion 6 (a
non-human principal that can be an assignee and act as itself) are met together
by exactly one project whose useful half is not behind a paywall. Plane's MCP
server is vendor-owned, covers 204 operations and works against self-hosted
instances. Plane's bot users are assignable, @-mentionable and non-billable — an
agent appears in the org's task board *as itself*, which is the whole point of
ADR-0026's accountable-owner model and is impossible with OpenProject's
login-less placeholder users or with a human's borrowed credentials anywhere
else. Custom properties on custom work item types give us a typed home for
`agent`, `session`, `mission` and `approval` without patching the product.
Taiga's licence is friendlier and GitLab's API is stronger, but neither gets an
agent an identity we can live with on the free tier.

**The strongest argument against this recommendation**, which is strong: the
edition boundary runs straight through the feature we are choosing Plane for.
The maintainer's rejection of PR #9399 is not a shrug about one PR — it is a
statement that the account and permission model is *kept on the commercial
side*. Our platform provisions agents by compilation: a tenant publishes a spec
and N agent identities should appear. Plane's community edition gives us bot
users through an interactive OAuth app install performed by a workspace admin,
and declines to give us an API to mint them. So the very first thing we would
automate is the thing the vendor has said it will not support for free, and the
workarounds are a fork, a headless browser, or direct database writes — all
three disqualifying for a tenant-facing dependency. A reasonable person reading
that same evidence would choose Taiga (MPL-2.0, no paywall to run into, accept
that agents hold human-shaped accounts and that we write the MCP server
ourselves) and would not be wrong; the counter is that writing an MCP server is
a known cost we can pay once, while a vendor's commercial boundary moving toward
us is a cost we cannot bound. That argument cuts both ways, and it is the
decision to be made.

A defensible middle path: build the mapping layer against Plane, keep a second
adapter for Gitea issues as a proof that the layer is not Plane-shaped, and
measure how much of the integration turns out to be backend-specific before
committing the platform.

---

## 4. What integration requires from us, whichever we choose

No product on this list knows what an agent run is. Four pieces are ours.

**A task maps to a run, not to an agent.** A task is an *instruction with an
accountable owner*; a run is one bounded execution. The relation is one-to-many
and must be explicit: a task carries zero or more run IDs, and a run carries
exactly one task ID or none (triggered and scheduled work has no task). The
mapping belongs in our records, not in the tracker's description field — the
tracker holds a pointer, we hold the truth. This keeps the tracker replaceable
and keeps ADR-0050 intact, because the run record never leaves the tenant.

**An agent authenticates as itself.** The rule is that no agent ever holds a
human's credential, which rules out the common "bot account owned by the team
lead" pattern. Concretely: one principal per agent, minted by the fabric at
deploy time alongside the agent's other identities, with a token scoped to that
agent's projects and a lifetime tied to the deployment — so de-provisioning an
agent revokes its ability to touch tasks, the same stale-permission discipline
ADR-0026 asks for elsewhere. This is exactly where the products differ, and it
is why criterion 6 dominated the recommendation. Where a product cannot mint
principals over an API, the fabric must record the manual provisioning step as a
deployment prerequisite that the phase gate can check, rather than pretending it
is automatic.

**Task state and session state stay in step through events, not polling.** The
tracker is the human's view; our run record is the system's. Two flows: inbound,
a webhook (assignment, comment, state change, field edit) lands on the fabric,
is authenticated by signature, is mapped to a tenant, and either starts a run or
delivers input to a live one; outbound, run lifecycle transitions write back a
state change and a comment. Both directions need idempotency keys, because
webhooks are at-least-once everywhere and our own retries are too. Every write
is attributed to the agent's principal so the tracker's own activity log stays
truthful — an agent's transition must be visibly an agent's.

**When they disagree, the tracker wins on intent and the run record wins on
fact.** The disagreements are enumerable. A human closes a task while a run is
in flight: the run is cancelled, not abandoned, and the cancellation is
commented back. A run fails but the tracker still says In Progress: our
reconciler, not the human, moves it and says why. A human reassigns a task from
one agent to another mid-run: the first run is cancelled and a new one starts;
we do not transfer a session. A human edits the agent-owned custom fields by
hand: we treat our values as authoritative and restore them with a comment
explaining the overwrite, because those fields are machine state wearing a
human-readable coat. Underlying all four: a periodic reconciler is required
regardless of webhook quality, because webhooks are missed, and its job is to
make the tracker agree with the run record and to log every correction. A
missing reconciler is how these integrations rot.

---

## 5. What we could not verify

- **Plane's own documentation.** `plane.so` and `developers.plane.so` are
  blocked by this environment's egress proxy. Everything attributed to Plane's
  docs here comes from search-result extracts of those pages or from the GitHub
  repositories, which are reachable. The API-reference details (exact endpoint
  list, error model) were not read first-hand.
- **OpenProject's website.** `www.openproject.org` is blocked. The OpenProject
  API, webhook and versioning quotations come from the in-tree docs in
  `opf/openproject`, which is a primary source; the Enterprise-only status of
  the MCP server, placeholder users and SSO comes from search extracts of the
  blocked site and from the 17.2/17.8 release notes, not from a page we read.
- **Whether Plane's community edition can create bot users at all without an
  interactive OAuth app install.** The docs describing agent installation were
  behind the blocked domain. PR #9399's rejection tells us the API path is
  declined; it does not tell us what the install flow requires in the AGPL
  build. This is the single most decision-relevant unknown here and should be
  settled by standing up Plane locally before anyone commits.
- **Rate limits for OpenProject, Taiga, Vikunja, Kaneo, Redmine and Huly.** The
  docs do not say. Only Plane, GitLab and Gitea document a limit. For a system
  that may drive hundreds of agent writes per minute per tenant this is not a
  detail, and it has to be measured per candidate.
- **Whether Taiga, Vikunja, Kaneo or Huly have any non-human principal concept.**
  We found none; absence of evidence in docs that are otherwise thorough is
  suggestive but not conclusive.
- **Gitea custom fields on issues.** We found no support and believe there is
  none, but no page states the negative outright.
- **Huly's HTTP API and webhook surface.** Documented as a typed client with
  GitHub-integration webhooks; whether a general webhook facility exists is
  unknown.
- **GitLab's official MCP server details.** GitLab ships MCP functionality, but
  we did not verify its self-managed availability or its tier, so it is left
  vague in the table deliberately rather than asserted.
- **Kaneo's custom fields, custom states and release cadence.** The repository
  shows ~3k commits; we did not obtain dated releases, and `kaneo.app/docs` was
  read only through search extracts.
- **Long-term licence stability.** Nothing here predicts relicensing. Plane
  already keeps an edition boundary and has moved a feature across it within the
  last three months; that is a live risk, not a historical one.

---

## Sources

- Plane: [makeplane/plane](https://github.com/makeplane/plane) ·
  [plane-mcp-server](https://github.com/makeplane/plane-mcp-server) ·
  [PR #9399, closed unmerged](https://github.com/makeplane/plane/pull/9399) ·
  [issue #9034, rate-limit override](https://github.com/makeplane/plane/issues/9034) ·
  developers.plane.so (blocked; via search extracts)
- OpenProject: [opf/openproject docs/api](https://github.com/opf/openproject/blob/dev/docs/api/README.md) ·
  [docs/system-admin-guide/api-and-webhooks](https://github.com/opf/openproject/blob/dev/docs/system-admin-guide/api-and-webhooks/README.md) ·
  openproject.org MCP/placeholder-user/SSO pages (blocked; via search extracts)
- Taiga: [taigaio](https://github.com/taigaio) ·
  [kaleidos-ventures/taiga](https://github.com/kaleidos-ventures/taiga) ·
  [taiga-docker](https://github.com/taigaio/taiga-docker)
- Vikunja: [go-vikunja/vikunja](https://github.com/go-vikunja/vikunja) ·
  [vikunja.io/docs/openid](https://vikunja.io/docs/openid/)
- GitLab: [custom fields](https://docs.gitlab.com/user/work_items/custom_fields/) ·
  [configurable work item types](https://docs.gitlab.com/user/work_items/configurable_work_item_types/) ·
  [service accounts](https://gitlab.com/gitlab-org/gitlab/-/blob/master/doc/user/profile/service_accounts.md)
- Gitea: [docs.gitea.com/api](https://docs.gitea.com/api/) ·
  [webhooks](https://docs.gitea.com/usage/repository/webhooks/) ·
  [OAuth2 provider](https://docs.gitea.com/development/oauth2-provider/)
- Redmine: [redmine/redmine tags](https://github.com/redmine/redmine/tags) ·
  [REST API wiki](https://www.redmine.org/projects/redmine/wiki/Rest_api)
- Kaneo: [kaneo-app/app](https://github.com/kaneo-app/app) ·
  [MCP docs](https://kaneo.app/docs/core/integrations/mcp)
- Huly: [hcengineering/platform](https://github.com/hcengineering/platform) ·
  [huly-selfhost](https://github.com/hcengineering/huly-selfhost) ·
  [dearlordylord/huly-mcp](https://github.com/dearlordylord/huly-mcp)
- Focalboard: [mattermost-community/focalboard](https://github.com/mattermost-community/focalboard) ·
  [Call for Maintainers #5038](https://github.com/mattermost-community/focalboard/issues/5038)
- Wekan: [releases](https://github.com/wekan/wekan/releases) ·
  [REST-API.md](https://github.com/wekan/wekan/blob/main/docs/API/REST-API.md)
- Leantime: [v3.9.8 release](https://github.com/Leantime/leantime/releases/latest)
