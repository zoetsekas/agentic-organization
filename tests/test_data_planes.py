import pytest

from orgagents.data.planes import AccessDenied
from orgagents.models import Visibility


def test_private_is_isolated(platform):
    cfo = platform.org.agent("agt_cfo")
    analyst = platform.org.agent("agt_fin_analyst")
    platform.planes.write(cfo, "notes", "close-plan", {"step": 1})
    assert platform.planes.read(cfo, "notes", "close-plan").value == {"step": 1}
    assert platform.planes.read(analyst, "notes", "close-plan") is None


def test_public_is_readable_by_all(platform):
    eng = platform.org.agent("agt_platform_eng")
    analyst = platform.org.agent("agt_fin_analyst")
    platform.planes.contribute_public(eng, "kb", "deploy-runbook", "ship on green")
    assert platform.planes.read(analyst, "kb", "deploy-runbook").value == "ship on green"


def test_protected_requires_shared_group(platform):
    cfo = platform.org.agent("agt_cfo")
    analyst = platform.org.agent("agt_fin_analyst")
    eng = platform.org.agent("agt_platform_eng")
    platform.planes.write(cfo, "forecast", "q3", {"rev": 1},
                          visibility=Visibility.PROTECTED, groups=["finance"])
    assert platform.planes.read(analyst, "forecast", "q3").value == {"rev": 1}
    with pytest.raises(AccessDenied):
        platform.planes.read(eng, "forecast", "q3")


def test_cannot_publish_outside_own_groups(platform):
    eng = platform.org.agent("agt_platform_eng")
    with pytest.raises(AccessDenied):
        platform.planes.write(eng, "forecast", "q4", {},
                              visibility=Visibility.PROTECTED, groups=["finance"])
