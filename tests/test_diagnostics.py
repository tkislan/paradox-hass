from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from tests.helpers import setup_entry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

    from tests.fake_panel import FakePanel

REDACTED = "**REDACTED**"


async def test_diagnostics_hold_the_entry_without_credentials_the_layout_and_the_status(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    panel.set_status(
        statuszone=[0, 0, 1, *[0] * 29],
        useraccess=[2, 0],
        alarms=["1"],
        troubles=["16", "19", "38"],
    )
    await setup_entry(hass, mock_config_entry)

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    assert set(diagnostics) == {"entry", "layout", "status", "last_update_success"}
    assert diagnostics["entry"]["data"] == {
        "host": panel.host,
        "username": REDACTED,
        "password": REDACTED,
    }
    layout = diagnostics["layout"]
    assert set(layout) == {"zones", "area_names", "trouble_names"}
    assert layout["area_names"] == ["Cast 1", "Cast 2"]
    assert layout["zones"] == [
        [1, "Predsien"],
        [1, "Lava izba"],
        [1, "Prava izba"],
        [1, "Spalna"],
        [1, "Obyvacka"],
        [1, "Chodba"],
        *[[0, " "]] * 26,
    ]
    trouble_names = layout["trouble_names"]
    assert len(trouble_names) == 45
    assert trouble_names[16] == "Odpojený výstup Bell"
    assert trouble_names[19] == "Výpadok času a dátumu"
    assert trouble_names[38] == "Tamper na PCS module"
    assert diagnostics["status"] == {
        "statuszone": [0, 0, 1, *[0] * 29],
        "useraccess": [2, 0],
        "alarms": [1],
        "troubles": [16, 19, 38],
    }
    assert diagnostics["last_update_success"] is True


async def test_diagnostics_keep_the_last_status_after_a_failed_poll(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    panel.set_status(troubles=["16"])
    await setup_entry(hass, mock_config_entry)
    mock_config_entry.runtime_data.async_set_update_error(UpdateFailed("down"))

    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    assert diagnostics["last_update_success"] is False
    assert diagnostics["status"]["troubles"] == [16]
