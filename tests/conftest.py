import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orgagents.platform import Platform  # noqa: E402
from orgagents.seed import seed  # noqa: E402


@pytest.fixture()
def platform(tmp_path, monkeypatch) -> Platform:
    monkeypatch.chdir(tmp_path)
    os.environ.pop("WAREHOUSE_DSN", None)
    return seed(str(tmp_path / "test.db"))


@pytest.fixture()
def blank(tmp_path) -> Platform:
    return Platform(str(tmp_path / "blank.db"), configure_logs=False)


# A real PostgreSQL, started in Docker when a test asks for one (ADR-0113).
from pg import (empty_postgres_url, postgres_server,  # noqa: E402,F401
                postgres_template, postgres_url)
