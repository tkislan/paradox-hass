import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from _pytest.mark import ParameterSet

TESTS_DIR = Path(__file__).parent
SPEC_DIR = TESTS_DIR / "spec"

_COMMON_KEYS = {"name", "note", "known_bug"}


def load_spec(name: str) -> dict[str, Any]:
    return json.loads((SPEC_DIR / f"{name}.json").read_text(encoding="utf-8"))


def spec_rows(name: str, group: str, keys: set[str]) -> list[ParameterSet]:
    """
    One test case per row of ``group``, with its ``name`` (else ``operation``) as id.

    Raises ``ValueError`` naming the file, group and row when a row has a key outside
    ``keys``.
    """
    rows = []
    for row in load_spec(name)[group]:
        row_id = row.get("name", row.get("operation"))
        if unknown := row.keys() - keys - _COMMON_KEYS:
            where = f"{name}.json, group {group!r}, row {row_id!r}"
            raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
        rows.append(pytest.param(row, id=row_id))
    return rows


def spec_text(value: str | dict[str, str]) -> str:
    """Inline text as is, or the content of ``{"file": path}`` under ``tests/``."""
    if isinstance(value, str):
        return value
    if value.keys() != {"file"}:
        raise ValueError(f"expected a string or {{'file': ...}}, got {sorted(value)}")
    return (TESTS_DIR / value["file"]).read_text(encoding="utf-8")
