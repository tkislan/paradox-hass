import re
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import EntityCategory, Platform
from homeassistant.helpers import entity_registry as er

from tests.fake_panel import FakeResponse, render_index_page
from tests.helpers import TROUBLE_ENTITY_ID, entity_id_for, setup_entry, tick

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from tests.fake_panel import FakePanel

BELL: Final = "Odpojený výstup Bell"
TIME_AND_DATE: Final = "Výpadok času a dátumu"
PCS_TAMPER: Final = "Tamper na PCS module"


def serve_trouble_names(panel: FakePanel, table: str) -> None:
    page = re.sub(
        r"tbl_troublename = new Array\(.*\);",
        lambda _: f"tbl_troublename = new Array({table});",
        render_index_page(panel.zones),
    )
    panel.respond_with("/index.html", FakeResponse(body=page))


def trouble_attributes(hass: HomeAssistant) -> dict[str, Any]:
    state = hass.states.get(TROUBLE_ENTITY_ID)
    assert state is not None
    return {"state": state.state, "troubles": state.attributes["troubles"]}


async def test_the_trouble_sensor_is_a_diagnostic_problem_sensor_named_trouble(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, mock_config_entry)

    assert (
        entity_id_for(hass, mock_config_entry, Platform.BINARY_SENSOR, "trouble")
        == TROUBLE_ENTITY_ID
    )
    registry_entry = er.async_get(hass).async_get(TROUBLE_ENTITY_ID)
    assert registry_entry is not None
    assert registry_entry.original_device_class == "problem"
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC
    state = hass.states.get(TROUBLE_ENTITY_ID)
    assert state is not None
    assert state.attributes["friendly_name"] == "Paradox Trouble"


async def test_the_trouble_sensor_is_on_and_names_the_active_troubles(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    panel.set_status(troubles=["16", "19", "38"])

    await setup_entry(hass, mock_config_entry)

    assert trouble_attributes(hass) == {
        "state": "on",
        "troubles": [BELL, TIME_AND_DATE, PCS_TAMPER],
    }


async def test_the_trouble_sensor_follows_the_troubles_the_panel_reports(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    panel: FakePanel,
    mock_config_entry: MockConfigEntry,
) -> None:
    panel.set_status(troubles=[])
    await setup_entry(hass, mock_config_entry)
    assert trouble_attributes(hass) == {"state": "off", "troubles": []}

    panel.set_status(troubles=["16"])
    await tick(hass, freezer)
    assert trouble_attributes(hass) == {"state": "on", "troubles": [BELL]}

    panel.set_status(troubles=["19", "38"])
    await tick(hass, freezer)
    assert trouble_attributes(hass) == {
        "state": "on",
        "troubles": [TIME_AND_DATE, PCS_TAMPER],
    }

    panel.set_status(troubles=[])
    await tick(hass, freezer)
    assert trouble_attributes(hass) == {"state": "off", "troubles": []}


async def test_a_trouble_is_named_by_its_stripped_name_or_else_by_its_index(
    hass: HomeAssistant, panel: FakePanel, mock_config_entry: MockConfigEntry
) -> None:
    # Index 1 is blank and index 3 is past the end of the table.
    serve_trouble_names(panel, '"  Bell ","   ","Siren"')
    panel.set_status(troubles=["0", "1", "2", "3"])

    await setup_entry(hass, mock_config_entry)

    assert trouble_attributes(hass) == {
        "state": "on",
        "troubles": ["Bell", "Trouble 1", "Siren", "Trouble 3"],
    }
