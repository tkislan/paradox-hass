import json
from typing import TYPE_CHECKING

import pytest

from tests import spec_tables
from tests.spec_tables import spec_rows, spec_text

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def spec_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(spec_tables, "SPEC_DIR", tmp_path)
    return tmp_path


def write_spec(spec_dir: Path, rows: list[dict[str, object]]) -> None:
    (spec_dir / "sample.json").write_text(
        json.dumps({"_comment": "x", "things": rows}), encoding="utf-8"
    )


def test_row_with_unknown_key_names_file_group_and_row(spec_dir: Path) -> None:
    write_spec(
        spec_dir,
        [{"name": "fine", "a": 1}, {"name": "the odd one", "a": 1, "extra": 2}],
    )

    with pytest.raises(
        ValueError, match=r"sample\.json.*'things'.*'the odd one'.*extra"
    ):
        spec_rows("sample", "things", {"a"})


def test_rows_become_cases_with_name_as_id(spec_dir: Path) -> None:
    write_spec(
        spec_dir,
        [
            {"name": "first", "a": 1, "note": "n"},
            {"name": "second", "a": 2, "known_bug": "b"},
        ],
    )

    rows = spec_rows("sample", "things", {"a"})

    assert [row.id for row in rows] == ["first", "second"]
    assert [row.values[0]["a"] for row in rows] == [1, 2]


def test_row_without_name_uses_operation_as_id(spec_dir: Path) -> None:
    write_spec(spec_dir, [{"operation": "arm", "a": 1}])

    assert [row.id for row in spec_rows("sample", "things", {"a", "operation"})] == [
        "arm"
    ]


def test_name_wins_over_operation_as_id(spec_dir: Path) -> None:
    write_spec(spec_dir, [{"name": "n", "operation": "op"}])

    rows = spec_rows("sample", "things", {"operation"})

    assert [row.id for row in rows] == ["n"]


def test_spec_text_inline_is_returned_as_is() -> None:
    assert spec_text("tbl_zone = new Array()") == "tbl_zone = new Array()"


def test_spec_text_file_is_read_relative_to_tests() -> None:
    assert "loginaff(" in spec_text({"file": "data/login_page.html"})


def test_spec_text_rejects_other_keys() -> None:
    with pytest.raises(ValueError, match="body"):
        spec_text({"file": "data/login_page.html", "body": "x"})
    with pytest.raises(ValueError, match="body"):
        spec_text({"body": "x"})
