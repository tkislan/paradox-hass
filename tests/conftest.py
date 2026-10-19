from typing import TYPE_CHECKING

import pytest

from tests.fake_panel import FakePanel, FakePanelOptions
from tests.spec_tables import load_spec

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pytest_aiohttp.plugin import AiohttpServer

GOLDEN_NAME = "sample session, 4 digit code"


@pytest.fixture
def golden() -> dict[str, str]:
    rows: list[dict[str, str]] = [
        row for row in load_spec("crypto")["credentials"] if row["name"] == GOLDEN_NAME
    ]
    assert len(rows) == 1, f"crypto.json has no credentials named {GOLDEN_NAME!r}"
    return rows[0]


@pytest.fixture
async def fake_panel(
    aiohttp_server: AiohttpServer, socket_enabled: None, golden: dict[str, str]
) -> AsyncIterator[FakePanel]:
    panel = FakePanel(
        FakePanelOptions(
            session_value=golden["session"],
            credentials={golden["session"]: (golden["u"], golden["p"])},
        )
    )
    server = await aiohttp_server(panel.app())
    panel.host = f"{server.host}:{server.port}"
    yield panel
    panel.release()
