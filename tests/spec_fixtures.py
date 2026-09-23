"""A minimal valid spec, shared by the tests that need one to bend.

Imported as a plain module (`from spec_fixtures import ...`) rather than as
`tests.spec_fixtures`. Several tests used the package form and passed locally
under `python -m pytest`, which puts the working directory on `sys.path`, and
failed in CI under a bare `pytest`, which does not. Since `tests/` holds no
`__init__.py`, pytest puts *that* directory on the path and the plain form
works either way.
"""
from __future__ import annotations

from orgagents.spec.model import SystemSpec

#: Two decision classes, a finance team, and nothing else. Small on purpose:
#: a fixture that carries more than the rule under test invites the rule to be
#: proved by something incidental.
BASE = {
    "metadata": {"name": "t", "spec_version": "1.1.0", "version": "0.1.0"},
    "decisions": [{"id": "spend"}, {"id": "deploy"}, {"id": "close"}],
    "organization": {
        "id": "root",
        "name": "Root",
        "mandate": {"decisions": ["spend", "deploy", "close"]},
        "members": [{"id": "ceo", "name": "CEO"}],
        "teams": [
            {
                "id": "fin",
                "name": "Finance",
                "leader": "cfo",
                "mandate": {"decisions": ["spend", "close"],
                            "conditions": {"max_value": 250}},
                "members": [
                    {"id": "cfo", "name": "CFO"},
                    {"id": "clerk", "name": "Clerk",
                     "mandate": {"decisions": ["close"]}},
                ],
            }
        ],
    },
}


def org(spec_dict) -> SystemSpec:
    """Validate a spec dict into the model."""
    return SystemSpec.model_validate(spec_dict)


#: `BASE` plus a separation of duties over the two decisions the finance team
#: holds. The pairing that makes the rules about separation testable at all.
SEPARATED = {
    **BASE,
    "separations": [
        {"id": "payment_control", "decisions": ["spend", "close"],
         "reason": "one principal must not do both"},
    ],
}
