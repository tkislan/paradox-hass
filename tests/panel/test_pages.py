import re
from typing import Any

import pytest

from custom_components.paradox.panel.errors import ParadoxError, ParadoxPageError
from custom_components.paradox.panel.models import PanelLayout
from custom_components.paradox.panel.pages import (
    get_js_value,
    is_login_page,
    pairs,
    parse_index_page,
    parse_session_value,
    parse_status_page,
    parse_title,
)
from tests.spec_tables import TESTS_DIR, spec_rows, spec_text

JS_VALUE_KEYS = {"content", "pattern", "expected"}
LIST_KEYS = {"list", "expected"}
STATUS_FIELDS = ("statuszone", "useraccess", "alarms", "troubles")


def page(name: str) -> str:
    return (TESTS_DIR / "data" / f"{name}.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("row", spec_rows("util", "getJsValue", JS_VALUE_KEYS))
def test_get_js_value(row: dict[str, Any]) -> None:
    assert get_js_value(spec_text(row["content"]), row["pattern"]) == row["expected"]


@pytest.mark.parametrize(
    "row", spec_rows("util", "getJsValue_errors", {"content", "pattern", "error"})
)
def test_get_js_value_errors(row: dict[str, Any]) -> None:
    with pytest.raises(ParadoxPageError) as error:
        get_js_value(spec_text(row["content"]), row["pattern"])

    assert str(error.value) == row["error"]


@pytest.mark.parametrize(
    "row", spec_rows("util", "getJsValue_not_json", {"content", "pattern"})
)
def test_get_js_value_rejects_text_that_is_not_json(row: dict[str, Any]) -> None:
    with pytest.raises(ParadoxPageError):
        get_js_value(spec_text(row["content"]), row["pattern"])


@pytest.mark.parametrize(
    "content", ["x=[NaN]", "x=Infinity", "x=-Infinity", "x=new Array(NaN)"]
)
def test_get_js_value_rejects_non_finite_constants(content: str) -> None:
    with pytest.raises(ParadoxPageError):
        get_js_value(content, r"x=(.*)")


@pytest.mark.parametrize(
    "content", ["x=new Array(1)junk", "x=new Array(1),(2)", "x=new Array(1)\n"]
)
def test_get_js_value_reads_only_a_capture_that_is_exactly_new_array(
    content: str,
) -> None:
    with pytest.raises(ParadoxPageError):
        get_js_value(content, r"x=([\s\S]*)")


def test_get_js_value_error_hides_the_json_error() -> None:
    with pytest.raises(ParadoxPageError) as error:
        get_js_value("x=[NaN]", r"x=(.*)")

    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


@pytest.mark.parametrize("pattern", [r"x=(.*)", r"x=(a)?"])
def test_get_js_value_of_an_empty_or_unmatched_capture_is_none(pattern: str) -> None:
    assert get_js_value("x=", pattern) is None


def test_get_js_value_drops_only_one_trailing_comma() -> None:
    with pytest.raises(ParadoxPageError):
        get_js_value("x=new Array(1,2,,)", r"x=(.*)")


def test_get_js_value_accepts_a_compiled_pattern() -> None:
    assert get_js_value("x=[1]", re.compile(r"x=(.*)")) == [1]


def test_get_js_value_without_a_capture_group_is_none() -> None:
    assert get_js_value("x=1", r"x=1") is None


def test_get_js_value_ascii_pattern_ignores_non_ascii_digits() -> None:
    with pytest.raises(ParadoxPageError, match="didn't match"):
        get_js_value("x=٣", r"x=(\d)")


@pytest.mark.parametrize("row", spec_rows("util", "iterateTuples", LIST_KEYS))
def test_pairs(row: dict[str, Any]) -> None:
    assert [list(pair) for pair in pairs(row["list"])] == row["expected"]


@pytest.mark.parametrize(
    "row", spec_rows("util", "iterateTuples_errors", {"list", "error"})
)
def test_pairs_errors(row: dict[str, Any]) -> None:
    with pytest.raises(ParadoxPageError) as error:
        pairs(row["list"])

    assert str(error.value) == row["error"]


@pytest.mark.parametrize(
    "row", spec_rows("status_pages", "get_status", {"page", "expected"})
)
def test_parse_status_page(row: dict[str, Any]) -> None:
    content = spec_text(row["page"])
    expected = row["expected"]

    if "error" in expected:
        with pytest.raises(ParadoxPageError) as error:
            parse_status_page(content)
        assert str(error.value) == expected["error"]
        return

    status = parse_status_page(content)
    assert {field: list(getattr(status, field)) for field in STATUS_FIELDS} == expected


def test_parse_index_page_of_the_captured_page() -> None:
    layout = parse_index_page(page("index_page"))

    assert len(layout.zones) == 32
    assert layout.zones[:6] == (
        (1, "Predsien"),
        (1, "Lava izba"),
        (1, "Prava izba"),
        (1, "Spalna"),
        (1, "Obyvacka"),
        (1, "Chodba"),
    )
    assert layout.zone_slots == (0, 1, 2, 3, 4, 5)
    assert layout.area_names == ("Cast 1", "Cast 2")
    assert len(layout.trouble_names) == 45
    assert layout.trouble_names[16] == "Odpojený výstup Bell"
    assert layout.trouble_names[-1] == ""


def test_parse_index_page_without_name_tables_has_none() -> None:
    layout = parse_index_page('tbl_zone = new Array(1,"A",0," ");')

    assert layout.zones == ((1, "A"), (0, " "))
    assert layout.area_names == ()
    assert layout.trouble_names == ()


@pytest.mark.parametrize("name", ["tbl_areanam", "tbl_troublename"])
def test_parse_index_page_with_a_name_but_no_table_fails(name: str) -> None:
    with pytest.raises(ParadoxPageError, match="didn't match"):
        parse_index_page(f"tbl_zone = new Array();\nnames({name}, 1);")


def test_parse_index_page_of_a_login_page_fails() -> None:
    with pytest.raises(ParadoxPageError, match="didn't match"):
        parse_index_page(page("login_page"))


@pytest.mark.parametrize(
    ("page", "zones"),
    [
        (
            'tbl_zone = new Array(10,"A",255,"B",0," ");',
            ((10, "A"), (255, "B"), (0, " ")),
        ),
        ('tbl_zone = new Array(1,"a\\"b",0,"c\\\\");', ((1, 'a"b'), (0, "c\\"))),
    ],
)
def test_parse_index_page_zone_table(
    page: str, zones: tuple[tuple[int, str], ...]
) -> None:
    assert parse_index_page(page).zones == zones


@pytest.mark.parametrize(
    "page",
    ['tbl_zone = new Array(-1,"A");', 'tbl_zone = new Array(\u0663,"A");'],
)
def test_parse_index_page_rejects_a_bitmask_that_is_not_ascii_digits(page: str) -> None:
    with pytest.raises(ParadoxPageError, match="didn't match"):
        parse_index_page(page)


def test_parse_index_page_name_tables() -> None:
    layout = parse_index_page(
        'tbl_zone = new Array(); tbl_areanam = new Array("a\\"b","");'
        ' tbl_troublename = new Array("c\\"d","e\\\\",);'
    )

    assert layout.area_names == ('a"b', "")
    assert layout.trouble_names == ('c"d', "e\\")


def test_parse_index_page_with_empty_tables() -> None:
    layout = parse_index_page(
        "tbl_zone = new Array(); tbl_areanam = new Array();"
        " tbl_troublename = new Array();"
    )

    assert layout == PanelLayout(zones=(), area_names=(), trouble_names=())


def test_parse_session_value() -> None:
    assert parse_session_value(page("login_page")) == "91AC25D06A0C26BA"


@pytest.mark.parametrize("page", ['loginaff("\u00e9123",0)', 'loginaff("",0)'])
def test_parse_session_value_needs_ascii_word_characters(page: str) -> None:
    with pytest.raises(ParadoxPageError, match="Session value not found"):
        parse_session_value(page)


def test_parse_session_value_fails_without_one() -> None:
    with pytest.raises(ParadoxPageError, match="Session value not found in login page"):
        parse_session_value(page("default_page"))


@pytest.mark.parametrize("name", ["default_page", "pretty_default_page"])
def test_parse_title(name: str) -> None:
    assert parse_title(page(name)) == "Paradox IP Module"


def test_parse_title_is_case_insensitive() -> None:
    assert parse_title("<TITLE>Paradox IP Module</Title>") == "Paradox IP Module"


def test_parse_title_case_folding_is_ascii_only() -> None:
    with pytest.raises(ParadoxPageError, match="Title not found"):
        parse_title("<t\u0131tle>Paradox IP Module</title>")


def test_parse_title_may_be_empty() -> None:
    assert parse_title("<title></title>") == ""


def test_parse_title_fails_without_one() -> None:
    with pytest.raises(ParadoxPageError, match="Title not found in default page"):
        parse_title("<html></html>")


def test_is_login_page() -> None:
    assert is_login_page(page("login_page"))
    assert not is_login_page(page("unarmed"))
    assert not is_login_page("<p>loginaff</p>")


@pytest.mark.parametrize("table", ["tbl_alarmes", "tbl_troubles"])
def test_parse_status_page_rejects_an_index_too_long_for_an_int(table: str) -> None:
    page = (
        "tbl_statuszone = new Array(1);tbl_useraccess = new Array(1);"
        f'{table} = new Array("{"9" * 5000}");'
    )

    with pytest.raises(ParadoxPageError, match=f"Invalid index in {table}"):
        parse_status_page(page)


def test_page_error_is_a_paradox_error() -> None:
    assert issubclass(ParadoxPageError, ParadoxError)
    assert issubclass(ParadoxError, Exception)
