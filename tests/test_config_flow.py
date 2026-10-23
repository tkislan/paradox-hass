from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.paradox.const import DOMAIN, LOGGER
from custom_components.paradox.panel.client import ParadoxClient
from tests.fake_panel import FakeResponse
from tests.logs import problem_records

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any

    from homeassistant.config_entries import ConfigFlowResult
    from homeassistant.core import HomeAssistant

    from tests.fake_panel import FakePanel, PanelResult


@pytest.fixture
def user_input(panel: FakePanel) -> dict[str, str]:
    return {"host": panel.host, "username": "user", "password": "1234"}


@pytest.fixture
def entry_setup() -> Iterator[None]:
    """Keep a created or reloaded entry from logging in to the panel."""
    with patch("custom_components.paradox.async_setup_entry", return_value=True):
        yield


@pytest.fixture
def stale_entry(hass: HomeAssistant, panel: FakePanel) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Paradox",
        data={"host": panel.host, "username": "old", "password": "9999"},
    )
    entry.add_to_hass(hass)
    return entry


async def start_user_flow(hass: HomeAssistant) -> ConfigFlowResult:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def submit(
    hass: HomeAssistant, result: ConfigFlowResult, user_input: dict[str, Any]
) -> ConfigFlowResult:
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


def suggested_values(result: ConfigFlowResult) -> dict[str, Any]:
    return {
        str(key.schema): key.description["suggested_value"]
        for key in result["data_schema"].schema
        if key.description
    }


@pytest.mark.usefixtures("entry_setup")
async def test_user_step_creates_entry_and_logs_the_session_out(
    hass: HomeAssistant, panel: FakePanel, user_input: dict[str, str]
) -> None:
    result = await submit(hass, await start_user_flow(hass), user_input)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Paradox"
    assert result["data"] == {
        "host": panel.host,
        "username": "user",
        "password": "1234",
    }
    assert len(panel.requests_to("/statuslive.html")) == 1
    assert panel.request_lines[-1] == "GET /logout.html"


@pytest.mark.usefixtures("entry_setup")
async def test_user_step_strips_the_host_before_validating_and_storing_it(
    hass: HomeAssistant, panel: FakePanel, user_input: dict[str, str]
) -> None:
    padded = {**user_input, "host": f"  {panel.host} "}

    result = await submit(hass, await start_user_flow(hass), padded)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["host"] == panel.host


@pytest.mark.usefixtures("entry_setup")
@pytest.mark.parametrize(
    ("error", "bad_input"),
    [
        pytest.param("invalid_auth", {"password": "0000"}, id="rejected-login"),
        # Nothing listens on tcpmux, so the connection is refused at once.
        pytest.param("cannot_connect", {"host": "127.0.0.1:1"}, id="refused-port"),
    ],
)
async def test_user_step_shows_error_for_input_the_panel_cannot_accept(
    hass: HomeAssistant,
    user_input: dict[str, str],
    error: str,
    bad_input: dict[str, str],
) -> None:
    typed = {**user_input, **bad_input}

    result = await submit(hass, await start_user_flow(hass), typed)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}
    suggested = suggested_values(result)
    assert (suggested["host"], suggested["username"]) == (
        typed["host"],
        typed["username"],
    )

    result = await submit(hass, result, user_input)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == user_input


@pytest.mark.usefixtures("entry_setup")
@pytest.mark.parametrize(
    ("error", "path", "fault"),
    [
        pytest.param(
            "cannot_connect",
            "/login_page.html",
            FakeResponse(status=404),
            id="404-on-login-page",
        ),
        pytest.param(
            "cannot_connect",
            "/statuslive.html",
            FakeResponse(status=500),
            id="500-on-status-page",
        ),
        pytest.param(
            "unsupported_firmware",
            "/statuslive.html",
            FakeResponse(status=404),
            id="404-on-status-page",
        ),
    ],
)
async def test_user_step_shows_error_for_a_panel_that_fails(
    hass: HomeAssistant,
    panel: FakePanel,
    *,
    user_input: dict[str, str],
    error: str,
    path: str,
    fault: PanelResult,
) -> None:
    panel.respond_with(path, fault)

    result = await submit(hass, await start_user_flow(hass), user_input)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}
    suggested = suggested_values(result)
    assert (suggested["host"], suggested["username"]) == (panel.host, "user")
    assert not panel.logged_in

    panel.clear_overrides()
    result = await submit(hass, result, user_input)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == user_input


@pytest.mark.usefixtures("entry_setup")
async def test_user_step_shows_unknown_and_logs_the_traceback_of_an_unexpected_error(
    hass: HomeAssistant,
    panel: FakePanel,
    caplog: pytest.LogCaptureFixture,
    user_input: dict[str, str],
) -> None:
    with patch.object(
        ParadoxClient, "async_get_status", side_effect=RuntimeError("boom")
    ):
        result = await submit(hass, await start_user_flow(hass), user_input)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
    suggested = suggested_values(result)
    assert (suggested["host"], suggested["username"]) == (panel.host, "user")
    records = [r for r in problem_records(caplog) if r.name == LOGGER.name]
    assert [r.exc_info and r.exc_info[0] for r in records] == [RuntimeError]
    assert not panel.logged_in

    result = await submit(hass, result, user_input)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == user_input


@pytest.mark.parametrize("padding", ["", "  "])
async def test_user_step_aborts_for_a_known_host_before_any_request_reaches_the_panel(
    hass: HomeAssistant,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    padding: str,
) -> None:
    mock_config_entry.add_to_hass(hass)
    other_login = {
        "host": f"{padding}{panel.host}{padding}",
        "username": "other",
        "password": "0000",
    }

    result = await submit(hass, await start_user_flow(hass), other_login)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert panel.requests == []


async def test_reauth_form_suggests_the_username_of_the_entry(
    hass: HomeAssistant, stale_entry: MockConfigEntry
) -> None:
    result = await stale_entry.start_reauth_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {}
    assert suggested_values(result) == {"username": "old"}


async def test_reauth_with_rejected_credentials_shows_invalid_auth_and_keeps_the_entry(
    hass: HomeAssistant, panel: FakePanel, stale_entry: MockConfigEntry
) -> None:
    result = await stale_entry.start_reauth_flow(hass)

    result = await submit(hass, result, {"username": "user", "password": "0000"})

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "invalid_auth"}
    assert stale_entry.data == {
        "host": panel.host,
        "username": "old",
        "password": "9999",
    }


@pytest.mark.usefixtures("entry_setup")
async def test_reauth_with_accepted_credentials_updates_and_reloads_the_entry(
    hass: HomeAssistant, panel: FakePanel, stale_entry: MockConfigEntry
) -> None:
    result = await stale_entry.start_reauth_flow(hass)

    result = await submit(hass, result, {"username": "user", "password": "1234"})
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert stale_entry.data == {
        "host": panel.host,
        "username": "user",
        "password": "1234",
    }
    assert stale_entry.state is ConfigEntryState.LOADED
    assert len(panel.requests_to("/default.html")) == 1
    assert panel.request_lines[-1] == "GET /logout.html"


@pytest.mark.parametrize(
    "key",
    [
        "error.cannot_connect",
        "error.invalid_auth",
        "error.unsupported_firmware",
        "error.unknown",
        "abort.already_configured",
        "abort.reauth_successful",
    ],
)
async def test_every_error_and_abort_reason_of_the_flow_is_translated(
    hass: HomeAssistant, key: str
) -> None:
    translations = await async_get_translations(hass, "en", "config", [DOMAIN])

    assert translations[f"component.{DOMAIN}.config.{key}"]
