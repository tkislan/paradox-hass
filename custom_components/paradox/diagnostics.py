from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import ParadoxConfigEntry

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: ParadoxConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "layout": asdict(coordinator.layout),
        "status": asdict(coordinator.data),
        "last_update_success": coordinator.last_update_success,
    }
