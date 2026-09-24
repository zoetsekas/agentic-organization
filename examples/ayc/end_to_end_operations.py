#!/usr/bin/env python3
"""AYC, operating: an organization that coordinates, covers and prioritises.

    PYTHONPATH=src python3 examples/ayc/end_to_end_operations.py

The other end-to-end scripts take a design the whole distance from spec to
generated stack. This one starts where they stop, and runs the things an
organization does *once it is up*:

  * a leader **fans work out** to several agents at once and reconciles
    afterwards, instead of asking each in turn (ADR-0093);
  * a leader **hits its parallel bound** and is told which of the things it is
    holding matters least (ADR-0097);
  * a leader **fails repeatedly and is stood in for**, with its mandate
    inherited under a bound and a separation of duties still holding
    (ADR-0094);
  * a leader **reads what its whole team is doing**, not just what it
    personally assigned;
  * a **cyclic workflow** runs, and the bound on a loop that does not converge
    says which bound it was (ADR-0096).

Every stage is a real call into the runtime the API and the CLI drive. If a
stage would fail, this script fails at it. A test
(`tests/test_end_to_end_operations.py`) runs the whole thing so it cannot rot.

Nothing here needs a model, a network or a container: the echo adapter runs
every agent deterministically, because what is being demonstrated is the
coordination, not the prose.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from orgagents.models import Agent, SessionState          # noqa: E402
from orgagents.platform import Platform                   # noqa: E402
from orgagents.runtime.loader import load_system          # noqa: E402
from orgagents.compiler.ir import build_ir                # noqa: E402
from orgagents.spec.loader import load_spec               # noqa: E402
from orgagents.workflows.engine import WorkflowEngine     # noqa: E402

SPEC = ROOT / "examples" / "ayc" / "ayc.system.yaml"


def banner(step: str, title: str) -> None:
    print(f"\n{'─' * 72}\n{step}  {title}\n{'─' * 72}")


def main() -> dict:
    out: dict = {}
    spec = load_spec(SPEC)
    ir = build_ir(spec)

    # The store keeps its SQLite file open; on Windows that stops the
    # directory being removed, which is not a reason to fail the run.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        platform = Platform(str(pathlib.Path(tmp) / "ayc.db"),
                            configure_logs=False)
        load_system(platform, ir)
        runtime, org, sessions = platform.runtime, platform.org, platform.sessions

        # 1. Fan out ------------------------------------------------------
        banner("1", "A leader asks four agents at once")
        coo = org.agent("coo_agent")
        coo.harness.max_parallel_subagents = 4
        org.add_agent(coo)
        session = sessions.create(coo.id, title="Monday stand-up")
        tools = runtime._delegation_tools(coo, session.id)

        work = [
            ("buyer_agent", "What is on order and when does it land?", "normal"),
            ("warehouse_agent", "Anything stuck on the dock?", "urgent"),
            ("inventory_agent", "Where did last week's count land?", "low"),
            ("ap_agent", "Which invoices are held for a match?", "high"),
        ]
        handles = []
        for agent_id, task, priority in work:
            reply = tools["assign"](agent_id, task, priority=priority)
            assert reply["ok"], reply
            handles.append(reply["handle"])
            print(f"  assigned {priority:>6} → {agent_id}")
        print(f"\n  four handles held at once; serial delegation could hold one")

        collected = tools["gather"](handles, timeout_s=30)
        print("\n  collected, most important first:")
        for row in collected["results"]:
            print(f"    {row['priority']:>6}  {row['to']:<18} "
                  f"{row['state']}")
        out["fanned_out"] = len(collected["results"])
        out["priority_order"] = [r["priority"] for r in collected["results"]]

        # 2. The bound ----------------------------------------------------
        banner("2", "The parallel bound refuses, and says what to drop")
        coo.harness.max_parallel_subagents = 2
        org.add_agent(coo)
        session2 = sessions.create(coo.id, title="A busier morning")
        tools2 = runtime._delegation_tools(org.agent(coo.id), session2.id)

        gate = threading.Event()
        real_run = runtime.run

        def gated(agent_id, prompt, **kw):
            if kw.get("session_id"):
                assert gate.wait(timeout=20)
            return real_run(agent_id, prompt, **kw)

        runtime.run = gated
        try:
            tools2["assign"]("warehouse_agent", "Dock status.", priority="urgent")
            tools2["assign"]("inventory_agent", "Cycle count.", priority="low")
            refused = tools2["assign"]("buyer_agent", "Reorder.", priority="urgent")
            print(f"  ok: {refused['ok']}")
            print(f"  {refused['error']}")
            out["bound_refusal"] = refused["error"]
        finally:
            gate.set()
            runtime.run = real_run
        tools2["gather"](runtime.unsettled_handles(session2.id), timeout_s=30)

        # 3. Standing in --------------------------------------------------
        banner("3", "A leader fails, and somebody covers it")
        ecom = org.agent("ecommerce_agent")
        print(f"  {ecom.name} declares successor: {ecom.successor_agent_id}")
        print(f"  its mandate: {ecom.mandate}")

        threshold = ecom.harness.escalate_to_human_after_failures
        for _ in range(threshold):
            failed = sessions.create(ecom.id)
            sessions.set_state(failed.id, SessionState.FAILED)
        print(f"  {runtime.consecutive_failures(ecom.id)} consecutive failures "
              f"(threshold {threshold})")

        standing = runtime.stand_down(ecom.id)
        print(f"\n  {standing.describe()}")
        print(f"  conferred: {standing.decisions}")
        print(f"  withheld:  {[w.describe() for w in standing.withheld] or 'nothing'}")
        holder = org.mandate_holder(ecom.id, "publish_product")
        print(f"  who may publish a product now: {holder.name}")
        out["standing_in"] = standing.successor_agent_id
        out["conferred"] = standing.decisions

        # What the gate would have refused, and why it matters here.
        banner("3b", "The stand-in a separation forbids")
        cs = org.agent("cs_agent")
        ar = org.agent("ar_agent")
        print(f"  {cs.name} holds {cs.mandate}")
        print(f"  {ar.name} holds {ar.mandate}")
        print("  the 'refund_and_receivable' separation keeps those two hands")
        print("  apart, so ar_agent may not stand in for cs_agent — and the")
        print("  phase gate refuses that spec rather than leaving it to be")
        print("  discovered during the outage it was meant to survive.")
        forced = org.stand_in_for(
            cs.id,
            separations=[{"id": "refund_and_receivable",
                          "decisions": ["issue_refund", "record_receivable"],
                          "reason": "a refund and a written-off balance are "
                                    "two hands"}],
        )
        # Its manager stands in by hierarchy, conferring nothing.
        print(f"\n  in fact: {forced.describe()}")
        out["cs_stand_in_by_hierarchy"] = forced.by_hierarchy

        # 4. What is my team doing ----------------------------------------
        banner("4", "A leader reads its whole team")
        parked = sessions.create("warehouse_agent", title="Waiting on a person")
        sessions.set_state(parked.id, SessionState.WAITING_HUMAN)
        running = sessions.create("buyer_agent", title="Reordering")
        sessions.set_state(running.id, SessionState.RUNNING)

        rows = runtime.team_workload("coo_agent")
        for row in rows[:6]:
            mark = "⏸ waiting on a person" if row["waiting_human"] else "running"
            cover = (f" (covered by {row['stood_in_for_by']})"
                     if row["stood_in_for_by"] else "")
            print(f"  {row['agent']:<20} {mark}{cover}")
        print(f"\n  {len(rows)} unsettled across the subtree; "
              "nothing outside it is visible")
        out["team_rows"] = len(rows)
        assert not [r for r in rows if r["agent_id"] == "ecommerce_agent"], \
            "the COO can see into Growth, which is not its subtree"

        # 5. A cyclic workflow --------------------------------------------
        banner("5", "A loop that converges, and one that does not")
        from orgagents.models import WorkflowRef
        restock = next(w for w in ir.workflows if w.id == "restock_review")
        ref = WorkflowRef(id=restock.id, name=restock.name,
                          graph=restock.graph)

        rounds = {"n": 0}

        def tool_caller(name, args):
            rounds["n"] += 1
            # Three SKUs to reorder, then the list is empty and the loop ends.
            return ["sku-1", "sku-2", "sku-3"][rounds["n"]:]

        def agent_caller(agent_id, args):
            return {"po": f"PO-{rounds['n']}"}

        engine = WorkflowEngine(tool_caller=tool_caller, agent_caller=agent_caller)
        result = engine.run(ref, {"low_stock": ["sku-1"]})
        print(f"  path: {' → '.join(result.path)}")
        print(f"  steps: {result.steps}, error: {result.error}")
        out["cycle_steps"] = result.steps
        out["cycle_error"] = result.error

        runaway = WorkflowRef(id="runaway", name="runaway", graph={
            "entry": "spin",
            "nodes": [{"id": "spin", "kind": "transform", "output": "x",
                       "expr": "1"}],
            "edges": [{"from": "spin", "to": "spin"}],
        })
        stuck = WorkflowEngine().run(runaway, max_steps=25)
        print(f"\n  a loop that never converges: {stuck.error}")
        out["runaway_error"] = stuck.error

        banner("✓", "AYC coordinated, covered, prioritised and looped")
        print("  Nothing above needed a model, a network or a container.")
        return out


if __name__ == "__main__":
    # Box-drawing on a Windows console without PYTHONUTF8=1.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    main()
