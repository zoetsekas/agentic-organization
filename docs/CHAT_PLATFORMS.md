# Chat platforms: giving agents a place to reach people

Researched September 2026. ADR-0021 makes human contact a declared channel
contract — purpose, working hours, SLA, escalation — and ADR-0026 pairs every
agent with an accountable owner, approvers and reviewers. The contract is
real; the wire behind it is not. `ChannelClass` is abstract, Slack and Teams
appear only in the binding, and **there is no bridge client behind either**:
routing plans are computed against an in-process stub (WS-013 M4,
ARCHITECTURE §9). For a Docker-based alpha we want a chat server we can run in
the stack, or a gateway that reaches the chat servers people already use.

This document evaluates the self-hosted chat servers and compares them against
the option already on the roadmap — binding to an **OpenClaw gateway**
(ROADMAP 3.4). It is not an ADR and makes no decision; it makes a
recommendation and then argues against it.

One constraint on the research itself, and it is a heavy one: this
environment's egress proxy blocks **every vendor documentation site** in scope
— `docs.mattermost.com`, `developers.mattermost.com`, `api.mattermost.com`,
`mattermost.com`, `docs.rocket.chat`, `developer.rocket.chat`, `rocket.chat`,
`zulip.com`, `zulip.readthedocs.io`, `chat.zulip.org`, `matrix.org`,
`spec.matrix.org`, `element.io`, `element-hq.github.io`, `nextcloud.com`,
`docs.nextcloud.com`, `openclaw.ai` and `docs.openclaw.ai`. GitHub is
reachable. Every one of these projects keeps its documentation in-tree, so the
quotations below are taken from the source repositories (`mattermost/docs`,
`mattermost/mattermost-developer-documentation`,
`mattermost/mattermost-api-reference`, `mattermost/mattermost`, `zulip/zulip`,
`zulip/docker-zulip`, `nextcloud/spreed`, `element-hq/synapse`,
`element-hq/dendrite`, `openclaw/openclaw`) — which is the primary source, not
a mirror of it. Rocket.Chat is the exception and is marked as such: its
documentation repository `RocketChat/docs` **was archived on 27 June 2024** and
the live docs are behind the blocked domain, so Rocket.Chat claims below rest
partly on search-result extracts. Section 6 lists what stayed unverified.

Every image tag recommended here was checked against the Docker registry
manifest API, not against documentation; digests are in §3.

---

## 1. The shortlist at a glance

| | **Mattermost** | **Rocket.Chat** | **Zulip** | **Matrix (Synapse)** | **Nextcloud Talk** | **OpenClaw gateway** |
|---|---|---|---|---|---|---|
| **Bot / non-human principal** | **Yes, first class, free.** Bot accounts: can't be logged into, "don't count as a registered user"; `BOT` tag in the UI; assignable to channels; @-mentionable and DM-able | Bot role + app-associated bot users; app bot username must equal the app name | Bot users (generic / incoming-webhook / outgoing-webhook), free, API-only accounts, "cannot create other bots" | Any user is a bot; **application services** own a whole namespace of virtual users — the strongest identity model here | **Bots exist but can only be installed from the command line** (`occ talk:bot:install`); a bot is a URL + shared secret, not an account | N/A — the agent is the gateway's identity in *your* Slack/Teams/Telegram |
| **Minting a principal over an API** | `POST /api/v4/bots` (`create_bot` permission, since 5.10) + token endpoint | REST user create + PAT; **private apps are premium from v7.0** | `POST /json/bots` as an admin | Register user, or one appservice registration file per namespace (file, not API) | **No — CLI only, by design ("for security reasons")** | Config file / CLI per channel account |
| **API completeness** | REST v4, ~200 endpoints, OpenAPI in `mattermost-api-reference`; post, react, thread (`root_id`), create channel, membership | REST + DDP realtime API; broad; rate limiter on by default, bypassable per role | REST, versioned, excellent reference; send/edit/react/topics/streams/subscriptions | Client-Server API, versioned spec (`v1.x`), rooms/state/reactions/threads | OCS API v1/v4 under `/ocs/v2.php/apps/spreed`; chat, reactions, rooms, participants | Gateway HTTP/WS API + CLI; channel-agnostic send/receive |
| **Auth model** | Personal access tokens per account (incl. bots); OAuth2 provider | PAT, resume tokens, OAuth | API key per (bot) user, HTTP basic | Access tokens; appservice `as_token`/`hs_token` | Bot: HMAC-SHA256 shared secret per bot; users: app passwords/OAuth | Gateway token, Tailscale identity, or trusted proxy |
| **Rate limits** | Off by default; when enabled `PerSec` 10, `MaxBurst` 100, vary by remote address (`model/config.go`) | On by default, configurable; `api-bypass-rate-limit` permission | Documented per-user limits | Per-homeserver config; appservices exempt | the docs do not say | N/A |
| **MCP** | **Official** — `mattermost/mattermost-plugin-agents` ships an embedded MCP server (`mcpserver/`), Apache-2.0, PAT auth, tools incl. `create_post`, `read_channel`, `create_channel` | Community only (`twostone/rocket-chat-mcp`, `huiseo/rocketchat-threads-mcp`) | Community only, and crowded: `akougkas/zulipchat-mcp`, `avisekrath/zulip-mcp-server`, `Monadical-SAS/zulip-mcp`, `antra-tess/zulip_mcp` — no vendor server | Community only | Community only | It *is* the tool surface; MCP servers are plugins to it |
| **Interactive elements** | **Strongest.** Mattermost Blocks (`props.mm_blocks`), markdown action buttons (`mmaction://`), interactive dialogs via `/api/v4/actions/dialogs/open` with `trigger_id`, stackable to 3 since v11.10; slash commands | UIKit blocks, modals, contextual bars, action buttons, slash commands — **but building them means a private app, premium from v7.0** | **Weak.** `zform` buttons exist, but a click "simulates a message reply"; widgets are core-only — "Zulip doesn't have a plugin model for widgets" | **None in the spec.** Reactions and polls; no buttons, no forms | **None.** Messages and reactions only | Inherits whatever the underlying platform offers (Slack blocks, Teams cards) |
| **Events** | WebSocket event stream + outgoing webhooks + plugin hooks | DDP websocket + outgoing webhooks | Long-poll `register`/`events` queue; outgoing-webhook bots push on mention/DM | `/sync` long-poll; appservice **push** transactions | Signed webhook POST per message (Activity Streams 2.0, HMAC header) | Gateway events/webhooks |
| **Self-host / licence** | Docker + Compose; server MIT (Team Edition); Enterprise Edition binary free in "Entry" mode; plugins increasingly under the **Mattermost Source Available License** | Docker + Compose; MIT core, premium tiers | Docker Compose or installer; Apache-2.0 | Docker; **AGPL-3.0** or commercial; ESS Community "non-commercial community use cases" | Docker (whole Nextcloud); AGPL-3.0 | npm/Docker; **MIT, no paid tier** |
| **SSO/OIDC free?** | **No.** SAML, Entra, Okta, "SSO with AD/LDAP, OpenID, and others" are **not** in Team Edition; they are in Entry and paid tiers | Enterprise/Pro for SAML/LDAP | Free (OIDC/SAML in the open-source server) | Free (OIDC in Synapse config) | Free (Nextcloud SSO apps) | N/A |
| **Multi-tenancy** | Teams inside one instance — an authorization tree, not an isolation boundary; instance per tenant | Workspaces = instances | **Realms**: several organizations per server, one subdomain each — genuine separation inside one instance | One homeserver per tenant (or federation) | Instance per tenant | "one cell per tenant" — the vendor's own position |
| **Footprint** | App + PostgreSQL. 2 containers | App + **MongoDB replica set**. 2–4 containers | App + PostgreSQL + Redis + memcached + RabbitMQ; "**at least 2GB of available RAM**"; needs root | Synapse + PostgreSQL (+ a client if you want one); Element web is separate | The whole of Nextcloud: PHP-FPM + web + DB + Redis + cron | One Node daemon |
| **Maturity** | v11.11.0 image published 7 Sep 2026, 10.11 ESR patched 16 Sep 2026, 12.0.0-rc1 18 Sep 2026 — monthly cadence | 8.8.1 latest release; `develop` images built 20 Sep 2026 | 12.2 latest release (`LATEST_RELEASE_VERSION = "12.2"`), 12.0-dev on main | Actively built (images 18 Sep 2026) | Ships with Nextcloud, active | Very active, MIT, ~390k★ (LANDSCAPE §8) |

Not on the shortlist, with evidence:

- **Dendrite — dormant by its maintainers' own statement.** `element-hq/dendrite`'s
  README says verbatim: *"It is currently in maintenance mode, meaning only
  security fixes are being applied"*, and separately *"Dendrite is beta
  software"* with *"no high-availability/clustering support"*. It is the
  lighter Matrix homeserver and would have been the interesting one for
  footprint; it is not a foundation. Rejected.
- **Element as a separate candidate** — Element is a *client* for Matrix; the
  server question is Synapse (or Dendrite). Evaluated as Matrix below.
- **XMPP (Prosody, ejabberd)** — tiny footprint, trivial bot identity, decades
  of maturity, and no interactive elements, no MCP, and no chat product humans
  will accept for approvals in 2026. A fallback, not a candidate.
- **Conduit / continuwuity** — lighter Matrix homeservers; they inherit
  Matrix's missing interactive elements, so they do not change the Matrix
  verdict and add implementation risk. Not separately evaluated.
- **Consumer messengers** — declined by ADR-0021 ("enterprise surfaces only")
  regardless of merit.

---

## 2. The serious candidates

### Mattermost — the only one with buttons, bots and an MCP server on the free tier

**What it is.** An open-core, self-hosted team chat server. Team Edition is
MIT; the Enterprise Edition binary can be run unlicensed in a free mode the
docs call **Entry**. Docker Compose self-hosting, PostgreSQL, two containers.

**Criterion 1 — the non-human principal.** This is where it wins. The
developer documentation on bot accounts says bot accounts *"are just like user
accounts, except they: Can't be logged into. Can't be used to create other bot
accounts... Don't count as a registered user and therefore don't count towards
the total number of users for an Enterprise Edition license."* And: *"A `BOT`
tag is used throughout Mattermost where bot accounts are referenced"*,
*"Integrations created by a user and tied to a bot account no longer break if
the user leaves the company"*, and *"System Admins can enable bot accounts to
post to any channel in the system, including private teams, Private channels,
or Direct Messages."* They are @-mentionable and DM-able because they are
users. They can be minted over the API: `POST /api/v4/bots`, *"Must have
`create_bot` permission. Minimum server version: 5.10"*, and a token is issued
through the personal access token endpoint. One caveat from the same page:
*"Only System Admins or plugins can create or manage bot accounts"* — so the
fabric holds a system-admin credential to mint agents, which is exactly the
ADR-0021 "bridge holds the credential" posture, not a violation of it. A second
caveat, recorded verbatim because it will matter to somebody: *"In Mattermost
Enterprise Edition, service accounts without an email address pulled from LDAP
or SAML systems are not yet supported."*

**Criterion 4 — interactive elements.** Also where it wins, and this is the
criterion that decides approval routing. Three mechanisms, all free:
*Mattermost Blocks* (`props.mm_blocks` for layout, text, buttons and menus,
with handlers in `props.mm_blocks_actions`) which the docs call *"the
recommended way to build interactive integration posts"*; *markdown action
buttons* using `mmaction://` links — the docs' own example is *"a short message
[that] reads naturally with an inline 'Approve' or 'Reject' link"*, which is
literally our use case; and *interactive dialogs*, opened by POSTing a
`trigger_id` to `/api/v4/actions/dialogs/open`, for when approving needs a
reason or a field. Since v11.10 dialogs stack up to three deep. Legacy message
attachment `actions` still work and are translated into Blocks at render time.

**Criterion 5 — events.** A WebSocket event stream, outgoing webhooks
(configured per channel with a trigger word and a content type), and, for a
click, a direct POST to the `integration.url` in the button definition. A human
clicking *Approve* reaches us as an authenticated HTTP request carrying the
button's `context`, not as a message we must parse.

**Criterion 3 — MCP.** Official and vendor-owned:
`mattermost/mattermost-plugin-agents` contains `mcpserver/`, *"A Model Context
Protocol (MCP) server that provides AI agents and automation tools with secure
access to Mattermost channels, users, and content"*, authenticating with a
personal access token and exposing `read_post`, `read_channel`, `search_posts`,
`create_post`, `create_channel` among others. Read the warning in that README
before planning on it: *"The `mattermost-mcp-server` process ... is intended for
development and local tooling only and is not supported for production
deployments. For production, use the Agents plugin's embedded MCP."* The
plugin is Apache-2.0 at the root with an `enterprise/` directory under the
Source Available licence.

**What it would cost us.** Two things, and the second is not small.

*The licence boundary is moving, in public.* The `mattermost/docs` FAQ on the
Mattermost Source Available License states the rule plainly — *"allows
free-of-charge and unrestricted use of the source code in development and
testing environments, but requires a valid Mattermost Enterprise Edition
License in a production environment"* — and then the direction of travel:
*"New, Mattermost-authored plugins will generally be released under the
Mattermost Source Available License"*, though *"No licensing changes are
planned to non-plugin repositories, such as `mattermost`"*. The server is safe;
the plugin ecosystem is not, and the MCP server lives in a plugin.

*SSO is paid, and this is the thing that has bitten us before.* The plans table
in `mattermost/docs` puts *"Single sign-on w/SAML 2.0, Entra ID, Okta, and
others"* and *"SSO with AD/LDAP, OpenID, and others"* **outside Team Edition** —
they appear from Entry upward. Guest accounts are likewise outside Team
Edition. So the MIT build gives us bots, buttons, webhooks and the API, and
does not give us a way for a tenant's humans to sign in with the tenant's IdP.
Entry gives SSO back for free, but Entry is the commercial binary under a
licence that *"prohibits reverse engineering or tampering with the license key
mechanism"*, and it carries server-wide limits: *"10,000 Channel messages
history across all channels (older messages remain in the database but aren't
viewable or searchable)"*, no compliance features, no high availability,
*"best suited for teams less than 50 users"*. For an approvals surface, 10,000
messages across all channels is a ceiling we would hit and then silently lose
history behind.

*Multi-tenancy* is teams inside one instance, which is an authorization tree,
not an isolation boundary — under ADR-0050 this is one instance per tenant. The
footprint makes that affordable: one Go binary plus PostgreSQL.

### Rocket.Chat — the right features, the wrong side of the paywall

UIKit is genuinely good: the marketplace documentation lists app capabilities
as *"Display interactive and dynamic content on their own interface - like a
modal or the room's contextual bar"*, *"Create buttons in the UI for users to
interact with native content"*, *"Create slash commands"*, *"Register endpoints
to receive data from other applications and services"*. Bot users exist, the
REST API is broad, there is a DDP realtime API and outgoing webhooks, and MIT
covers the core.

The problem is how you get those buttons. They come from an App, and our App
would be a *private* app — and private apps are where the community edition
stops. The reporting is consistent: community workspaces may enable up to five
marketplace apps and at most three private apps, and *"Private app uploads will
be reserved exclusively for premium plans (including the forever free Starter
plan) beginning with v7.0"*. Starter is free but capped (50 users,
self-managed) and is a *plan registered against Rocket.Chat Cloud*, which is a
dependency on the vendor's cloud for a tenant-facing component. Add that the
marketplace requires the workspace to be registered on Rocket.Chat Cloud even
on the community plan, that support windows are six months per release, and
that MongoDB (as a replica set) is the heaviest data dependency on this list,
and Rocket.Chat is a worse Mattermost for our purpose.

**This is also where the evidence is weakest.** `RocketChat/docs` is archived
(27 June 2024) and the live docs are blocked here. The private-app restriction
is stated consistently across the sources we could read, but we did not read it
on a current vendor page. Treat it as likely, not established.

### Zulip — the best-run project here, with the wrong interaction model

Apache-2.0, no paid self-hosted tier, no feature paywall, OIDC and SAML free,
a REST API whose reference is the best of the group, bot users that are free
and API-only, and — uniquely — **realms**: *"Zulip's approach for supporting
multiple organizations on a single Zulip server is for each organization to be
hosted on its own subdomain."* That is the only candidate that offers a
credible within-instance tenant separation, which under ADR-0050 would still
need arguing but is at least arguable.

It fails criterion 4, and criterion 4 is the one that matters. Zulip's
button story is `zform`, and the `docs/subsystems/widgets.md` description of
how a click works is the disqualifier: the click handler calls
`transmit.reply_message(opts.message, reply_content)` — i.e. **clicking a
button sends a chat message**. Approval would round-trip as a text reply that
we parse, which is the "type a magic word" failure we are trying to design
out, wearing a button. And it cannot be extended: *"Currently, Zulip doesn't
have a plugin model for widgets—they are served by the core server
implementation"*, so a custom approval widget means forking the server. There
is an open feature request (`zulip/zulip` #32373) asking for exactly the
capability we need, which is corroboration that it is missing.

Events are a long-poll `register`/`events` queue rather than webhooks —
workable, and outgoing-webhook bots do push to us on mention or DM, but it is
another daemon holding a queue. MCP is community-only and crowded, which is
worse than none: four or five unaffiliated implementations and no vendor
answer about which survives. Footprint is the largest here:
`zulip/docker-zulip` recommends *"at least 2GB of available RAM"* and the stack
is Zulip plus PostgreSQL, Redis, memcached and RabbitMQ; it also states *"This
project doesn't support `docker-rootless`"* because *"Zulip needs root
access"*. Five moving parts and root, per tenant, on a single machine, is the
kind of thing we rejected Kafka and Pulsar for.

### Matrix with Synapse — the best identity model, no interaction model

Matrix's **application service** concept is, on paper, the best answer to
ADR-0026 on this list: an appservice owns a namespace of users, so every agent
in a tenant can be a real Matrix user under one registration, the homeserver
pushes events to us in transactions rather than us polling, and no agent ever
holds a human's credential. Federation means a tenant could even keep its own
homeserver. The spec is versioned and stable, the client ecosystem (Element)
is good, and it is the only option here with a genuine standard behind it.

Then criterion 4 ends it. The Matrix specification has no interactive
buttons, no dialogs and no forms; it has messages, reactions, and polls. An
approval would be an emoji reaction or a poll vote or a typed reply — all
three are unauthenticated-looking gestures we would have to interpret, and
none of them carries a callback with a context payload. Everything button-like
in the Matrix world lives in a *widget* rendered by a particular client, which
means the approval UI works in Element and nowhere else.

Licence is the second cost. Synapse is **AGPL-3.0** ("or alternatively under a
commercial license from Element"), which is the same publication duty
TASK_SERVICES flagged for Plane, and the supported deployment path is the
Element Server Suite, whose free edition is described as *"tailored to
small-/mid-scale, **non-commercial** community use cases"*. Running AGPL
Synapse standalone for a paying tenant is permissible; running the vendor's
supported distribution is not. Footprint is moderate (Synapse + PostgreSQL),
and registration of appservices is a config file on disk, not an API — awkward
for a compiler that mints agents per deploy.

### Nextcloud Talk — eliminated by criterion 1, in one sentence from its own docs

`nextcloud/spreed`'s `docs/bots.md` says: *"For security reasons bots can only
be added via the command line. `./occ talk:bot:install --help` gives you a
short overview of the required arguments."* Our platform provisions agents by
compilation; a chat surface where creating an agent's identity is an `occ`
invocation inside the container is a manual deployment prerequisite forever.
The bot model is also not an account: a bot is a URL plus a shared secret,
identified by *"Hash of the URL prefixed with `bot-` serves as `actor_id`"*, so
an agent cannot be DM'd as itself in the ordinary sense, only messaged in
conversations where an administrator has enabled the bot.

What it does well is worth recording, because it is the best webhook story
here: messages are delivered as signed POSTs following the *"Activity Streams
2.0 Vocabulary"*, with an HMAC-SHA256 over a random header plus body, and the
bot features are enumerable (`webhook`, `response`, `event`, `reaction`). But
bot capabilities stop at messages and reactions — no buttons, no forms — and
the footprint is all of Nextcloud. Rejected.

---

## 3. The other class of answer: an OpenClaw gateway

LANDSCAPE §8 and ROADMAP 3.4 already carry this: rather than running a chat
server, bind `ChannelClass.TEAM_CHAT` to an **OpenClaw gateway** and let humans
stay in Slack, Teams, Telegram or whatever they already have. `openclaw/openclaw`
is MIT, Node, stewarded by a 501(c)(3), with *"no paid tier, hosted service, or
token"*, and its README claims *"Discord, iMessage, Slack, Teams, Telegram,
WhatsApp, and 20+ more"*. Its team guide is explicit that team use is
*"configuration, not a separate edition"*.

It is a different *class* of answer, and the comparison is not feature by
feature:

- **It does not solve criterion 1 and 4 — it relocates them.** An agent's
  identity and an approval button become Slack's or Teams' problem, which is
  where the real answer has always been: Slack Block Kit and Teams Adaptive
  Cards are both better approval surfaces than anything on the self-hosted
  list. What OpenClaw gives is that we do not write and maintain those two
  clients.
- **It is honest about tenancy, and the news is the same as ours.** Its
  own multi-tenant page: *"OpenClaw's default security model is one trusted
  operator boundary per Gateway, not hostile multi-tenant isolation inside one
  shared Gateway"*, so *"Hosting users or organizations that do not share a
  trust boundary therefore means running a separate complete OpenClaw instance
  for each tenant."* That matches ADR-0050 exactly — and the per-tenant
  footprint is one Node process, which is the smallest per-tenant footprint
  in this document by a wide margin.
- **The mechanism for that is flagged experimental.** Same page: *"Fleet is
  **experimental**: its commands, flags, and container profile can change
  between releases without a deprecation window"*, and *"Fleet is tested on
  Linux and macOS hosts. Windows hosts are currently untested."*
- **It assumes the tenant already has the workspace.** For a tenant with
  Slack, that is a feature. For an alpha demo on one machine with no workspace
  credentials at all, it is a blocker — there is nothing to point it at.

**Which class fits better.** They fit different problems, and we have both
problems. A self-hosted chat server answers *"the alpha stack must demonstrate
an approval end to end on one machine with no third-party accounts"*. A gateway
answers *"a real customer's humans must approve in the tool they already
live in"*. The gateway is the better long-run answer and does not become
unnecessary if we ship a chat server; the chat server is the better alpha
answer and does not become unnecessary if we ship the gateway, because it is
the only way to run the whole path in CI and in a demo. The mistake would be
to treat them as competing, and the way to avoid making it is to make both
bind to the same internal port so the choice is a binding, which is what
ADR-0021 already says a channel is.

---

## 4. Recommendation

**Run Mattermost, Team Edition, one instance per tenant, behind our own
channel-bridge port — and keep the OpenClaw gateway binding as the second
adapter behind that same port, because it is the one real customers will
want.**

The reasoning is narrow. Criteria 2, 5, 8 and 9 are met by several candidates.
Criterion 1 and criterion 4 together are met by exactly one: Mattermost is the
only option where an agent can hold a free, API-minted, @-mentionable,
DM-able, visibly-badged identity **and** a human can be shown a button labelled
Approve whose click arrives at us as an authenticated callback with a context
payload. Rocket.Chat has the buttons and paywalls the means of building them;
Zulip's button sends a chat message and cannot be extended without forking;
Matrix has no buttons at all; Nextcloud Talk cannot mint the identity without
a shell. Criterion 3 then breaks the tie in the same direction — Mattermost is
the only one with a vendor-owned MCP server. Criterion 8 agrees: one Go binary
and PostgreSQL is the smallest chat-server footprint on the list, and it
matters because ADR-0050 makes this a per-tenant component.

Verified image, checked against the registry manifest API on 20 September 2026,
not from documentation:

- `mattermost/mattermost-team-edition:11.11.0` →
  `sha256:335db2833330323d9a9f43313429f4b16b497067644660acfdfc2424ab04d2de`
  (the same digest `:latest` currently resolves to; published 7 September 2026)
- ESR alternative, `mattermost/mattermost-team-edition:10.11.24` →
  `sha256:956002f81635864b873239ee83042a803167f0fb452348fbea72b2142d7c89fc`
  (published 16 September 2026)
- If SSO is needed, the Enterprise binary in Entry mode:
  `mattermost/mattermost-enterprise-edition:11.11.0` →
  `sha256:f0643c47b55bd7be78643ee79b681e0ec5e11da32f64c7dfba367399d157454e`
- `postgres:17-alpine` →
  `sha256:f02121de6f74d30d8a94cd1d9584125e2178d7e6c377d8130112d4e52d867995`

`12.0.0-rc1` exists in the registry (18 September 2026) and must not be used;
`11.11.1` does not exist (the registry returns 404 for it).

**The strongest argument against this recommendation.** It is that we would be
choosing the MIT edition of an open-core product for the one job — human
identity and access — that the vendor sells. The `mattermost/docs` plans table
puts **all** SSO outside Team Edition: SAML, Entra, Okta, *"SSO with AD/LDAP,
OpenID, and others"*, and guest accounts with them. Our tenants are
organizations with identity providers; ADR-0047 already commits the designer to
verified OIDC. So the chat surface where a tenant's approvers must be
identified would be the one component in the stack that cannot use the
tenant's IdP, and the escape hatch — Entry — is a commercial binary whose free
mode caps message history at *"10,000 Channel messages history across all
channels"* and whose licence forbids touching the key mechanism. Choosing
Mattermost therefore means either accepting local accounts on the approvals
surface (a compliance conversation we will lose) or accepting a commercial
licence in the alpha stack (a decision above this document's pay grade). The
licence drift makes it worse rather than better: *"New, Mattermost-authored
plugins will generally be released under the Mattermost Source Available
License"*, and the official MCP server is a plugin.

A reasonable person reading the same evidence would take **Zulip** — Apache-2.0
throughout, free SSO, real multi-realm separation, the best-documented API, no
vendor whose commercial boundary can move — and would accept that approval
arrives as a parsed reply, defending it on the grounds that ADR-0021's contract
already treats an approval as a routed *request and answer* rather than a UI
event, and that a reply we parse is auditable in a way a callback is not. That
is not a silly position. The counter is that "did the human really mean yes"
becomes a text-matching problem in the one place where ambiguity is most
expensive, and that we would then own a widget fork or a parser forever.

A defensible middle path, and the one to take if the alpha timeline is tight:
build the bridge port with Mattermost as adapter one and a *recorded transcript*
adapter as adapter zero (the existing `messaging.channel_transport` stub,
promoted to a first-class adapter), and require the OpenClaw binding as adapter
two before anything is called done — because it is the adapter that proves the
port is not Mattermost-shaped.

---

## 5. What integration requires from us, whichever we choose

No chat product knows what an agent run or an approval gate is. Five pieces
are ours, and they are the same five in every option above.

**An approval round-trips through a request record, not through a message.**
The message is a view of the request; the request is the truth. Concretely: the
runtime creates an approval request with an ID, an expiry derived from
`response_sla_minutes`, and the escalation chain from `humans.py`'s
`RoutingPlan`. The bridge renders it on the bound channel — a Mattermost Block
with Approve and Reject buttons whose `context` carries the request ID and a
short-lived HMAC, a Slack Block Kit action through OpenClaw, or in the
degenerate case a text prompt. The click (or reply) arrives at the bridge, which
verifies the signature, resolves the request ID, checks that the clicking user
is in the approver set for *that* request, and only then resolves the gate. The
chat platform is never the authority on who approved; it is the place the
question was asked. This also means the same approval can be re-rendered on the
next escalation step without creating a second decision.

**An agent authenticates as itself, and the bridge holds the workspace
credential.** ADR-0021 already fixes this and it survives contact with every
product here: one component per channel owns the workspace token, agents talk
to the bridge, and `ChannelBinding.bot_identity_ref` stays a name, never a
token. What changes per product is *minting*: Mattermost mints over
`POST /api/v4/bots` using a system-admin credential the fabric holds; Matrix
mints by writing an appservice registration; Nextcloud Talk cannot mint without
a shell, which is why it is out. Where minting is not an API, the fabric must
record the manual step as a deployment prerequisite the phase gate checks —
the same discipline TASK_SERVICES §4 arrived at — rather than pretending it is
automatic. De-provisioning an agent must revoke its token and deactivate its
principal, or a decommissioned agent keeps a seat at the table.

**A channel maps to a `ChannelClass`/`ChannelPurpose` pair, and the mapping is
declared, not inferred.** `ChannelClass.TEAM_CHAT` binds to one chat workspace;
each `ChannelSpec` binds to exactly one room/channel in it, carrying its
`purposes`. Two rules follow from ADR-0021 that the bridge must enforce
rather than trust: routing a request to a channel that does not serve its
purpose is an error, not a best guess, so the bridge rejects a post whose
purpose is not in the target's `purposes`; and `forbid_data_classes` is checked
at the bridge, on the rendered payload, because that is the last place before
egress. Practically, expect one channel per purpose per team rather than one
channel per agent — `approve` wants a small, staffed room with a real SLA;
`notify` and `report` want a firehose nobody is on call for; `handoff` and
`ask` want threads, which is why threading (`root_id` in Mattermost, threads in
Matrix, topics in Zulip) is a criterion and not a nicety. DMs are the natural
binding for a single accountable owner under ADR-0026, and are also the
surface where OpenClaw's pairing-approval control (ROADMAP 3.4b) is missing
from our model.

**A human replying to a stale request gets a definite answer, not silence.**
This is the case the products handle worst and we must handle explicitly. When
a click or reply arrives for a request that has already expired, escalated past
that person, been decided by someone else, or belongs to a run that has since
failed, the bridge resolves it as *late* and says so in the thread, naming what
actually happened and when — "this was approved by X at 09:12 and the run
completed", "this expired at 03:40 and escalated to Y", "the run failed before
this was answered". It must never silently apply a late approval to a gate
whose deadline has passed, because that converts an SLA into a suggestion, and
it must never drop the click, because a button that does nothing teaches people
the channel is theatre — which ADR-0021's disadvantages section already names
as the failure mode to avoid. The rendered message should be edited in place to
show its terminal state, so the channel history is not a field of unanswerable
questions.

**A reconciler, because events are missed.** Websocket streams drop, webhooks
are at-least-once, and long-poll queues expire. Every write needs an
idempotency key, and a periodic sweep must compare open approval requests
against their rendered messages and against the clock: expire what is expired,
escalate what is due, re-render what was never delivered, and log every
correction. Nothing on this list will do that for us.

---

## 6. What we could not verify

- **Every vendor documentation site in scope was blocked** by this
  environment's egress proxy (listed in the preamble). Everything above comes
  from the projects' own repositories, from release artefacts, or from
  search-result extracts, and the extracts are marked where used. No vendor
  pricing page, feature-comparison page or API reference was read first-hand.
- **Rocket.Chat's private-app restriction and current plan limits.**
  `RocketChat/docs` is archived (27 June 2024) and `docs.rocket.chat` is
  blocked. The claim that private apps are premium from v7.0, and the 5-app /
  3-private-app community limits, come from search extracts of the blocked
  site. This is the single claim here that most deserves re-checking, because
  it is the one that eliminates Rocket.Chat.
- **Whether Mattermost Team Edition can run the Agents plugin (and therefore
  the official MCP server) at all**, and whether the embedded MCP server is
  gated by the plugin's `enterprise/` directory. The plugin root is Apache-2.0
  and `mcpserver/` appears outside `enterprise/`, but we did not read a
  statement of which tiers may load it, and `docs.mattermost.com/agents/` is
  blocked. We would not depend on it for the bridge — the REST API is enough —
  but the MCP claim in the table is weaker than the licence file alone
  suggests.
- **Mattermost Entry's practical limits.** The docs list them with an asterisk:
  *"Limits will take effect in a future release."* So the 10,000-message cap
  may or may not bite today, and which release turns it on is unknown.
- **Rate limits for Nextcloud Talk.** The docs do not say. Zulip's and
  Rocket.Chat's exist but were not read first-hand; only Mattermost's defaults
  were read in source (`server/public/model/config.go`: disabled by default,
  `PerSec` 10, `MaxBurst` 100).
- **Resident memory figures.** Nothing here is measured. Zulip's *"at least
  2GB"* is the project's own recommendation; every other footprint claim is
  inferred from the number of containers and the runtime, not from running the
  stack. Footprint is criterion 8 and it is the criterion with the least
  evidence behind it. Measure before committing.
- **Whether Mattermost's realm of "teams" could ever satisfy ADR-0050.** We
  assumed not, on the grounds that teams share one identity domain and one
  database. We did not find a vendor statement either way.
- **Zulip's `zform` status.** The description above is from
  `docs/subsystems/widgets.md` on `main`, which is a developer document, not an
  API contract; whether a bot's `widget_content` is a supported public API or
  an internal mechanism is not stated in a way we could quote.
- **OpenClaw's Slack and Teams channel completeness.** `docs.openclaw.ai` is
  blocked; the channel list and the multi-tenant and team guidance come from
  the repository's in-tree docs. Whether its Slack channel supports Block Kit
  interactive callbacks — the whole reason to prefer it for approvals — is
  **not verified**, and it is the decisive unknown for the gateway option.
- **Matrix polls.** We could not locate the poll module in `matrix-spec` at the
  path we tried and `spec.matrix.org` is blocked, so "polls exist in the spec"
  is asserted from general knowledge, not quoted. The load-bearing claim —
  that the spec has no buttons, dialogs or forms — is a negative we could not
  read a statement of either.
- **Long-term licence stability.** Mattermost has moved plugins to a source
  available licence and says new ones will generally follow; Synapse has
  already moved to AGPL. Neither is a prediction about the future, and both are
  evidence that these boundaries move.

---

## Sources

- Mattermost: [mattermost/mattermost](https://github.com/mattermost/mattermost) ·
  [docs: editions and offerings](https://github.com/mattermost/docs/blob/master/source/product-overview/editions-and-offerings.rst) ·
  [docs: plans table](https://github.com/mattermost/docs/blob/master/source/product-overview/plans.md) ·
  [docs: source available licence FAQ](https://github.com/mattermost/docs/blob/master/source/product-overview/faq-mattermost-source-available-license.rst) ·
  [dev docs: bot accounts](https://github.com/mattermost/mattermost-developer-documentation/blob/master/site/content/integrate/reference/bot-accounts/_index.md) ·
  [dev docs: personal access tokens](https://github.com/mattermost/mattermost-developer-documentation/blob/master/site/content/integrate/reference/personal-access-token/_index.md) ·
  [dev docs: interactive messages](https://github.com/mattermost/mattermost-developer-documentation/blob/master/site/content/integrate/plugins/interactive-messages/_index.md) ·
  [dev docs: interactive dialogs](https://github.com/mattermost/mattermost-developer-documentation/blob/master/site/content/integrate/plugins/interactive-dialogs/_index.md) ·
  [dev docs: outgoing webhooks](https://github.com/mattermost/mattermost-developer-documentation/blob/master/site/content/integrate/webhooks/outgoing/_index.md) ·
  [API: bots.yaml](https://github.com/mattermost/mattermost-api-reference/blob/master/v4/source/bots.yaml) ·
  [rate limit defaults, model/config.go](https://github.com/mattermost/mattermost/blob/master/server/public/model/config.go) ·
  [mattermost-plugin-agents MCP server](https://github.com/mattermost/mattermost-plugin-agents/blob/master/mcpserver/README.md) ·
  docs.mattermost.com, api.mattermost.com (blocked)
- Rocket.Chat: [RocketChat/Rocket.Chat](https://github.com/RocketChat/Rocket.Chat) ·
  [docs (archived 2024): marketplace](https://github.com/RocketChat/docs/blob/main/extend-rocket.chat-capabilities/rocket.chat-marketplace/README.md) ·
  [docs (archived 2024): our plans](https://github.com/RocketChat/docs/blob/main/readme/our-plans.md) ·
  docs.rocket.chat, developer.rocket.chat (blocked; via search extracts)
- Zulip: [zulip/zulip](https://github.com/zulip/zulip) ·
  [widgets subsystem](https://github.com/zulip/zulip/blob/main/docs/subsystems/widgets.md) ·
  [hosting multiple organizations](https://github.com/zulip/zulip/blob/main/docs/production/multiple-organizations.md) ·
  [real-time events API](https://github.com/zulip/zulip/blob/main/api_docs/real-time-events.md) ·
  [docker-zulip](https://github.com/zulip/docker-zulip) ·
  [issue #32373, enhanced bot capabilities](https://github.com/zulip/zulip/issues/32373) ·
  zulip.com, zulip.readthedocs.io (blocked)
- Matrix: [element-hq/synapse](https://github.com/element-hq/synapse) ·
  [element-hq/dendrite (maintenance mode)](https://github.com/element-hq/dendrite) ·
  [matrix-org/matrix-spec](https://github.com/matrix-org/matrix-spec) ·
  matrix.org, spec.matrix.org, element.io (blocked)
- Nextcloud Talk: [nextcloud/spreed bots and webhooks](https://github.com/nextcloud/spreed/blob/main/docs/bots.md) ·
  [bot management](https://github.com/nextcloud/spreed/blob/main/docs/bot-management.md) ·
  [chat API](https://github.com/nextcloud/spreed/blob/main/docs/chat.md) ·
  nextcloud.com (blocked)
- OpenClaw: [openclaw/openclaw](https://github.com/openclaw/openclaw) ·
  [team setup](https://github.com/openclaw/openclaw/blob/main/docs/start/teams.md) ·
  [multi-tenant hosting](https://github.com/openclaw/openclaw/blob/main/docs/gateway/multi-tenant-hosting.md) ·
  [channels](https://github.com/openclaw/openclaw/blob/main/docs/channels/index.md) ·
  docs.openclaw.ai (blocked)
- Community MCP servers named: [twostone/rocket-chat-mcp](https://github.com/twostone/rocket-chat-mcp) ·
  [huiseo/rocketchat-threads-mcp](https://github.com/huiseo/rocketchat-threads-mcp) ·
  [akougkas/zulipchat-mcp](https://github.com/akougkas/zulipchat-mcp) ·
  [avisekrath/zulip-mcp-server](https://github.com/avisekrath/zulip-mcp-server) ·
  [Monadical-SAS/zulip-mcp](https://github.com/Monadical-SAS/zulip-mcp)
- Image digests: Docker registry manifest API (`registry-1.docker.io`),
  checked 20 September 2026.
