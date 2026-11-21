from typing import TYPE_CHECKING, Final

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EntityCategory, Platform
from homeassistant.helpers import entity_registry as er

from tests.fake_panel import FakeResponse, Reset
from tests.helpers import entity_id_for, setup_entry, tick

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.fake_panel import FakePanel

SENSOR_ENTITY_ID: Final = "sensor.paradox_failed_requests"


def sensor_state(hass: HomeAssistant) -> str:
    state = hass.states.get(SENSOR_ENTITY_ID)
    assert state is not None
    return state.state


async def test_the_sensor_is_a_diagnostic_total_named_failed_requests(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, mock_config_entry)

    assert (
        entity_id_for(hass, mock_config_entry, Platform.SENSOR, "failed_requests")
        == SENSOR_ENTITY_ID
    )
    registry_entry = er.async_get(hass).async_get(SENSOR_ENTITY_ID)
    assert registry_entry is not None
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC
    state = hass.states.get(SENSOR_ENTITY_ID)
    assert state is not None
    assert state.attributes["state_class"] == "total_increasing"
    assert state.attributes["friendly_name"] == "Paradox Failed requests"


async def test_the_sensor_is_0_after_a_setup_without_failures(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, mock_config_entry)

    assert sensor_state(hass) == "0"


async def test_the_sensor_counts_a_failed_request_of_the_setup(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    panel.respond_with("/logout.html", Reset(), times=2)

    await setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert sensor_state(hass) == "1"


async def test_a_reset_poll_counts_at_once(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    panel.respond_with("/statuslive.html", Reset(), times=2)

    await tick(hass, freezer)

    assert sensor_state(hass) == "1"


async def test_a_keep_alive_answered_with_http_500_counts(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    panel.respond_with("/keep_alive.html", FakeResponse(status=500), times=1)

    await tick(hass, freezer, 3)

    assert sensor_state(hass) == "1"


async def test_a_poll_answered_with_the_login_page_does_not_count(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    seen_at_setup = len(panel.requests)
    panel.expire_session()

    await tick(hass, freezer)

    assert "GET /login_page.html" in panel.request_lines[seen_at_setup:]
    assert sensor_state(hass) == "0"


async def test_the_sensor_stays_available_and_counting_while_the_panel_is_down(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, mock_config_entry)
    panel.down = True

    await tick(hass, freezer, 31)

    assert {
        state.state
        for state in hass.states.async_all(["alarm_control_panel", "binary_sensor"])
    } == {"unavailable"}
    # 31 polls and the keep-alives at 3, 6, ..., 30 s. aiohttp sends each twice, and
    # each counts once.
    assert sensor_state(hass) == "41"
