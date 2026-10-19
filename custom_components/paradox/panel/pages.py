import json
import re
from typing import TYPE_CHECKING, Any, NoReturn

from .errors import ParadoxPageError
from .models import PanelLayout, PanelStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

# re.ASCII keeps \w and \d ASCII-only, as they are in the bridge's JavaScript patterns.
LOGIN_SESSION_VALUE_RE = re.compile(r'loginaff\("([\w]+)"', re.ASCII)
PAGE_TITLE_RE = re.compile(r"<title>([^<]*)</title>", re.ASCII | re.IGNORECASE)
ZONES_VALUE_RE = re.compile(
    r'tbl_zone[ ]+=[ ]+(new Array\((?:\d+,"(?:[^"\\]|\\.)*",?)*\))', re.ASCII
)
AREA_NAMES_RE = re.compile(
    r'tbl_areanam[ ]+=[ ]+(new Array\((?:"(?:[^"\\]|\\.)*",?)*\))', re.ASCII
)
TROUBLE_NAMES_RE = re.compile(
    r'tbl_troublename[ ]+=[ ]+(new Array\((?:"(?:[^"\\]|\\.)*",?)*\))', re.ASCII
)
STATUSZONE_VALUE_RE = re.compile(
    r"tbl_statuszone[ ]+=[ ]+(new Array\((?:\d,?)*\))", re.ASCII
)
USERACCESS_VALUE_RE = re.compile(
    r"tbl_useraccess[ ]+=[ ]+(new Array\((?:\d,?)*\))", re.ASCII
)
ALARMS_VALUE_RE = re.compile(
    r'tbl_alarmes[ ]+=[ ]+(new Array\((?:(?:"\d+"|\d+)(?:,(?:"\d+"|\d+))*,?)?\))',
    re.ASCII,
)
TROUBLES_VALUE_RE = re.compile(
    r'tbl_troubles[ ]+=[ ]+(new Array\((?:(?:"\d+"|\d+)(?:,(?:"\d+"|\d+))*,?)?\))',
    re.ASCII,
)
# Assumed, never captured: without a session the panel answers with its login page
# and HTTP 200.
LOGIN_PAGE_RE = re.compile(r"loginaff\(", re.ASCII)

_NEW_ARRAY_RE = re.compile(r"new Array\((.*?),?\)", re.DOTALL)


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(name)


def get_js_value(content: str, pattern: str | re.Pattern[str]) -> Any:
    """
    Read capture group 1 of the first match of ``pattern`` in ``content`` as JSON.

    A capture that is exactly ``new Array(...)`` is read as the list ``[...]``, with
    one trailing comma dropped. A missing or empty capture gives ``None``. A capture
    that is not JSON, ``NaN`` and ``Infinity`` included, raises ``ParadoxPageError``.
    """
    regex = re.compile(pattern, re.ASCII) if isinstance(pattern, str) else pattern
    match = regex.search(content)
    if match is None:
        raise ParadoxPageError("Regex didn't match the value")

    value = match.group(1) if regex.groups else None
    if not value:
        return None
    if array := _NEW_ARRAY_RE.fullmatch(value):
        value = f"[{array.group(1)}]"
    try:
        return json.loads(value, parse_constant=_reject_constant)
    except ValueError:
        raise ParadoxPageError("Value is not valid JSON") from None


def pairs(values: Sequence[Any]) -> tuple[tuple[int, str], ...]:
    """
    Group ``values`` into ``(area bitmask, name)`` pairs.

    An odd number of values raises ``ParadoxPageError``.
    """
    if len(values) % 2:
        raise ParadoxPageError("Invalid list length, should be divisible by tuple size")
    return tuple(zip(values[::2], values[1::2], strict=True))


def parse_session_value(page: str) -> str:
    match = LOGIN_SESSION_VALUE_RE.search(page)
    if match is None:
        raise ParadoxPageError("Session value not found in login page")
    return match.group(1)


def parse_title(page: str) -> str:
    match = PAGE_TITLE_RE.search(page)
    if match is None:
        raise ParadoxPageError("Title not found in default page")
    return match.group(1)


def is_login_page(page: str) -> bool:
    return LOGIN_PAGE_RE.search(page) is not None


def _table(page: str, name: str, pattern: re.Pattern[str]) -> list[Any]:
    if name not in page:
        return []
    return list(get_js_value(page, pattern))


def _indexes(page: str, name: str, pattern: re.Pattern[str]) -> tuple[int, ...]:
    try:
        return tuple(int(entry) for entry in _table(page, name, pattern))
    except ValueError:
        raise ParadoxPageError(f"Invalid index in {name}") from None


def parse_index_page(page: str) -> PanelLayout:
    """
    Read the zone, area-name and trouble-name tables of the index page.

    A page without the name ``tbl_areanam`` or ``tbl_troublename`` gives ``()`` for
    it. Any table that is present but not in the expected format raises
    ``ParadoxPageError``, as does a missing zone table.
    """
    return PanelLayout(
        zones=pairs(get_js_value(page, ZONES_VALUE_RE)),
        area_names=tuple(_table(page, "tbl_areanam", AREA_NAMES_RE)),
        trouble_names=tuple(_table(page, "tbl_troublename", TROUBLE_NAMES_RE)),
    )


def parse_status_page(page: str) -> PanelStatus:
    """
    Read the tables of the status page.

    A page without the name ``tbl_alarmes`` or ``tbl_troubles`` gives ``()`` for it.
    Any table that is present but not in the expected format raises
    ``ParadoxPageError``, as does a missing ``tbl_statuszone`` or ``tbl_useraccess``.
    """
    return PanelStatus(
        statuszone=tuple(get_js_value(page, STATUSZONE_VALUE_RE)),
        useraccess=tuple(get_js_value(page, USERACCESS_VALUE_RE)),
        alarms=_indexes(page, "tbl_alarmes", ALARMS_VALUE_RE),
        troubles=_indexes(page, "tbl_troubles", TROUBLES_VALUE_RE),
    )
