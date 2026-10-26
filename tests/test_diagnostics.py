from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from syrupy.filters import props

from tests.helpers import setup_entry

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
    from syrupy.assertion import SnapshotAssertion

    from tests.fake_panel import FakePanel

REDACTED = "**REDACTED**"


async def test_diagnostics_hold_the_entry_without_credentials_the_layout_and_the_status(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
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

    # The fake panel listens on a port that differs per run.
    assert diagnostics["entry"]["data"] == {
        "host": panel.host,
        "username": REDACTED,
        "password": REDACTED,
    }
    assert diagnostics == snapshot(
        exclude=props("entry_id", "created_at", "modified_at", "host")
    )


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
