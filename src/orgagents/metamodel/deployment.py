"""The Deployment profile (ADR-0112 §4): the binding — how a design is
realised on a target — modelled as UML deployment.

It imports every spec profile and none imports it, which is how the spec's
neutrality (ADR-0004) is kept: nothing in the spec profiles can name a
server, a runtime or a cloud. It is not part of the assembled spec
`PROFILE`; `metamodel.PROFILES` lists it last.
"""
from __future__ import annotations

from .uml import Profile

PROFILE = Profile(
    name="Deployment",
    imports=("Core", "Organisation", "Authority", "Access", "Data",
             "Knowledge", "Process", "Assurance"),
    doc="The binding: targets, servers, runtimes and one binding per spec "
        "concern.",
)
