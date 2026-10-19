from dataclasses import FrozenInstanceError

import pytest

from custom_components.paradox.panel.models import PanelLayout, PanelStatus


def status(
    statuszone: tuple[int, ...] = (), useraccess: tuple[int, ...] = ()
) -> PanelStatus:
    return PanelStatus(
        statuszone=statuszone, useraccess=useraccess, alarms=(), troubles=()
    )


def test_zone_open_for_open_codes_only() -> None:
    panel = status(statuszone=tuple(range(11)))

    assert [slot for slot in range(11) if panel.zone_open(slot)] == [1, 2, 4, 6, 9]


def test_zone_open_codes_are_exactly_the_open_ones() -> None:
    panel = status(statuszone=tuple(range(256)))

    assert [slot for slot in range(256) if panel.zone_open(slot)] == [1, 2, 4, 6, 9]


def test_models_are_frozen_and_slotted() -> None:
    layout = PanelLayout(zones=(), area_names=(), trouble_names=())
    panel = status()

    with pytest.raises(FrozenInstanceError):
        layout.zones = ((1, "A"),)
    with pytest.raises(FrozenInstanceError):
        panel.alarms = (1,)
    assert not hasattr(layout, "__dict__")
    assert not hasattr(panel, "__dict__")


def test_zone_open_is_false_past_the_table() -> None:
    assert not status(statuszone=tuple(range(11))).zone_open(11)


def test_zone_open_is_false_for_a_negative_slot() -> None:
    assert not status(statuszone=(1, 1)).zone_open(-1)


def test_area_code_is_none_past_the_table() -> None:
    panel = status(useraccess=(7, 0))

    assert [panel.area_code(index) for index in range(3)] == [7, 0, None]


def test_area_code_is_none_for_a_negative_index() -> None:
    assert status(useraccess=(7, 0)).area_code(-1) is None


def test_zone_slots_skip_unused_slots() -> None:
    layout = PanelLayout(
        zones=((1, "A"), (0, " "), (2, "C")), area_names=(), trouble_names=()
    )

    assert layout.zone_slots == (0, 2)
