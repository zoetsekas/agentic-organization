"""Running the evaluation cases the spec declares (WS-014 M3, ADR-0060).

`EvaluationCase` has existed since ADR-0022 and `evaluations_passed` has been
gating promotion since then — on nothing. The cases were declared, the gate
recorded the requirement, and no code ever ran a case. A governance control
that nobody executes is a promise, so this module executes them.

Three positions shape what is here, and all three cost something:

* **A case asserts what a machine can check, or it asserts nothing.** The
  declared `expect` is free text, so an assertion is chosen by prefix —
  `exact:`, `contains:`, `schema:`, `guardrail:`, `refuses`. An unprefixed
  expectation is prose written for a human reviewer; there is no LLM judge in
  this platform, and inventing one that "sort of" agrees with prose would give
  the gate a pass rate nobody could reproduce. Prose is therefore reported as
  `UNSUPPORTED` — counted in the denominator, never in the numerator.
* **"Never run" is not "failed".** `GateState` carries `NOT_EVALUATED`
  separately from `FAILED`, for the same reason ADR-0052 refuses to let a
  stale observation read as `healthy`: an absent verdict is an open question,
  and collapsing it into either answer loses the fact that nobody looked.
* **A result older than what it judged is stale, not evidence.** Same trap,
  same house answer (ADR-0052): staleness is in the model, not in a comment.

Nothing here needs a provider credential. The default runner is the `echo`
adapter, and any callable of the `CaseRunner` shape can be injected, so the
gate is runnable in CI. What the echo runtime cannot do is tell you whether a
real model would have passed — an echo run proves the wiring, not the agent.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .guardrails import GuardrailEngine, validate_shape
from .ids import new_id, now_iso
from .models import Agent
from .spec.model import (
    EvaluationCase,
    GateRequirement,
    GuardrailKind,
    LifecycleStage,
    PromotionGate,
    SystemSpec,
)
from .store import Store

EVALUATION_RUNS = "evaluation_runs"
EVALUATION_RESULTS = "evaluation_results"

#: How much of a response is kept on a result. Enough to see why a case failed,
#: short enough that a run of a hundred cases is still a readable document.
EXCERPT_CHARS = 600


class AssertionKind(str, Enum):
    """What a case may assert. Everything here is deterministic on purpose."""

    EXACT = "exact"              # the whole response, stripped, equals the text
    CONTAINS = "contains"        # the text appears in the response
    NOT_CONTAINS = "not_contains"  # a `must_not` entry does not appear
    SCHEMA = "schema"            # the response parses and matches a contract
    GUARDRAIL = "guardrail"      # a named guardrail trips on the response
    REFUSAL = "refusal"          # the agent declines rather than complies
    #: Prose. Declared, readable, and not checkable here — see the module
    #: docstring. Named rather than silently dropped.
    JUDGE = "judge"


class CaseOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    #: The case asserts something this runner cannot check (prose, or a
    #: contract that is not declared). Not a pass, and not the agent's fault.
    UNSUPPORTED = "unsupported"
    #: The runner itself broke. Also not a pass, and distinct from a failure
    #: so a broken harness cannot be read as a misbehaving agent.
    ERROR = "error"


class GateState(str, Enum):
    """The state of `evaluations_passed` for one agent."""

    #: Nobody has run the cases. An open question, not a verdict.
    NOT_EVALUATED = "not_evaluated"
    PASSED = "passed"
    FAILED = "failed"
    #: A verdict exists but it judged an older spec or agent definition.
    STALE = "stale"


# --------------------------------------------------------------------------
# The runtime under test
# --------------------------------------------------------------------------


@dataclass
class CaseResponse:
    """One agent's answer to one case's `given`.

    `refused` and `guardrails_tripped` are reported by the runner because only
    the runner knows them; a refusal inferred from the text would be a guess.
    """

    text: str = ""
    refused: bool = False
    guardrails_tripped: list[str] = field(default_factory=list)
    error: Optional[str] = None


@runtime_checkable
class CaseRunner(Protocol):
    """Anything that can put a prompt to an agent and return its answer."""

    def __call__(self, agent_id: str, prompt: str) -> CaseResponse: ...


class EchoRunner:
    """The offline default: the `echo` adapter, no model and no credential.

    It proves a case executes end to end and that the assertions work. It
    cannot prove an agent is good, so a run recorded against it says so in
    `runtime` and any reader of the result can discount it.
    """

    runtime = "echo"

    def __init__(self, system_prompt: str = "") -> None:
        self.system_prompt = system_prompt

    def __call__(self, agent_id: str, prompt: str) -> CaseResponse:
        from .runtime.adapters import EchoAdapter

        adapter = EchoAdapter(Agent(name=agent_id), self.system_prompt, {})
        return CaseResponse(text=adapter.run(prompt).text)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


class AssertionOutcome(BaseModel):
    """One assertion's verdict, with both sides of it kept.

    A failure that does not say what was expected and what arrived sends
    somebody back to re-run it by hand, which is where evaluation suites go to
    die.
    """

    kind: AssertionKind
    expected: str
    actual: str = ""
    passed: bool = False
    detail: str = ""


class CaseResult(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evres"))
    run_id: str = ""
    case_id: str
    agent_id: str
    outcome: CaseOutcome
    weight: float = 1.0
    assertions: list[AssertionOutcome] = Field(default_factory=list)
    given: str = ""
    response: str = ""
    reason: str = ""
    evaluated_at: str = Field(default_factory=now_iso)

    @property
    def passed(self) -> bool:
        return self.outcome is CaseOutcome.PASSED


class AgentVerdict(BaseModel):
    """What one run concluded about one agent's gate."""

    agent_id: str
    state: GateState
    to_stage: LifecycleStage
    pass_rate: float = 0.0
    min_pass_rate: float = 1.0
    passed: int = 0
    failed: int = 0
    unsupported: int = 0
    errored: int = 0
    reason: str = ""

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.unsupported + self.errored


class EvaluationRun(BaseModel):
    """One execution of the declared cases, and the evidence it produced."""

    id: str = Field(default_factory=lambda: new_id("evrun"))
    system: str = ""
    #: The declared version, because that is what a human cites in a review.
    spec_version: str = ""
    #: The computed one, because a version somebody forgot to bump is exactly
    #: the case staleness must catch.
    spec_fingerprint: str = ""
    #: Per agent, so editing one agent does not invalidate the whole fleet's
    #: evidence — only that agent's.
    agent_fingerprints: dict[str, str] = Field(default_factory=dict)
    to_stage: LifecycleStage = LifecycleStage.PRODUCTION
    runtime: str = "echo"
    started_at: str = Field(default_factory=now_iso)
    results: list[CaseResult] = Field(default_factory=list)
    verdicts: list[AgentVerdict] = Field(default_factory=list)

    def verdict(self, agent_id: str) -> Optional[AgentVerdict]:
        return next((v for v in self.verdicts if v.agent_id == agent_id), None)


class GateVerdict(BaseModel):
    """`evaluations_passed` for one agent, as of now.

    `required` is separate from `state` because a gate that does not ask for
    evaluations has not passed them either — it simply does not care, and
    saying so is clearer than reporting a pass nobody earned.
    """

    agent_id: str
    to_stage: LifecycleStage
    required: bool
    state: GateState
    reason: str
    run_id: Optional[str] = None
    evaluated_at: Optional[str] = None
    verdict: Optional[AgentVerdict] = None

    @property
    def passed(self) -> bool:
        return self.state is GateState.PASSED


# --------------------------------------------------------------------------
# Fingerprints — what a result was judged against
# --------------------------------------------------------------------------


def _digest(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def spec_fingerprint(spec: SystemSpec) -> str:
    """The lifecycle declaration a run was judged against.

    Deliberately narrow: the cases and the gates, not the whole spec. Renaming
    a channel does not invalidate an evaluation, and a fingerprint that says it
    does trains people to ignore staleness.
    """
    return _digest(spec.lifecycle.model_dump(mode="json"))


def agent_fingerprint(spec: SystemSpec, agent_id: str) -> str:
    """The agent definition a run was judged against, or `""` if it is gone."""
    agent = spec.agent(agent_id)
    return _digest(agent.model_dump(mode="json")) if agent else ""


# --------------------------------------------------------------------------
# Assertions
# --------------------------------------------------------------------------

_PREFIXES = {
    "exact:": AssertionKind.EXACT,
    "contains:": AssertionKind.CONTAINS,
    "schema:": AssertionKind.SCHEMA,
    "guardrail:": AssertionKind.GUARDRAIL,
}


def parse_expectation(expect: str) -> tuple[AssertionKind, str]:
    """Read the assertion out of a declared `expect` string.

    The spec field is free text (ADR-0022) and this module will not change the
    spec to add a discriminator, so the discriminator is a prefix. Anything
    without one is prose for a human and is reported as `JUDGE`.
    """
    text = (expect or "").strip()
    lowered = text.lower()
    for prefix, kind in _PREFIXES.items():
        if lowered.startswith(prefix):
            return kind, text[len(prefix):].strip()
    if lowered in ("refuses", "refuse", "refusal", "refuses:", "refusal:"):
        return AssertionKind.REFUSAL, ""
    if lowered.startswith("refuses:") or lowered.startswith("refusal:"):
        return AssertionKind.REFUSAL, text.split(":", 1)[1].strip()
    return AssertionKind.JUDGE, text


def _excerpt(text: str) -> str:
    return text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS] + "…"


class _Checker:
    """Turns one case plus one response into assertion outcomes."""

    def __init__(self, spec: SystemSpec) -> None:
        self.spec = spec

    def check(
        self, case: EvaluationCase, agent_id: str, response: CaseResponse
    ) -> tuple[list[AssertionOutcome], bool]:
        """Return the assertions and whether any of them was uncheckable."""
        kind, argument = parse_expectation(case.expect)
        outcomes: list[AssertionOutcome] = []
        unsupported = False

        if kind is AssertionKind.JUDGE:
            unsupported = True
            outcomes.append(AssertionOutcome(
                kind=kind, expected=argument, actual=_excerpt(response.text),
                passed=False,
                detail="an LLM-judged expectation is not implemented; this "
                       "case is declared but unverified",
            ))
        elif kind is AssertionKind.EXACT:
            passed = response.text.strip() == argument
            outcomes.append(AssertionOutcome(
                kind=kind, expected=argument, actual=_excerpt(response.text),
                passed=passed,
                detail="" if passed else "the response is not exactly the "
                                         "expected text",
            ))
        elif kind is AssertionKind.CONTAINS:
            passed = argument.lower() in response.text.lower()
            outcomes.append(AssertionOutcome(
                kind=kind, expected=argument, actual=_excerpt(response.text),
                passed=passed,
                detail="" if passed else "the expected text does not appear",
            ))
        elif kind is AssertionKind.SCHEMA:
            outcomes.append(self._schema(argument, response))
            unsupported = outcomes[-1].detail.startswith("no output contract")
        elif kind is AssertionKind.GUARDRAIL:
            outcomes.append(self._guardrail(argument, agent_id, response))
            unsupported = outcomes[-1].detail.startswith("no guardrail")
        elif kind is AssertionKind.REFUSAL:
            passed = response.refused or bool(response.guardrails_tripped)
            outcomes.append(AssertionOutcome(
                kind=kind, expected=argument or "a refusal",
                actual=_excerpt(response.text), passed=passed,
                detail="" if passed else "the agent answered instead of "
                                         "refusing",
            ))

        # `must_not` is literal forbidden text, checked on every case whatever
        # the expectation was: a case that passes its assertion while leaking
        # the thing it was told not to say has not passed.
        for forbidden in case.must_not:
            absent = forbidden.lower() not in response.text.lower()
            outcomes.append(AssertionOutcome(
                kind=AssertionKind.NOT_CONTAINS, expected=forbidden,
                actual=_excerpt(response.text), passed=absent,
                detail="" if absent else "forbidden text appears in the response",
            ))
        return outcomes, unsupported

    def _schema(self, contract_id: str, response: CaseResponse) -> AssertionOutcome:
        contract = next(
            (c for c in self.spec.output_contracts if c.id == contract_id), None
        )
        if contract is None:
            return AssertionOutcome(
                kind=AssertionKind.SCHEMA, expected=contract_id,
                actual=_excerpt(response.text), passed=False,
                detail=f"no output contract '{contract_id}' is declared",
            )
        try:
            value = json.loads(response.text)
        except json.JSONDecodeError as exc:
            return AssertionOutcome(
                kind=AssertionKind.SCHEMA, expected=contract_id,
                actual=_excerpt(response.text), passed=False,
                detail=f"the response is not JSON: {exc}",
            )
        errors = validate_shape(value, contract.schema_)
        errors += [
            f"value.{key}: required field is missing"
            for key in contract.required
            if isinstance(value, dict) and key not in value
        ]
        return AssertionOutcome(
            kind=AssertionKind.SCHEMA, expected=contract_id,
            actual=_excerpt(response.text), passed=not errors,
            detail="; ".join(errors),
        )

    def _guardrail(
        self, guardrail_id: str, agent_id: str, response: CaseResponse
    ) -> AssertionOutcome:
        guardrail = next(
            (g for g in self.spec.guardrails if g.id == guardrail_id), None
        )
        if guardrail is None:
            return AssertionOutcome(
                kind=AssertionKind.GUARDRAIL, expected=guardrail_id,
                actual=_excerpt(response.text), passed=False,
                detail=f"no guardrail '{guardrail_id}' is declared",
            )
        # A runner that already applied the guardrail is believed; otherwise
        # the declared guardrail is run over the text here, which is the same
        # engine the runtime uses.
        if guardrail_id in response.guardrails_tripped:
            return AssertionOutcome(
                kind=AssertionKind.GUARDRAIL, expected=guardrail_id,
                actual=_excerpt(response.text), passed=True,
                detail="the runtime reported this guardrail tripped",
            )
        result = GuardrailEngine([guardrail]).check(
            response.text, GuardrailKind.OUTPUT, context={"agent": agent_id}
        )
        tripped = bool(result.violations)
        return AssertionOutcome(
            kind=AssertionKind.GUARDRAIL, expected=guardrail_id,
            actual=_excerpt(response.text), passed=tripped,
            detail=result.reason() if tripped
            else f"guardrail '{guardrail_id}' did not trip",
        )


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------


def cases_for(spec: SystemSpec, agent_id: str) -> list[EvaluationCase]:
    """The declared cases that apply to one agent — empty `applies_to` is all."""
    return [
        c for c in spec.lifecycle.evaluations
        if not c.applies_to or agent_id in c.applies_to
    ]


def gate_for(spec: SystemSpec, to_stage: LifecycleStage) -> Optional[PromotionGate]:
    return next((g for g in spec.lifecycle.gates if g.to_stage is to_stage), None)


class EvaluationService:
    """Executes declared cases, records the evidence, and answers the gate."""

    def __init__(
        self,
        store: Store,
        runner: Optional[CaseRunner] = None,
        *,
        runtime_name: Optional[str] = None,
    ) -> None:
        self.store = store
        self.runner: CaseRunner = runner or EchoRunner()
        self.runtime_name = runtime_name or getattr(self.runner, "runtime", "injected")

    # -- executing ---------------------------------------------------------

    def run(
        self,
        spec: SystemSpec,
        *,
        agent_ids: Optional[list[str]] = None,
        to_stage: LifecycleStage = LifecycleStage.PRODUCTION,
    ) -> EvaluationRun:
        targets = agent_ids or [a.id for a in spec.agents()]
        gate = gate_for(spec, to_stage)
        min_pass_rate = gate.min_pass_rate if gate else 1.0
        checker = _Checker(spec)

        run = EvaluationRun(
            system=spec.metadata.name,
            spec_version=spec.metadata.version,
            spec_fingerprint=spec_fingerprint(spec),
            agent_fingerprints={a: agent_fingerprint(spec, a) for a in targets},
            to_stage=to_stage,
            runtime=self.runtime_name,
        )

        for agent_id in targets:
            results = [
                self._run_case(checker, case, agent_id, run.id)
                for case in cases_for(spec, agent_id)
            ]
            run.results += results
            run.verdicts.append(
                _verdict(agent_id, results, to_stage, min_pass_rate)
            )

        self.store.put(EVALUATION_RUNS, run, name=run.system)
        for result in run.results:
            self.store.put(EVALUATION_RESULTS, result, parent=run.id)
        return run

    def _run_case(
        self, checker: _Checker, case: EvaluationCase, agent_id: str, run_id: str
    ) -> CaseResult:
        try:
            response = self.runner(agent_id, case.given)
        except Exception as exc:  # a broken harness is not a failing agent
            return CaseResult(
                run_id=run_id, case_id=case.id, agent_id=agent_id,
                outcome=CaseOutcome.ERROR, weight=case.weight, given=case.given,
                reason=f"the runner raised {type(exc).__name__}: {exc}",
            )
        if response.error:
            return CaseResult(
                run_id=run_id, case_id=case.id, agent_id=agent_id,
                outcome=CaseOutcome.ERROR, weight=case.weight, given=case.given,
                response=_excerpt(response.text),
                reason=f"the runtime reported: {response.error}",
            )

        assertions, unsupported = checker.check(case, agent_id, response)
        if unsupported:
            outcome = CaseOutcome.UNSUPPORTED
        elif not assertions:
            # A case with neither an expectation nor a `must_not` asserts
            # nothing; passing it would be the exact dishonesty M3 exists to
            # remove.
            outcome = CaseOutcome.UNSUPPORTED
            assertions = [AssertionOutcome(
                kind=AssertionKind.JUDGE, expected="",
                detail="the case declares nothing to check",
            )]
        elif all(a.passed for a in assertions):
            outcome = CaseOutcome.PASSED
        else:
            outcome = CaseOutcome.FAILED

        failures = [a for a in assertions if not a.passed]
        return CaseResult(
            run_id=run_id, case_id=case.id, agent_id=agent_id, outcome=outcome,
            weight=case.weight, assertions=assertions, given=case.given,
            response=_excerpt(response.text),
            reason="; ".join(
                f"{a.kind.value} expected {a.expected!r}: {a.detail}"
                for a in failures
            ) or "every assertion held",
        )

    # -- reading back ------------------------------------------------------

    def runs(self, system: str, limit: int = 50) -> list[EvaluationRun]:
        """Recorded runs for a system, newest first.

        Sorted on `started_at` rather than the store's `updated_at`, which has
        second granularity: two runs in the same second would otherwise come
        back in an arbitrary order and the gate would read whichever won.
        """
        found = [
            r for r in self.store.list(EVALUATION_RUNS, EvaluationRun, limit=limit)
            if r.system == system
        ]
        return sorted(found, key=lambda r: r.started_at, reverse=True)

    def latest_run(self, system: str) -> Optional[EvaluationRun]:
        return next(iter(self.runs(system)), None)

    def results_for(self, run_id: str) -> list[CaseResult]:
        return self.store.list(EVALUATION_RESULTS, CaseResult, parent=run_id)

    # -- the gate ----------------------------------------------------------

    def gate_state(
        self,
        spec: SystemSpec,
        agent_id: str,
        *,
        to_stage: LifecycleStage = LifecycleStage.PRODUCTION,
    ) -> GateVerdict:
        """What `evaluations_passed` says about this agent, right now."""
        gate = gate_for(spec, to_stage)
        required = bool(
            gate and GateRequirement.EVALUATIONS_PASSED in gate.requires
        )
        run = self.latest_run(spec.metadata.name)
        if run is None or run.verdict(agent_id) is None:
            return GateVerdict(
                agent_id=agent_id, to_stage=to_stage, required=required,
                state=GateState.NOT_EVALUATED,
                reason="no evaluation run has judged this agent",
            )

        verdict = run.verdict(agent_id)
        assert verdict is not None
        stale = _staleness(spec, run, agent_id)
        if stale:
            return GateVerdict(
                agent_id=agent_id, to_stage=to_stage, required=required,
                state=GateState.STALE, reason=stale, run_id=run.id,
                evaluated_at=run.started_at, verdict=verdict,
            )
        return GateVerdict(
            agent_id=agent_id, to_stage=to_stage, required=required,
            state=verdict.state, reason=verdict.reason, run_id=run.id,
            evaluated_at=run.started_at, verdict=verdict,
        )

    def gate_states(
        self,
        spec: SystemSpec,
        *,
        to_stage: LifecycleStage = LifecycleStage.PRODUCTION,
    ) -> dict[str, GateVerdict]:
        return {
            a.id: self.gate_state(spec, a.id, to_stage=to_stage)
            for a in spec.agents()
        }


def _verdict(
    agent_id: str,
    results: list[CaseResult],
    to_stage: LifecycleStage,
    min_pass_rate: float,
) -> AgentVerdict:
    """Weighted pass rate, with everything unproven in the denominator."""
    passed = [r for r in results if r.outcome is CaseOutcome.PASSED]
    failed = [r for r in results if r.outcome is CaseOutcome.FAILED]
    unsupported = [r for r in results if r.outcome is CaseOutcome.UNSUPPORTED]
    errored = [r for r in results if r.outcome is CaseOutcome.ERROR]

    if not results:
        # No case applies to this agent, so no evidence exists. That is an
        # unanswered question, not a pass — a gate that passes on an empty
        # suite is the promise M3 is replacing.
        return AgentVerdict(
            agent_id=agent_id, state=GateState.NOT_EVALUATED, to_stage=to_stage,
            min_pass_rate=min_pass_rate,
            reason="no declared evaluation case applies to this agent",
        )

    total_weight = sum(r.weight for r in results) or 1.0
    rate = sum(r.weight for r in passed) / total_weight
    state = GateState.PASSED if rate >= min_pass_rate else GateState.FAILED
    detail = []
    if failed:
        detail.append(f"{len(failed)} failed: " + ", ".join(
            f"{r.case_id} ({r.reason})" for r in failed))
    if unsupported:
        detail.append(
            f"{len(unsupported)} unverifiable: "
            + ", ".join(r.case_id for r in unsupported)
        )
    if errored:
        detail.append(f"{len(errored)} errored: " + ", ".join(
            r.case_id for r in errored))
    reason = (
        f"pass rate {rate:.0%} against a required {min_pass_rate:.0%}"
        + ("; " + "; ".join(detail) if detail else "")
    )
    return AgentVerdict(
        agent_id=agent_id, state=state, to_stage=to_stage, pass_rate=rate,
        min_pass_rate=min_pass_rate, passed=len(passed), failed=len(failed),
        unsupported=len(unsupported), errored=len(errored), reason=reason,
    )


def _staleness(spec: SystemSpec, run: EvaluationRun, agent_id: str) -> str:
    """Why this run no longer describes the thing it judged, or `""`.

    ADR-0052 settled the house position on a belief that has outlived its
    subject: report it as unknown, never as health. An evaluation result is the
    same shape of belief, so the rule here is the same — a result is evidence
    only about the definition it ran against.

    Two triggers, because two different mistakes happen. The declared
    `metadata.version` is what a reviewer cites, so a bump invalidates the
    evidence even if nothing material changed; the fingerprints catch the more
    common case, an edit shipped without a version bump. Either one alone would
    leave a hole.
    """
    current_agent = agent_fingerprint(spec, agent_id)
    if not current_agent:
        return f"agent '{agent_id}' no longer exists in the spec"
    if run.spec_version != spec.metadata.version:
        return (
            f"ran against spec version {run.spec_version or 'unrecorded'}; "
            f"the spec is now {spec.metadata.version}"
        )
    if run.spec_fingerprint != spec_fingerprint(spec):
        return (
            "the lifecycle declaration changed since this run, without a "
            "version bump"
        )
    if run.agent_fingerprints.get(agent_id) != current_agent:
        return (
            f"agent '{agent_id}' was edited since this run, without a version "
            "bump"
        )
    return ""
