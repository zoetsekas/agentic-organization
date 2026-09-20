"""Evaluation runner: cases execute, and the gate reflects a real run.

WS-014 M3 / ADR-0060. Everything here runs offline: the default runner is the
`echo` adapter and every other runner is a local stub, so a missing provider
credential can never be the reason this suite is skipped.
"""
import pytest

from orgagents.evaluations import (
    AssertionKind,
    CaseOutcome,
    CaseResponse,
    EchoRunner,
    EvaluationService,
    GateState,
    parse_expectation,
    spec_fingerprint,
)
from orgagents.spec.model import (
    AgentSpec,
    EvaluationCase,
    GateRequirement,
    Guardrail,
    GuardrailCheck,
    GuardrailKind,
    LifecycleStage,
    Metadata,
    OutputContract,
    PromotionGate,
    SystemSpec,
    Team,
)
from orgagents.store import Store


def make_spec(cases, *, min_pass_rate=1.0, version="0.1.0", **kwargs):
    """A two-agent system with one production gate on `evaluations_passed`."""
    spec = SystemSpec(metadata=Metadata(name="evals", version=version))
    spec.organization = Team(
        id="root", name="root",
        members=[AgentSpec(id="analyst", name="Analyst"),
                 AgentSpec(id="sre", name="SRE")],
    )
    spec.lifecycle.evaluations = cases
    spec.lifecycle.gates = [
        PromotionGate(
            to_stage=LifecycleStage.PRODUCTION,
            requires=[GateRequirement.EVALUATIONS_PASSED],
            min_pass_rate=min_pass_rate,
        )
    ]
    for key, value in kwargs.items():
        setattr(spec, key, value)
    return spec


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "evals.db")


def fixed_runner(text, **kwargs):
    def run(agent_id, prompt):
        return CaseResponse(text=text, **kwargs)

    return run


# -- what a case may assert ------------------------------------------------


@pytest.mark.parametrize(
    "expect,kind",
    [
        ("exact: hello", AssertionKind.EXACT),
        ("contains: warehouse", AssertionKind.CONTAINS),
        ("schema: findings", AssertionKind.SCHEMA),
        ("guardrail: no_pii", AssertionKind.GUARDRAIL),
        ("refuses", AssertionKind.REFUSAL),
        ("An answer naming the tables used.", AssertionKind.JUDGE),
    ],
)
def test_the_assertion_is_read_from_the_declared_expectation(expect, kind):
    assert parse_expectation(expect)[0] is kind


def test_a_contains_case_executes_and_passes(store):
    spec = make_spec([EvaluationCase(id="c", given="hi", expect="contains: ok")])
    run = EvaluationService(store, fixed_runner("everything is ok here")).run(spec)

    result = next(r for r in run.results if r.agent_id == "analyst")
    assert result.outcome is CaseOutcome.PASSED
    assert run.verdict("analyst").state is GateState.PASSED
    assert run.verdict("analyst").pass_rate == 1.0


def test_an_exact_case_fails_on_a_near_miss(store):
    spec = make_spec([EvaluationCase(id="c", given="hi", expect="exact: yes")])
    run = EvaluationService(store, fixed_runner("yes, definitely")).run(spec)
    assert run.results[0].outcome is CaseOutcome.FAILED


def test_a_schema_case_checks_the_declared_output_contract(store):
    contract = OutputContract(
        id="findings",
        schema={"type": "object", "properties": {"source": {"type": "string"}},
                "required": ["source"]},
    )
    spec = make_spec(
        [EvaluationCase(id="c", given="hi", expect="schema: findings")],
        output_contracts=[contract],
    )
    service = EvaluationService(store, fixed_runner('{"source": "warehouse"}'))
    assert service.run(spec).results[0].outcome is CaseOutcome.PASSED

    bad = EvaluationService(store, fixed_runner('{"note": "no source"}')).run(spec)
    assert bad.results[0].outcome is CaseOutcome.FAILED
    assert "required field is missing" in bad.results[0].reason


def test_a_refusal_case_passes_only_when_the_agent_refuses(store):
    spec = make_spec([EvaluationCase(id="c", given="export pii", expect="refuses")])
    answered = EvaluationService(store, fixed_runner("here is the list")).run(spec)
    assert answered.results[0].outcome is CaseOutcome.FAILED

    refused = EvaluationService(
        store, fixed_runner("I cannot do that", refused=True)
    ).run(spec)
    assert refused.results[0].outcome is CaseOutcome.PASSED


def test_a_guardrail_case_passes_when_the_named_guardrail_trips(store):
    guardrail = Guardrail(
        id="no_secrets", applies_to=[GuardrailKind.OUTPUT],
        checks=[GuardrailCheck.SECRETS],
    )
    spec = make_spec(
        [EvaluationCase(id="c", given="leak", expect="guardrail: no_secrets")],
        guardrails=[guardrail],
    )
    leaked = EvaluationService(
        store, fixed_runner("token: sk-abcdefghijklmnopqrstuvwxyz0123456789")
    ).run(spec)
    assert leaked.results[0].outcome is CaseOutcome.PASSED

    clean = EvaluationService(store, fixed_runner("nothing sensitive")).run(spec)
    assert clean.results[0].outcome is CaseOutcome.FAILED


def test_must_not_fails_a_case_that_otherwise_passes(store):
    spec = make_spec([
        EvaluationCase(id="c", given="hi", expect="contains: ok",
                       must_not=["national_id"]),
    ])
    run = EvaluationService(store, fixed_runner("ok: national_id=7")).run(spec)
    assert run.results[0].outcome is CaseOutcome.FAILED
    assert "forbidden text" in run.results[0].reason


# -- honesty about what is not implemented ---------------------------------


def test_a_prose_expectation_is_unsupported_rather_than_judged(store):
    spec = make_spec([
        EvaluationCase(id="c", given="hi", expect="An answer citing its sources."),
    ])
    run = EvaluationService(store, fixed_runner("an answer citing its sources.")).run(spec)

    result = run.results[0]
    assert result.outcome is CaseOutcome.UNSUPPORTED
    assert "not implemented" in result.assertions[0].detail
    # Unverifiable is not passing — but it is not failing either. Nothing was
    # checkable, so nothing was checked: reporting FAILED would blame the agent
    # for the suite being unwritten, and send a reviewer chasing a failure that
    # does not exist. The gate must simply not read as met.
    verdict = run.verdict("analyst")
    assert verdict.state is GateState.NOT_EVALUATED
    assert verdict.state is not GateState.PASSED
    assert verdict.unsupported == 1


def test_a_case_that_asserts_nothing_is_unsupported(store):
    spec = make_spec([EvaluationCase(id="empty", given="hi")])
    run = EvaluationService(store, fixed_runner("anything")).run(spec)
    assert run.results[0].outcome is CaseOutcome.UNSUPPORTED


def test_a_broken_runner_errors_rather_than_failing_the_agent(store):
    def explode(agent_id, prompt):
        raise RuntimeError("no model configured")

    spec = make_spec([EvaluationCase(id="c", given="hi", expect="contains: ok")])
    run = EvaluationService(store, explode).run(spec)
    assert run.results[0].outcome is CaseOutcome.ERROR
    assert "no model configured" in run.results[0].reason
    assert run.verdict("analyst").errored == 1


# -- the gate --------------------------------------------------------------


def test_a_never_run_gate_is_distinct_from_a_failed_one(store):
    spec = make_spec([EvaluationCase(id="c", given="hi", expect="contains: ok")])
    service = EvaluationService(store, fixed_runner("nope"))

    before = service.gate_state(spec, "analyst")
    assert before.state is GateState.NOT_EVALUATED
    assert before.passed is False
    assert before.required is True
    assert before.run_id is None

    service.run(spec)
    after = service.gate_state(spec, "analyst")
    assert after.state is GateState.FAILED
    assert after.run_id is not None
    assert after.state is not before.state


def test_a_failing_case_fails_the_gate_and_a_passing_one_passes_it(store):
    spec = make_spec([EvaluationCase(id="c", given="hi", expect="contains: ok")])
    EvaluationService(store, fixed_runner("not fine")).run(spec)
    assert EvaluationService(store).gate_state(spec, "analyst").state is GateState.FAILED

    EvaluationService(store, fixed_runner("ok")).run(spec)
    assert EvaluationService(store).gate_state(spec, "analyst").state is GateState.PASSED


def test_an_agent_with_no_applicable_case_is_never_evaluated_not_passed(store):
    spec = make_spec([
        EvaluationCase(id="c", given="hi", expect="contains: ok", applies_to=["analyst"]),
    ])
    run = EvaluationService(store, fixed_runner("ok")).run(spec)
    assert run.verdict("analyst").state is GateState.PASSED
    assert run.verdict("sre").state is GateState.NOT_EVALUATED


def test_the_min_pass_rate_of_the_gate_decides(store):
    cases = [
        EvaluationCase(id="a", given="hi", expect="contains: ok"),
        EvaluationCase(id="b", given="hi", expect="contains: absent"),
    ]
    spec = make_spec(cases, min_pass_rate=0.5)
    run = EvaluationService(store, fixed_runner("ok")).run(spec)
    assert run.verdict("analyst").pass_rate == pytest.approx(0.5)
    assert run.verdict("analyst").state is GateState.PASSED


# -- recorded evidence -----------------------------------------------------


def test_results_record_expected_actual_and_which_assertion_failed(store):
    spec = make_spec([EvaluationCase(id="c", given="what happened?",
                                     expect="contains: warehouse")])
    run = EvaluationService(store, fixed_runner("I made it up")).run(spec)

    stored = EvaluationService(store).results_for(run.id)
    result = next(r for r in stored if r.agent_id == "analyst")
    assertion = result.assertions[0]
    assert assertion.kind is AssertionKind.CONTAINS
    assert assertion.expected == "warehouse"
    assert assertion.actual == "I made it up"
    assert result.given == "what happened?"
    assert "does not appear" in result.reason
    # The run itself is recorded too, not only its cases.
    assert EvaluationService(store).latest_run("evals").id == run.id


# -- staleness (ADR-0052's rule, applied to evidence) ----------------------


def test_a_result_against_an_older_spec_version_is_stale_not_passed(store):
    spec = make_spec([EvaluationCase(id="c", given="hi", expect="contains: ok")])
    service = EvaluationService(store, fixed_runner("ok"))
    service.run(spec)
    assert service.gate_state(spec, "analyst").state is GateState.PASSED

    spec.metadata.version = "0.2.0"
    stale = service.gate_state(spec, "analyst")
    assert stale.state is GateState.STALE
    assert stale.passed is False
    assert "0.1.0" in stale.reason


def test_an_edited_agent_without_a_version_bump_is_still_stale(store):
    spec = make_spec([EvaluationCase(id="c", given="hi", expect="contains: ok")])
    service = EvaluationService(store, fixed_runner("ok"))
    service.run(spec)

    spec.agent("analyst").description = "now does something else"
    verdict = service.gate_state(spec, "analyst")
    assert verdict.state is GateState.STALE
    assert "without a version bump" in verdict.reason
    # The other agent's evidence is untouched: staleness is per definition,
    # so editing one agent does not invalidate the whole fleet's run.
    assert service.gate_state(spec, "sre").state is GateState.PASSED


def test_editing_a_case_makes_the_evidence_stale(store):
    spec = make_spec([EvaluationCase(id="c", given="hi", expect="contains: ok")])
    service = EvaluationService(store, fixed_runner("ok"))
    before = spec_fingerprint(spec)
    service.run(spec)

    spec.lifecycle.evaluations[0].expect = "contains: something else"
    assert spec_fingerprint(spec) != before
    assert service.gate_state(spec, "analyst").state is GateState.STALE


# -- offline ---------------------------------------------------------------


def test_the_default_runner_needs_no_credentials(store, monkeypatch):
    """The echo adapter makes no provider call, so CI can run the gate."""
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    spec = make_spec([
        EvaluationCase(id="c", given="ping", expect="contains: acknowledged"),
    ])
    service = EvaluationService(store)
    run = service.run(spec)

    assert run.runtime == "echo"
    assert isinstance(service.runner, EchoRunner)
    assert run.results[0].outcome is CaseOutcome.PASSED
    assert "ping" in run.results[0].response


def test_a_real_failure_still_reads_as_failed_alongside_unverifiable_ones(store):
    # The correction above must not swallow genuine failures: a suite with one
    # checkable case that fails is a failure, whatever else it contains.
    spec = make_spec([
        EvaluationCase(id="prose", given="hi", expect="Something a human judges."),
        EvaluationCase(id="checkable", given="hi", expect="exact:the right answer"),
    ])
    run = EvaluationService(store, fixed_runner("the wrong answer")).run(spec)
    verdict = run.verdict("analyst")
    assert verdict.state is GateState.FAILED
    assert verdict.unsupported == 1
