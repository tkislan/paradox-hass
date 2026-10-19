import pytest

from custom_components.paradox.panel.crypto import (
    encrypt_credentials,
    hex_md5,
    keeplowbyte,
    rc4,
)
from tests.spec_tables import spec_rows

VECTOR_KEYS = {"input", "expected"}
RC4_KEYS = {"key", "text", "expected", "not_classic_rc4", "source", "js_only"}
CREDENTIALS_KEYS = {"session", "username", "password", "u", "p", "source"}


@pytest.mark.parametrize("row", spec_rows("crypto", "hex_md5", VECTOR_KEYS))
def test_hex_md5(row: dict[str, str]) -> None:
    assert hex_md5(row["input"]) == row["expected"]


@pytest.mark.parametrize("row", spec_rows("crypto", "keeplowbyte", VECTOR_KEYS))
def test_keeplowbyte(row: dict[str, str]) -> None:
    assert keeplowbyte(row["input"]) == row["expected"]


@pytest.mark.parametrize(
    "row",
    [
        case
        for case in spec_rows("crypto", "rc4", RC4_KEYS)
        if not case.values[0].get("js_only")
    ],
)
def test_rc4(row: dict[str, str]) -> None:
    assert rc4(row["key"], row["text"]) == row["expected"]


@pytest.mark.parametrize("row", spec_rows("crypto", "credentials", CREDENTIALS_KEYS))
def test_credentials(row: dict[str, str]) -> None:
    assert encrypt_credentials(row["session"], row["username"], row["password"]) == (
        row["u"],
        row["p"],
    )


def test_a_lone_surrogate_counts_as_its_low_byte() -> None:
    assert keeplowbyte("\ud800") == "\x00"
    assert hex_md5("\ud800") == "93B885ADFE0DA089CDF634904FD59F71"


def test_rc4_rejects_a_key_of_more_than_256_code_units() -> None:
    with pytest.raises(ValueError, match="256"):
        rc4("k" * 257, "text")


def test_rc4_rejects_a_key_that_is_short_in_characters_but_long_in_code_units() -> None:
    with pytest.raises(ValueError, match="256"):
        rc4("😀" * 129, "text")
