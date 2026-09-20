"""Two-tier memory: session scope, long-term governance, promotion (ADR-0028)."""
from datetime import datetime, timedelta, timezone

import pytest

from orgagents.data.planes import AccessDenied
from orgagents.memory import MemoryError, MemoryManager, ResolvedMemory
from orgagents.spec.model import (
    MemoryNamespace,
    MemoryPolicy,
    MemoryTier,
    RecallMode,
    SharingScope,
)
from orgagents.store import Store

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def manager(tmp_path):
    return MemoryManager(Store(tmp_path / "memory.db"))


def contract(
    agent_id="analyst", *, promotion=True, approval=False, groups=("finance",),
    readable=("finance_internal", "public_knowledge"), long_term=True,
) -> ResolvedMemory:
    return ResolvedMemory(
        agent_id=agent_id,
        session=MemoryPolicy(tier=MemoryTier.SESSION, max_items=3),
        long_term=MemoryPolicy(
            tier=MemoryTier.LONG_TERM, enabled=long_term, retention_days=365,
            promotion_allowed=promotion, promotion_requires_approval=approval,
            recall=RecallMode.ON_DEMAND, redact_data_classes=["customer_pii"],
        ),
        namespaces=[
            MemoryNamespace(id="query_patterns", scope=SharingScope.PROTECTED,
                            groups=["finance"], data_classes=["finance_internal"]),
            MemoryNamespace(id="company_facts", scope=SharingScope.PUBLIC,
                            data_classes=["public_knowledge"]),
        ],
        groups=groups,
        readable_data_classes=readable,
    )


def test_session_memory_is_scoped_to_its_session(manager):
    c = contract()
    manager.remember(c, "working note", session_id="ses1", key="note")
    assert [e.key for e in manager.session_memories(c, "ses1")] == ["note"]
    assert manager.session_memories(c, "ses2") == []


def test_session_memory_expires_even_without_retention(manager):
    c = contract()
    entry = manager.remember(c, "ephemeral", session_id="ses1", now=NOW)
    assert entry.expires_at is not None
    assert not entry.expired(NOW + timedelta(hours=23))
    assert entry.expired(NOW + timedelta(hours=25))


def test_session_memory_respects_its_item_cap(manager):
    c = contract()
    for i in range(5):
        manager.remember(c, f"note {i}", session_id="ses1", key=f"k{i}")
    remaining = [e.key for e in manager.session_memories(c, "ses1")]
    assert len(remaining) == 3 and "k0" not in remaining and "k4" in remaining


def test_promotion_carries_the_namespace_scope(manager):
    c = contract()
    entry = manager.remember(c, "invoices join customers", session_id="ses1",
                             key="join", data_class="finance_internal")
    promoted = manager.promote(c, entry.id, namespace="query_patterns")
    assert promoted.tier is MemoryTier.LONG_TERM
    assert promoted.scope is SharingScope.PROTECTED and promoted.groups == ["finance"]
    assert promoted.source.startswith("promoted:")


def test_promotion_can_be_forbidden_outright(manager):
    c = contract(promotion=False)
    entry = manager.remember(c, "note", session_id="ses1")
    with pytest.raises(MemoryError, match="not permitted"):
        manager.promote(c, entry.id, namespace="query_patterns")


def test_promotion_can_require_human_approval(manager):
    c = contract(approval=True)
    entry = manager.remember(c, "note", session_id="ses1",
                             data_class="finance_internal")
    with pytest.raises(MemoryError, match="human approval"):
        manager.promote(c, entry.id, namespace="query_patterns")
    assert manager.promote(c, entry.id, namespace="query_patterns", approved=True)


def test_only_the_owner_may_promote_or_forget(manager):
    mine, theirs = contract(), contract("cfo")
    entry = manager.remember(mine, "note", session_id="ses1",
                             data_class="finance_internal")
    with pytest.raises(AccessDenied):
        manager.promote(theirs, entry.id, namespace="query_patterns")
    with pytest.raises(AccessDenied):
        manager.forget(theirs, entry.id)
    assert manager.forget(mine, entry.id)


def test_a_namespace_refuses_a_class_it_does_not_hold(manager):
    c = contract()
    with pytest.raises(MemoryError, match="does not hold data class"):
        manager.remember(c, "x", tier=MemoryTier.LONG_TERM,
                         namespace="company_facts", data_class="finance_internal")


def test_an_agent_cannot_remember_what_it_may_not_read(manager):
    c = contract(readable=("public_knowledge",))
    with pytest.raises(AccessDenied, match="may not read"):
        manager.remember(c, "secret", session_id="ses1",
                         data_class="customer_pii")


def test_redaction_applies_to_configured_classes(manager):
    c = contract(readable=("customer_pii", "finance_internal"))
    c.namespaces.append(
        MemoryNamespace(id="regulated", scope=SharingScope.PRIVATE,
                        data_classes=["customer_pii"])
    )
    entry = manager.remember(c, "Ana Silva, id 12345", tier=MemoryTier.LONG_TERM,
                             namespace="regulated", data_class="customer_pii")
    assert entry.content == "[redacted: customer_pii]"


def test_recall_respects_sharing_scope(manager):
    finance, engineering = contract(), contract("sre", groups=("engineering",))
    engineering.namespaces = finance.namespaces
    manager.remember(finance, "revenue join pattern", tier=MemoryTier.LONG_TERM,
                     namespace="query_patterns", key="join",
                     data_class="finance_internal")
    manager.remember(finance, "the fiscal year starts in April",
                     tier=MemoryTier.LONG_TERM, namespace="company_facts",
                     key="fy", data_class="public_knowledge")
    assert {e.key for e in manager.recall(finance, "")} == {"join", "fy"}
    # Engineering is not in the finance group, so the protected one is invisible.
    assert {e.key for e in manager.recall(engineering, "")} == {"fy"}


def test_recall_ranks_by_relevance_and_counts_hits(manager):
    c = contract()
    manager.remember(c, "invoices join customers on customer_id",
                     tier=MemoryTier.LONG_TERM, namespace="query_patterns",
                     key="join", data_class="finance_internal")
    manager.remember(c, "headcount lives in the people table",
                     tier=MemoryTier.LONG_TERM, namespace="query_patterns",
                     key="headcount", data_class="finance_internal")
    found = manager.recall(c, "how do I join invoices")
    assert [e.key for e in found] == ["join"]
    assert manager.recall(c, "how do I join invoices")[0].recall_count == 2


def test_expired_memories_are_dropped(manager):
    c = contract()
    manager.remember(c, "note", session_id="ses1", now=NOW)
    assert manager.expire(NOW + timedelta(days=2)) == 1
    assert manager.session_memories(c, "ses1") == []


def test_stats_summarize_both_tiers(manager):
    c = contract()
    entry = manager.remember(c, "note", session_id="ses1",
                             data_class="finance_internal")
    manager.promote(c, entry.id, namespace="query_patterns")
    stats = manager.stats("analyst")
    assert stats["by_tier"] == {"session": 1, "long_term": 1}
    assert stats["namespaces"] == ["query_patterns"]
