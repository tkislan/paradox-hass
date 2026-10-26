# ruff: noqa: PTH110, BLE001, ASYNC240
import os
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
    snap = snapshot(exclude=props("entry_id", "created_at", "modified_at", "host"))
    try:
        assert diagnostics == snap
    except AssertionError as err:
        ext = snap.extension
        tl = snap.test_location
        loc = ext.get_location(test_location=tl, index=snap.index)
        name = ext.get_snapshot_name(test_location=tl, index=snap.index)
        info = {
            "loc": loc,
            "exists": os.path.exists(loc),
            "name": name,
            "index": snap.index,
            "ext": type(ext).__name__,
            "tl_file": tl.filepath,
            "dirname": ext.dirname(test_location=tl),
        }
        try:
            data = ext.read_snapshot_data_from_location(
                snapshot_location=loc,
                snapshot_name=name,
                session_id=str(id(snap.session)),
            )
            info["recall"] = None if data is None else data[:60]
        except Exception as exc:
            info["recall_err"] = repr(exc)
        try:
            data = snap.session.recall_snapshot(ext, tl, snap.index)
            info["session_recall"] = None if data is None else data[:60]
        except Exception as exc:
            info["session_recall_err"] = repr(exc)
        info["orig"] = str(err)[:200]
        raise AssertionError(repr(info)) from err


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
