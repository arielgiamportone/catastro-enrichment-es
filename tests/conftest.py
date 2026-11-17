import urllib.request

import pytest

from catastro_enrich.synthetic import ScriptedTransport
from catastro_enrich.transport import Pacer


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Tests must never reach the public services: fail loudly if they try."""

    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted during tests")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


class FakeClock:
    """Manual clock so pacing can be asserted without sleeping."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def no_wait() -> Pacer:
    """Pacer that never sleeps (delay 0)."""
    return Pacer(0.0, sleep=lambda _s: None)


@pytest.fixture
def transport() -> ScriptedTransport:
    return ScriptedTransport()
