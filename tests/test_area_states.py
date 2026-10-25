from typing import Any

import pytest

from custom_components.paradox.alarm_control_panel import area_state
from custom_components.paradox.panel.models import PanelStatus
from tests.spec_tables import spec_rows


@pytest.mark.parametrize(
    "row",
    spec_rows("area_states", "rows", {"useraccess", "alarms", "index", "expected"}),
)
def test_area_state(row: dict[str, Any]) -> None:
    status = PanelStatus(
        statuszone=(),
        useraccess=tuple(row["useraccess"]),
        alarms=tuple(row["alarms"]),
        troubles=(),
    )

    expected = None if row["expected"] == "unknown" else row["expected"]
    assert area_state(status, row["index"]) == expected
