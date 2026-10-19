from typing import TYPE_CHECKING

import aiohttp
import pytest

from custom_components.paradox.panel import client as client_module
from custom_components.paradox.panel.client import ParadoxClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests.fake_panel import FakePanel


@pytest.fixture
def fast_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the client's deadline and index retry wait at 1/20 of production."""
    monkeypatch.setattr(client_module, "REQUEST_TIMEOUT", 1.0)
    monkeypatch.setattr(client_module, "INDEX_RETRY_WAIT", 0.05)


@pytest.fixture
async def client(
    fake_panel: FakePanel, golden: dict[str, str]
) -> AsyncIterator[ParadoxClient]:
    async with aiohttp.ClientSession() as session:
        yield ParadoxClient(
            session, fake_panel.host, golden["username"], golden["password"]
        )
