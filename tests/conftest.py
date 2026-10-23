import select
import socket
from typing import TYPE_CHECKING

import pytest

from tests.fake_panel import FakePanel, FakePanelOptions
from tests.spec_tables import load_spec

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

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


@pytest.fixture
def unopened_host(socket_enabled: None) -> Iterator[str]:
    """
    Give a ``host:port`` that a connection never opens to.

    Linux drops the SYN of a new connection to a listening socket whose accept queue
    is full, and a backlog of 0 holds one connection that nobody accepts. The panel
    does the same with a source port it saw a minute earlier.
    """
    with socket.socket() as server, socket.socket() as filler:
        server.bind(("127.0.0.1", 0))
        server.listen(0)
        filler.settimeout(0)
        filler.connect_ex(server.getsockname())
        with socket.socket() as probe:
            probe.settimeout(0)
            probe.connect_ex(server.getsockname())
            if select.select([], [probe], [], 0.05)[1]:
                pytest.fail(
                    "unopened_host: the kernel accepted a connection past the backlog"
                )
        yield f"127.0.0.1:{server.getsockname()[1]}"
