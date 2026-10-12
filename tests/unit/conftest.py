from __future__ import annotations

import pytest

from .support import Clock, Transport


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    found = Clock()
    found.install(monkeypatch)
    return found


@pytest.fixture
def transport() -> Transport:
    return Transport()


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)
