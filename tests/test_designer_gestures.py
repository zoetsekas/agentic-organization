"""The designer, specified as gestures on the model (ADR-0103).

Every gesture is one model operation; these tests hold the designer to that.
The canvas's side — that a drawn edge sends this request and shows the answer
— is held by scripts/interaction_check.py in a real browser.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from orgagents.api import create_app
from orgagents.designer.gestures import _field, catalogue, evaluate, gestures
from orgagents.metamodel import NON_PALETTE, PROFILE, RelKind, Shape, _concrete
from orgagents.metamodel.instances import collect
from orgagents.metamodel.scenarios import base
from orgagents.spec.loader import dump_spec

ROOT = Path(__file__).resolve().parents[1]
A = {"X-User": "ana"}


def _draft():
    return yaml.safe_load(dump_spec(base()))


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "d.db")))


def test_every_drawable_relationship_has_a_gesture():
    """A relationship the model has and the canvas cannot make is a model
    the designer does not show."""
    ids = {g.id for g in gestures()}
    for r in _concrete(PROFILE):
        if not r.linkable or r.source in NON_PALETTE or \
                r.target in NON_PALETTE:
            continue
        if r.shape is Shape.PART:
            assert f"compose:{r.source}-{r.target}" in ids
        elif r.kind is RelKind.DEPLOYMENT:
            assert {f"deploy:{r.source}", f"undeploy:{r.source}"} <= ids
        else:
            assert f"draw:{r.source}-{_field(r)}-{r.target}" in ids


def test_every_palette_kind_can_be_created():
    ids = {g.id for g in gestures()}
    for st in PROFILE.stereotypes:
        if st.kind not in NON_PALETTE and st.kind != "note":
            assert f"create:{st.kind}" in ids, st.kind


@pytest.mark.parametrize(
    "gesture", [g for g in gestures() if g.id.startswith(("draw:", "compose:",
                                                          "deploy:"))],
    ids=lambda g: g.id)
def test_each_drawing_gesture_is_answered_by_the_model(gesture):
    """Played on the base organisation wherever it has one of each end: the
    model accepts or refuses, and never fails to understand the request."""
    model = collect(base())
    req = gesture.request
    src = next(iter(model.of(req["source"]["kind"])), None)
    tgt = next((i for i in model.of(req["target"]["kind"])
                if src is None or i.id != src.id), None)
    if src is None or tgt is None:
        pytest.skip("the base has no instance of one end")
    request = {**req, "source": {"kind": src.kind, "id": src.id},
               "target": {"kind": tgt.kind, "id": tgt.id}}
    if "attrs" in request:
        rel = next(r for r in PROFILE.relationships
                   if r.stereotype == req["relationship"] and r.choices)
        request["attrs"] = {"kind": rel.choices[-1]}
    answer = evaluate(_draft(), request)
    assert isinstance(answer["accepted"], bool)
    if not answer["accepted"]:
        assert answer["violations"], "a refusal says why"


# -- the route the canvas calls ----------------------------------------------

def _op(client, request, spec=None):
    return client.post("/api/designer/operations", headers=A,
                       json={"spec": spec or _draft(), "request": request})


def test_knowledge_linked_to_two_agents(client):
    """The request that started ADR-0101: one knowledge source, associated
    with more than one agent."""
    draft = _draft()
    for agent in ("analyst", "risk_lead"):
        answer = _op(client, {"op": "link",
                              "source": {"kind": "agent", "id": agent},
                              "target": {"kind": "knowledge", "id": "handbook"},
                              "relationship": "knowledge"}, draft).json()
        assert answer["accepted"], answer
        draft = answer["spec"]
    holders = {m["id"] for t in _teams(draft["organization"])
               for m in t.get("members", []) if "handbook" in
               m.get("knowledge", [])}
    assert holders == {"analyst", "risk_lead"}
    assert len(draft["organization"]["knowledge"]) == 1


def test_dropping_an_agent_into_an_environment_deploys_it(client):
    deployed = _op(client, {"op": "link",
                            "source": {"kind": "agent", "id": "risk_lead"},
                            "target": {"kind": "environment", "id": "secure"},
                            "relationship": "environments"}).json()
    assert deployed["accepted"]
    risk = _agent(deployed["spec"], "risk_lead")
    assert [e["environment"] for e in risk["environments"]] == ["secure"]
    back = _op(client, {"op": "unlink",
                        "source": {"kind": "agent", "id": "risk_lead"},
                        "target": {"kind": "environment", "id": "secure"},
                        "relationship": "environments"},
               deployed["spec"]).json()
    assert back["accepted"]
    assert not _agent(back["spec"], "risk_lead").get("environments")


def test_dragging_an_agent_onto_a_team_moves_it(client):
    answer = _op(client, {"op": "link", "source": {"kind": "team", "id": "risk"},
                          "target": {"kind": "agent", "id": "analyst"},
                          "relationship": "members"}).json()
    assert answer["accepted"]
    risk = next(t for t in _teams(answer["spec"]["organization"])
                if t["id"] == "risk")
    assert "analyst" in {m["id"] for m in risk["members"]}


def test_a_refusal_is_an_answer_not_an_error(client):
    res = _op(client, {"op": "link", "source": {"kind": "team", "id": "ap"},
                       "target": {"kind": "team", "id": "ops"},
                       "relationship": "unit_links",
                       "attrs": {"kind": "oversees"}})
    assert res.status_code == 200
    body = res.json()
    assert body["accepted"] is False and "spec" not in body
    assert body["violations"][0]["constraint"] == \
        "unit_links_respect_containment"


def test_deleting_says_what_else_went(client):
    body = _op(client, {"op": "delete", "kind": "capability",
                        "id": "pay_supplier"}).json()
    assert body["accepted"]
    assert any("payer" in e for e in body["effects"])


def test_a_request_the_model_cannot_read_is_422(client):
    assert _op(client, {"op": "teleport"}).status_code == 422
    assert _op(client, {"op": "link",
                        "source": {"kind": "team", "id": "ops"},
                        "target": {"kind": "agent", "id": "analyst"}}
               ).status_code == 422          # member or leads? It must say.


def test_the_specification_is_served_and_current(client):
    served = client.get("/api/designer/gestures").json()["gestures"]
    assert len(served) == len(gestures())
    assert (ROOT / "docs" / "designer" / "gestures.md").read_text() == \
        catalogue()


def _teams(team):
    yield team
    for child in team.get("teams", []) or []:
        yield from _teams(child)


def _agent(spec, agent_id):
    return next(m for t in _teams(spec["organization"])
                for m in t.get("members", []) if m["id"] == agent_id)
