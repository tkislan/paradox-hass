from dataclasses import dataclass
from typing import Final

ZONE_OPEN_CODES: Final = frozenset({1, 2, 4, 6, 9})


@dataclass(frozen=True, slots=True)
class PanelLayout:
    """
    Tables of the index page.

    Each ``zones`` entry is ``(area bitmask, name)`` of one zone slot: bit *i* of the
    bitmask means area *i* + 1, and 0 marks an unused slot.
    """

    zones: tuple[tuple[int, str], ...]
    area_names: tuple[str, ...]
    trouble_names: tuple[str, ...]

    @property
    def zone_slots(self) -> tuple[int, ...]:
        return tuple(
            slot
            for slot, (area_bitmask, _) in enumerate(self.zones)
            if area_bitmask != 0
        )


@dataclass(frozen=True, slots=True)
class PanelStatus:
    """
    Tables of the status page.

    ``statuszone`` holds one code per zone slot and ``useraccess`` one per area.
    ``alarms`` and ``troubles`` are 0-based indexes into ``PanelLayout.area_names``
    and ``PanelLayout.trouble_names``.
    """

    statuszone: tuple[int, ...]
    useraccess: tuple[int, ...]
    alarms: tuple[int, ...]
    troubles: tuple[int, ...]

    def zone_open(self, slot: int) -> bool:
        return (
            0 <= slot < len(self.statuszone)
            and self.statuszone[slot] in ZONE_OPEN_CODES
        )

    def area_code(self, index: int) -> int | None:
        return self.useraccess[index] if 0 <= index < len(self.useraccess) else None
