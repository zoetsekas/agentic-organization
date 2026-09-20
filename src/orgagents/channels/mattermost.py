"""Our model of Mattermost's channel surface, and nothing more (ADR-0061).

Mattermost Team Edition was chosen because it can mint bot accounts through
the API (`POST /api/v4/bots`) and because a human can *click* a decision —
interactive message actions and dialogs (`POST /api/v4/actions/dialogs/open`)
carry a `trigger_id` and call back to us. Those two properties are what rules 2
and 3 need; see `docs/CHAT_PLATFORMS.md`.

**Nothing here has been run against a server.** There is no daemon in this
environment and every vendor documentation site is blocked by its egress
proxy, so the paths below come from in-tree notes and search extracts, and the
request and response *shapes* are modelled in our own types behind the
`TODO(mattermost-wire)` boundary in `MattermostWire`. That is the same stance
`sandboxes/openshell.py` and `runtime/a2a.py` take: a wire format that reads as
authoritative and is actually a guess is worse than a stated gap.

The transport is injected — a callable, no SDK, no new dependency — so the
adapter is exercisable end to end against a fake server and cannot reach a
real one by accident.
"""
from __future__ import annotations

import hmac
from typing import Any, Callable, Mapping, Optional

from ..ids import now
from .model import (
    ApprovalCallback,
    ApprovalDecision,
    ApprovalRequest,
    BotPrincipal,
    InboundMessage,
    PostedMessage,
)
from .port import (
    BridgeCapabilities,
    CallbackNotAuthenticated,
    PostFailed,
    PrincipalKind,
)

#: (method, path, json body) -> decoded JSON response.
#: A caller supplies one that carries the bot token; this module never builds
#: an HTTP client, so a misconfigured deployment cannot silently reach the
#: internet from here.
Transport = Callable[[str, str, Optional[Mapping[str, Any]]], Mapping[str, Any]]

#: API v4 paths. These three are the ones ADR-0061 rests on.
BOTS_PATH = "/api/v4/bots"
POSTS_PATH = "/api/v4/posts"
DIALOGS_OPEN_PATH = "/api/v4/actions/dialogs/open"
ME_PATH = "/api/v4/users/me"


class MattermostWire:
    """The narrow boundary where our model becomes Mattermost's wire format.

    TODO(mattermost-wire): confirm every item below against a running
    Mattermost Team Edition 11.x before pointing this at a real server — not
    against this code, and not against a model's recollection:

      1. `POST /api/v4/bots` — the exact request keys (`username`,
         `display_name`, `description` are what we send) and, more importantly,
         the response key holding the bot's **user id**. We read `user_id` and
         fall back to `id`; one of those is wrong.
      2. `POST /api/v4/posts` — whether a threaded reply is `root_id` (what we
         send) and whether interactive buttons must live under
         `props.attachments[].actions[]` with an `integration.url` and
         `integration.context`, as modelled in `approval_post`.
      3. `POST /api/v4/actions/dialogs/open` — a `trigger_id` is issued to an
         *integration responding to a user action*. Whether a bot can obtain
         one for an agent-initiated approval, out of band, is the single
         riskiest assumption in this adapter; `open_dialog` exists for the
         click path and `open_approval` deliberately does not depend on it.
      4. The **callback** Mattermost POSTs to us when a button is pressed: the
         key holding the acting user (`user_id`), the key holding our own
         `context`, and what — if anything — authenticates the request beyond
         the token we put in that context ourselves. If Mattermost signs these,
         we must verify the signature instead and `verify` below becomes weaker
         than what the platform offers.
      5. Whether a bot account really is excluded from the Team Edition user
         count, which is the reason it was chosen over the alternatives.
    """

    @staticmethod
    def create_bot(username: str, display_name: str, description: str) -> dict[str, Any]:
        return {
            "username": username,
            "display_name": display_name,
            "description": description,
        }

    @staticmethod
    def bot_id(response: Mapping[str, Any]) -> str:
        # Item 1 above: two plausible keys, one guess, stated as a guess.
        return str(response.get("user_id") or response.get("id") or "")

    @staticmethod
    def post(channel_id: str, message: str, root_id: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {"channel_id": channel_id, "message": message}
        if root_id:
            body["root_id"] = root_id
        return body

    @staticmethod
    def approval_post(
        request: ApprovalRequest, *, callback_url: str, token: str
    ) -> dict[str, Any]:
        """A message carrying two buttons, each naming the request it answers.

        The correlation the ledger needs travels in `integration.context`, so a
        click comes back saying which request, whose tenant, and with the token
        that makes the callback something more than an unauthenticated POST.
        """
        def action(decision: ApprovalDecision, label: str) -> dict[str, Any]:
            return {
                "id": f"{request.id}:{decision.value}",
                "name": label,
                "type": "button",
                "integration": {
                    "url": callback_url,
                    "context": {
                        "request_id": request.id,
                        "tenant_id": request.tenant_id,
                        "agent_id": request.agent_id,
                        "session_id": request.session_id,
                        "decision": decision.value,
                        "token": token,
                    },
                },
            }

        text = request.question
        if request.detail:
            text = f"{text}\n\n{request.detail}"
        return {
            "channel_id": request.channel_id,
            "message": text,
            "props": {
                "attachments": [
                    {
                        "text": text,
                        "actions": [
                            action(ApprovalDecision.APPROVE, "Approve"),
                            action(ApprovalDecision.DENY, "Deny"),
                        ],
                    }
                ]
            },
        }

    @staticmethod
    def dialog(request: ApprovalRequest, *, trigger_id: str, callback_url: str
               ) -> dict[str, Any]:
        return {
            "trigger_id": trigger_id,
            "url": callback_url,
            "dialog": {
                "callback_id": request.id,
                "title": "Approval required"[:24],
                "submit_label": "Decide",
                "state": request.id,
                "elements": [
                    {
                        "display_name": "Decision",
                        "name": "decision",
                        "type": "select",
                        "options": [
                            {"text": "Approve", "value": ApprovalDecision.APPROVE.value},
                            {"text": "Deny", "value": ApprovalDecision.DENY.value},
                        ],
                    }
                ],
            },
        }


class MattermostBridge:
    """A `ChannelBridge` over an injected Mattermost transport."""

    def __init__(
        self,
        transport: Transport,
        *,
        tenant_id: str,
        bot_username: str = "orgagents",
        bot_display_name: str = "OrgAgents",
        callback_url: str = "",
        callback_token: str = "",
        wire: Optional[MattermostWire] = None,
    ) -> None:
        if not tenant_id:
            raise ValueError("a bridge instance belongs to exactly one tenant")
        if not callback_token:
            # Without it there is nothing to distinguish a real click from a
            # POST anybody could make, and rule 3 would be satisfied on paper
            # only.
            raise ValueError(
                "a callback token is required: an unauthenticated callback is "
                "not an approval (ADR-0061 rule 3)"
            )
        self.transport = transport
        self.tenant_id = tenant_id
        self.bot_username = bot_username
        self.bot_display_name = bot_display_name
        self.callback_url = callback_url
        self.callback_token = callback_token
        self.wire = wire or MattermostWire()
        self._principals: dict[str, BotPrincipal] = {}

    # -- declaration -------------------------------------------------------

    def capabilities(self) -> BridgeCapabilities:
        return BridgeCapabilities(
            name="mattermost",
            principal_kind=PrincipalKind.SERVICE,
            bot_principal_id=self.bot_username,
            can_mint_bot_principal=True,
            interactive_callbacks=True,
            threaded_replies=True,
            tenant_id=self.tenant_id,
            notes=(
                "Team Edition 11.x. Claims come from in-tree notes and search "
                "extracts; no server has been contacted from here."
            ),
        )

    # -- identity (rule 2) -------------------------------------------------

    def identify(self, *, agent_id: str = "") -> BotPrincipal:
        """Mint (or recall) the bot this agent speaks as.

        One principal per agent, not one shared account: the org chart claims
        each agent appears as itself, and a shared bot would make that false
        while looking fine in the channel.
        """
        key = agent_id or self.bot_username
        cached = self._principals.get(key)
        if cached is not None:
            return cached
        username = f"{self.bot_username}-{agent_id}" if agent_id else self.bot_username
        display = f"{self.bot_display_name} · {agent_id}" if agent_id else (
            self.bot_display_name
        )
        response = self.transport(
            "POST",
            BOTS_PATH,
            self.wire.create_bot(username, display, f"tenant {self.tenant_id}"),
        )
        principal = BotPrincipal(
            id=self.wire.bot_id(response),
            username=username,
            display_name=display,
            is_bot=True,
            agent_id=agent_id,
        )
        if not principal.id:
            raise PostFailed(
                f"Mattermost returned no id for bot '{username}'; refusing to "
                "post as an unidentified principal (ADR-0061 rule 2)"
            )
        self._principals[key] = principal
        return principal

    # -- posting -----------------------------------------------------------

    def post(self, channel_id: str, text: str, *, agent_id: str = "") -> PostedMessage:
        principal = self.identify(agent_id=agent_id)
        response = self.transport("POST", POSTS_PATH, self.wire.post(channel_id, text))
        return self._posted(response, channel_id, text, principal)

    def reply(
        self, channel_id: str, thread_id: str, text: str, *, agent_id: str = ""
    ) -> PostedMessage:
        principal = self.identify(agent_id=agent_id)
        response = self.transport(
            "POST", POSTS_PATH, self.wire.post(channel_id, text, root_id=thread_id)
        )
        message = self._posted(response, channel_id, text, principal)
        message.thread_id = str(response.get("root_id") or thread_id)
        return message

    def open_approval(self, request: ApprovalRequest) -> PostedMessage:
        """Post the decision as two buttons carrying the correlation context.

        A message with actions rather than a dialog: a dialog needs a
        `trigger_id`, and a `trigger_id` comes from a user action, so an
        agent-initiated approval has none to use (item 3 above).
        """
        principal = self.identify(agent_id=request.agent_id)
        body = self.wire.approval_post(
            request, callback_url=self.callback_url, token=self.callback_token
        )
        response = self.transport("POST", POSTS_PATH, body)
        return self._posted(response, request.channel_id, request.question, principal)

    def open_dialog(self, request: ApprovalRequest, *, trigger_id: str) -> Mapping[str, Any]:
        """Escalate a click into a form. Only valid with a live `trigger_id`."""
        if not trigger_id:
            raise PostFailed(
                "a dialog needs a trigger_id issued by a user action; there is "
                "no way to open one out of band (TODO(mattermost-wire) item 3)"
            )
        return self.transport(
            "POST",
            DIALOGS_OPEN_PATH,
            self.wire.dialog(request, trigger_id=trigger_id,
                             callback_url=self.callback_url),
        )

    # -- callbacks (rules 3 and 4) ----------------------------------------

    def resolve_approval(self, payload: Mapping[str, Any]) -> ApprovalCallback:
        """Authenticate a button press and say what it claims. No decision.

        Whether the click is *honoured* is the ledger's business; this method's
        only job is to refuse anything it cannot attribute to the platform and
        to a person.
        """
        context = payload.get("context")
        if not isinstance(context, Mapping):
            raise CallbackNotAuthenticated(
                "callback carried no integration context, so there is nothing "
                "saying which request it answers"
            )
        token = str(context.get("token") or "")
        if not hmac.compare_digest(token, self.callback_token):
            raise CallbackNotAuthenticated(
                "callback token did not match this bridge's; anyone can POST "
                "to a callback URL, so an unverified press is not an approval"
            )
        responder = str(payload.get("user_id") or "")
        if not responder:
            raise CallbackNotAuthenticated(
                "callback named no acting user, so it cannot prove which "
                "human clicked (ADR-0061 rule 4)"
            )
        raw_decision = str(context.get("decision") or "")
        try:
            decision = ApprovalDecision(raw_decision)
        except ValueError:
            raise CallbackNotAuthenticated(
                f"callback carried no decision we recognise ('{raw_decision}')"
            ) from None
        return ApprovalCallback(
            request_id=str(context.get("request_id") or ""),
            responder_id=responder,
            responder_contact=str(
                payload.get("user_email") or payload.get("user_name") or responder
            ),
            tenant_id=str(context.get("tenant_id") or ""),
            decision=decision,
            received_at=now(),
            raw=dict(payload),
        )

    # -- inbound (rule 5) --------------------------------------------------

    def parse_inbound(self, payload: Mapping[str, Any]) -> InboundMessage:
        """Turn an incoming post into untrusted text. It is not screened here.

        TODO(mattermost-wire) item 6: outgoing webhooks and the websocket event
        stream use different key names for the same fields; the ones read below
        (`channel_id`, `root_id`, `user_id`, `user_name`, `text`/`message`)
        cover both shapes we have seen described, which is not the same as
        having seen one.
        """
        return InboundMessage(
            channel_id=str(payload.get("channel_id") or ""),
            thread_id=str(payload.get("root_id") or payload.get("post_id") or ""),
            author_id=str(payload.get("user_id") or ""),
            author_contact=str(payload.get("user_email") or payload.get("user_name") or ""),
            text=str(payload.get("text") or payload.get("message") or ""),
            tenant_id=self.tenant_id,
            raw=dict(payload),
        )

    # -- internals ---------------------------------------------------------

    def _posted(
        self,
        response: Mapping[str, Any],
        channel_id: str,
        text: str,
        principal: BotPrincipal,
    ) -> PostedMessage:
        post_id = str(response.get("id") or "")
        if not post_id:
            raise PostFailed(
                f"Mattermost accepted nothing for channel '{channel_id}': the "
                "response carried no post id"
            )
        return PostedMessage(
            id=post_id,
            channel_id=str(response.get("channel_id") or channel_id),
            thread_id=str(response.get("root_id") or ""),
            text=text,
            posted_by=principal.id,
            raw=dict(response),
        )


__all__ = [
    "BOTS_PATH",
    "DIALOGS_OPEN_PATH",
    "ME_PATH",
    "MattermostBridge",
    "MattermostWire",
    "POSTS_PATH",
    "Transport",
]
