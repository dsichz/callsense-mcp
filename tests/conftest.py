import shutil
from pathlib import Path

import pytest

from callsense_mcp.service import CallSense
from callsense_mcp.store import Store

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_paid_api(monkeypatch):
    """Tests never reach the Anthropic API, even on a machine that has a key exported."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SCORER_MODEL", raising=False)


@pytest.fixture
def home(tmp_path):
    """A throwaway copy of the shipped store, so tests never write into data/scores of the repo."""
    dst = tmp_path / "store"
    shutil.copytree(REPO / "rules", dst / "rules")
    shutil.copytree(REPO / "data", dst / "data")
    return dst


@pytest.fixture
def app(home):
    return CallSense(Store(home))
