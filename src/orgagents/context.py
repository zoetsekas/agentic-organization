"""Keeping a long run affordable and usable (ADR-0036).

Two mechanisms, both borrowed from what production agent harnesses converged
on, and both declared in the spec rather than buried in a runtime:

* **Offloading.** A tool that returns a 200 KB result should not put 200 KB in
  the context window. It goes to the **artifact store** and the agent gets a
  reference it can read back — in full or in part — if it actually needs to.
* **Summarization.** Past a threshold, older turns are summarized and the most
  recent are kept verbatim. The summary is retained as a session memory, so
  what was compressed is recoverable rather than lost.

The artifact store is deliberately a third thing, distinct from memory (what an
agent *learned*) and from the sandbox (where it *executes*). Conflating them is
how a scratch file becomes permanent knowledge nobody classified.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .data.planes import AccessDenied
from .ids import new_id, now_iso
from .spec.model import ArtifactStore, ContextPolicy, SharingScope
from .store import Store

ARTIFACTS = "artifacts"


class ArtifactError(RuntimeError):
    """Raised when a write breaks the store's declared limits."""


class Artifact(BaseModel):
    """One file in a workspace."""

    id: str = Field(default_factory=lambda: new_id("art"))
    store_id: str
    agent_id: str
    session_id: Optional[str] = None
    path: str = ""
    content: str = ""
    media_type: str = "text/plain"
    size_bytes: int = 0
    data_class: str = ""
    scope: SharingScope = SharingScope.PRIVATE
    groups: list[str] = Field(default_factory=list)
    digest: str = ""
    source: str = ""
    created_at: str = Field(default_factory=now_iso)
    expires_at: Optional[str] = None

    def reference(self) -> str:
        """What the agent sees in place of the content."""
        return (
            f"[artifact {self.id} · {self.path or 'untitled'} · "
            f"{self.size_bytes} bytes · read with artifact_read('{self.id}')]"
        )

    def expired(self, now: Optional[datetime] = None) -> bool:
        if not self.expires_at:
            return False
        return datetime.fromisoformat(self.expires_at) <= (
            now or datetime.now(timezone.utc))


@dataclass
class ResolvedContext:
    """An agent's effective context and workspace policy."""

    agent_id: str
    policy: ContextPolicy
    store: Optional[ArtifactStore] = None
    groups: tuple[str, ...] = ()
    readable_data_classes: tuple[str, ...] = ()


@dataclass
class Turn:
    """One entry in a thread, as the context manager sees it."""

    role: str
    content: str

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.content)


@dataclass
class CompactionResult:
    turns: list[Turn]
    summary: Optional[str] = None
    tokens_before: int = 0
    tokens_after: int = 0
    compacted: bool = False
    offloaded: list[str] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """A deliberately rough estimate: about four characters per token.

    Exactness is the model provider's job; this only has to decide *when* to
    act, and being 20% out moves a threshold, not a behaviour.
    """
    return max(1, len(text) // 4)


class ArtifactWorkspace:
    """Reads and writes files under an artifact store's declared limits."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def write(
        self, context: ResolvedContext, content: str, *, path: str = "",
        session_id: Optional[str] = None, data_class: str = "",
        media_type: str = "text/plain", source: str = "",
        now: Optional[datetime] = None,
    ) -> Artifact:
        if context.store is None:
            raise ArtifactError(
                f"{context.agent_id} has no artifact store; declare one before "
                "offloading"
            )
        now = now or datetime.now(timezone.utc)
        declared = context.store
        size = len(content.encode())
        if size > declared.max_file_bytes:
            raise ArtifactError(
                f"artifact is {size} bytes, over the {declared.max_file_bytes} "
                f"limit for store '{declared.id}'"
            )
        if data_class:
            if (context.readable_data_classes
                    and data_class not in context.readable_data_classes):
                raise AccessDenied(
                    f"{context.agent_id} may not read '{data_class}', so it may "
                    "not store it"
                )
            if declared.data_classes and data_class not in declared.data_classes:
                raise ArtifactError(
                    f"store '{declared.id}' does not hold data class '{data_class}'"
                )
        if (declared.scope is SharingScope.PROTECTED
                and not set(declared.groups) & set(context.groups)):
            raise AccessDenied(
                f"{context.agent_id} is not in a group that owns store "
                f"'{declared.id}'"
            )
        self._enforce_total(context, size)

        artifact = Artifact(
            store_id=declared.id, agent_id=context.agent_id, session_id=session_id,
            path=path or f"{new_id('file')}.txt", content=content,
            media_type=media_type, size_bytes=size, data_class=data_class,
            scope=declared.scope, groups=list(declared.groups),
            digest=hashlib.sha256(content.encode()).hexdigest()[:16], source=source,
            expires_at=(now + timedelta(days=declared.retention_days)).isoformat()
            if declared.retention_days else None,
        )
        self.store.put(ARTIFACTS, artifact, parent=declared.id, name=artifact.path)
        return artifact

    def _enforce_total(self, context: ResolvedContext, incoming: int) -> None:
        declared = context.store
        assert declared is not None
        existing = self.list(context)
        total = sum(a.size_bytes for a in existing) + incoming
        if total <= declared.max_total_bytes:
            return
        # Make room oldest-first rather than refusing: a full scratch space is
        # an operational problem, not a reason to fail the run.
        for artifact in sorted(existing, key=lambda a: a.created_at):
            self.store.delete(ARTIFACTS, artifact.id)
            total -= artifact.size_bytes
            if total <= declared.max_total_bytes:
                return
        raise ArtifactError(
            f"artifact does not fit in store '{declared.id}' even when empty"
        )

    def read(self, context: ResolvedContext, artifact_id: str, *,
             offset: int = 0, limit: Optional[int] = None) -> Optional[Artifact]:
        artifact = self.store.get(ARTIFACTS, artifact_id, Artifact)
        if artifact is None or not self.may_read(context, artifact):
            return None
        if offset or limit:
            excerpt = artifact.model_copy(deep=True)
            end = offset + limit if limit else None
            excerpt.content = artifact.content[offset:end]
            return excerpt
        return artifact

    def may_read(self, context: ResolvedContext, artifact: Artifact) -> bool:
        if artifact.scope is SharingScope.PRIVATE:
            return artifact.agent_id == context.agent_id
        if artifact.scope is SharingScope.PROTECTED:
            return bool(set(artifact.groups) & set(context.groups))
        return True

    def list(self, context: ResolvedContext,
             session_id: Optional[str] = None) -> list[Artifact]:
        if context.store is None:
            return []
        found = self.store.list(ARTIFACTS, Artifact, parent=context.store.id,
                                limit=2000)
        return [
            a for a in found
            if self.may_read(context, a)
            and (session_id is None or a.session_id == session_id)
        ]

    def expire(self, now: Optional[datetime] = None) -> int:
        now = now or datetime.now(timezone.utc)
        dropped = 0
        for artifact in self.store.list(ARTIFACTS, Artifact, limit=10000):
            if artifact.expired(now):
                self.store.delete(ARTIFACTS, artifact.id)
                dropped += 1
        return dropped


class ContextManager:
    """Decides when to offload and when to compact."""

    def __init__(self, workspace: ArtifactWorkspace) -> None:
        self.workspace = workspace

    # -- offloading --------------------------------------------------------

    def should_offload(self, context: ResolvedContext, content: str) -> bool:
        threshold = context.policy.offload_tool_output_bytes
        return bool(threshold) and len(content.encode()) > threshold

    def offload(self, context: ResolvedContext, content: str, *, label: str,
                session_id: Optional[str] = None,
                data_class: str = "") -> tuple[str, Optional[Artifact]]:
        """Replace oversized content with a readable reference."""
        if not self.should_offload(context, content) or context.store is None:
            return content, None
        artifact = self.workspace.write(
            context, content, path=f"{label}.txt", session_id=session_id,
            data_class=data_class, source=label,
        )
        head = content[:400].rstrip()
        return (
            f"{artifact.reference()}\n\nFirst 400 characters:\n{head}…",
            artifact,
        )

    # -- compaction --------------------------------------------------------

    def should_compact(self, context: ResolvedContext, turns: list[Turn]) -> bool:
        return sum(t.tokens for t in turns) > context.policy.summarize_after_tokens

    def compact(
        self, context: ResolvedContext, turns: list[Turn],
        summarizer: Optional[Any] = None,
    ) -> CompactionResult:
        """Summarize older turns, keep the recent ones verbatim."""
        before = sum(t.tokens for t in turns)
        if not self.should_compact(context, turns):
            return CompactionResult(turns, tokens_before=before, tokens_after=before)

        keep = max(context.policy.keep_last_turns, 1)
        older, recent = turns[:-keep], turns[-keep:]
        if not older:
            return CompactionResult(turns, tokens_before=before, tokens_after=before)

        summary = _summarize_with(summarizer, older)
        compacted = [Turn("system", f"Summary of {len(older)} earlier turns:\n{summary}")]
        compacted += recent
        after = sum(t.tokens for t in compacted)
        if after >= before:
            # A "summary" longer than what it replaces is not a summary. This
            # happens with many short turns, and applying it would cost tokens
            # and lose detail at the same time.
            return CompactionResult(turns, tokens_before=before, tokens_after=before)
        return CompactionResult(compacted, summary=summary, tokens_before=before,
                                tokens_after=after, compacted=True)


@runtime_checkable
class Summarizer(Protocol):
    """What compaction needs from anything that condenses older turns.

    A plain callable satisfies it too, and `_summarize_with` accepts either —
    the runtime has always taken a function here and there is no reason to
    break that to gain an interface.
    """

    name: str

    def summarize(self, turns: list[Turn]) -> str:
        ...


def _summarize_with(summarizer: Optional[Any], turns: list[Turn]) -> str:
    if summarizer is None:
        return FirstLastSummarizer().summarize(turns)
    if hasattr(summarizer, "summarize"):
        return summarizer.summarize(turns)
    return summarizer(turns)


class FirstLastSummarizer:
    """The honest fallback used when no model summarizer is supplied.

    It keeps the first and last exchange verbatim and counts the rest, and
    says so in the text it produces. That is honest about what was dropped,
    which a fabricated prose summary would not be — so this stays the default
    and stays truthful about being structural rather than a real summary.
    """

    name = "first_last"

    def summarize(self, turns: list[Turn]) -> str:
        return _fallback_summary(turns)


class ModelSummarizer:
    """Real summarization, behind the same protocol (ADR-0045).

    `complete` is any callable taking a prompt and returning text, supplied by
    the deployment; nothing here imports a provider SDK or names a model.
    `model_class` records what the context policy asked for — a `ModelClass`
    value from the spec — and the binding decides what satisfies it.

    When the call fails or comes back empty, compaction falls back to the
    structural summary rather than dropping the older turns: losing a thread
    because a summarizer was unreachable is worse than a coarse summary, and
    the fallback text still says what it did.
    """

    name = "model"

    PROMPT = (
        "Summarize the conversation below for an agent that must continue it.\n"
        "Keep decisions, commitments, open questions, identifiers and numbers.\n"
        "Drop pleasantries and repetition. Be shorter than the original.\n"
        "Write prose, no preamble.\n\n{thread}"
    )

    def __init__(self, complete: Callable[[str], str], *,
                 model_class: str = "",
                 fallback: Optional[Any] = None) -> None:
        self.complete = complete
        self.model_class = model_class
        self.fallback = fallback or FirstLastSummarizer()

    def summarize(self, turns: list[Turn]) -> str:
        thread = "\n".join(f"{t.role}: {t.content}" for t in turns)
        try:
            summary = self.complete(self.PROMPT.format(thread=thread))
        except Exception as e:  # any provider failure, not just one shape
            return (f"{_summarize_with(self.fallback, turns)}\n"
                    f"- summarizer unavailable ({type(e).__name__}); "
                    "this is a structural summary, not a written one")
        summary = (summary or "").strip()
        if not summary:
            return (f"{_summarize_with(self.fallback, turns)}\n"
                    "- summarizer returned nothing; this is a structural "
                    "summary, not a written one")
        return summary


def _fallback_summary(turns: list[Turn]) -> str:
    """A structural summary used when no model summarizer is supplied.

    It keeps the first and last exchange verbatim and counts the rest. That is
    honest about what was dropped, which a fabricated prose summary would not
    be.
    """
    first, last = turns[0], turns[-1]
    # Quote proportionally: a fixed 200 characters is longer than the turns
    # themselves when a thread is made of many short ones.
    budget = max(60, min(200, sum(len(t.content) for t in turns) // (len(turns) * 4)))
    return (
        f"- opened with ({first.role}): {first.content[:budget]}\n"
        f"- {max(len(turns) - 2, 0)} intermediate turn(s) omitted\n"
        f"- most recent before this summary ({last.role}): {last.content[:budget]}"
    )
