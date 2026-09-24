"""Data semantics and contracts (ADR-0099).

None of this reads the producing system's schema, and it must not be
mistaken for having done so. What it checks is that the design is coherent
about what it relies on — which is the half this platform owns.
"""
from __future__ import annotations

from ..model import DataRelationKind
from .context import ValidationContext
from .registry import rule

SECTION = "data semantics and contracts"


@rule(SECTION, {"unknown_data_relation"})
def relations_resolve(ctx: ValidationContext) -> None:
    for data_class in ctx.spec.data_classes:
        for relation in data_class.relations:
            if relation.target not in ctx.classes_by_id:
                ctx.err("unknown_data_relation",
                        f"data class '{data_class.id}' declares a "
                        f"{relation.kind.value} relation to '{relation.target}', "
                        "which the spec does not declare", data_class.id)


@rule(SECTION, {"data_derivation_cycle", "derived_class_drops_restriction"})
def derivation_keeps_restrictions(ctx: ValidationContext) -> None:
    # A restriction that a derived class may drop is a restriction that leaks
    # through the first summary anybody builds.
    classes_by_id, err = ctx.classes_by_id, ctx.err

    def _sources(class_id: str, seen: set[str]) -> list[str]:
        out: list[str] = []
        current = classes_by_id.get(class_id)
        if current is None or class_id in seen:
            return out
        seen.add(class_id)
        for relation in current.relations:
            if relation.kind is not DataRelationKind.DERIVED_FROM:
                continue
            out.append(relation.target)
            out += _sources(relation.target, seen)
        return out

    for data_class in ctx.spec.data_classes:
        chain: set[str] = set()
        sources = _sources(data_class.id, chain)
        if data_class.id in sources:
            err("data_derivation_cycle",
                f"data class '{data_class.id}' is derived from itself, "
                "directly or through a chain", data_class.id)
            continue
        for source_id in sources:
            source = classes_by_id.get(source_id)
            if source is None:
                continue
            if data_class.may_leave_region and not source.may_leave_region:
                err("derived_class_drops_restriction",
                    f"data class '{data_class.id}' is derived from "
                    f"'{source_id}', which may not leave its region, and "
                    "says that it may. A restriction that a summary can drop "
                    "is not a restriction", data_class.id)
            if data_class.may_appear_in_traces and not source.may_appear_in_traces:
                err("derived_class_drops_restriction",
                    f"data class '{data_class.id}' is derived from "
                    f"'{source_id}', which may not appear in traces, and says "
                    "that it may", data_class.id)


@rule(SECTION, {"unknown_produced_data_class", "unknown_dependency_data_class",
                "unknown_data_producer", "producer_does_not_produce",
                "externally_produced_data", "whole_class_dependency"})
def dependencies_have_producers(ctx: ValidationContext) -> None:
    classes_by_id, err, warn = ctx.classes_by_id, ctx.err, ctx.warn
    agents_by_id = ctx.agents_by_id
    producers: dict[str, list[str]] = {}
    for agent in ctx.agents:
        for class_id in getattr(agent, "produces_data", []) or []:
            if class_id not in classes_by_id:
                err("unknown_produced_data_class",
                    f"agent '{agent.id}' produces '{class_id}', which the "
                    "spec does not declare as a data class", agent.id)
                continue
            producers.setdefault(class_id, []).append(agent.id)

    for agent in ctx.agents:
        for dependency in getattr(agent, "data_dependencies", []) or []:
            if dependency.data_class not in classes_by_id:
                err("unknown_dependency_data_class",
                    f"agent '{agent.id}' depends on data class "
                    f"'{dependency.data_class}', which the spec does not "
                    "declare", agent.id)
                continue
            if dependency.produced_by:
                if dependency.produced_by not in agents_by_id:
                    err("unknown_data_producer",
                        f"agent '{agent.id}' names '{dependency.produced_by}' "
                        f"as the producer of '{dependency.data_class}', which "
                        "is not an agent in this design", agent.id)
                elif dependency.data_class not in (
                        getattr(agents_by_id[dependency.produced_by],
                                "produces_data", []) or []):
                    err("producer_does_not_produce",
                        f"agent '{agent.id}' relies on "
                        f"'{dependency.produced_by}' for "
                        f"'{dependency.data_class}', and that agent does not "
                        "declare it produces it. A contract with one party is "
                        "not a contract", agent.id)
            elif dependency.data_class not in producers:
                # Normal, and worth saying once: most organizations' data comes
                # from outside, and a reader should know which half this is.
                warn("externally_produced_data",
                     f"agent '{agent.id}' depends on '{dependency.data_class}', "
                     "which nothing in this design produces. Its freshness and "
                     "shape are promised by a system outside this platform",
                     agent.id)
            if not dependency.fields:
                warn("whole_class_dependency",
                     f"agent '{agent.id}' depends on all of "
                     f"'{dependency.data_class}' rather than named fields, so "
                     "a change to any part of it is a change to this agent",
                     agent.id)
