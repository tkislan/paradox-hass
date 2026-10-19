"""
The panel's login crypto, MD5 and RC4 as the firmware computes them.

Strings are handled as UTF-16 code units, so a character outside the BMP counts
twice. MD5 hashes only the low byte of each unit; RC4 text units are XORed whole.
"""

import hashlib
from typing import Final

_STATE_SIZE: Final = 256


def utf16_units(text: str) -> list[int]:
    raw = text.encode("utf-16-le", "surrogatepass")
    return [int.from_bytes(raw[i : i + 2], "little") for i in range(0, len(raw), 2)]


def keeplowbyte(text: str) -> str:
    return "".join(chr(unit & 0xFF) for unit in utf16_units(text))


def hex_md5(text: str) -> str:
    data = bytes(unit & 0xFF for unit in utf16_units(text))
    return hashlib.md5(data, usedforsecurity=False).hexdigest().upper()


def rc4(key: str, text: str) -> str:
    """
    Encrypt ``text`` with the firmware's RC4 variant and return upper-case hex.

    Raises ``ValueError`` for a key of more than 256 UTF-16 code units, which the
    firmware's key schedule does not define.
    """
    key_units = utf16_units(key)
    if len(key_units) > _STATE_SIZE:
        raise ValueError(
            "key longer than 256 code units is undefined behaviour in the firmware code"
        )
    state = list(range(_STATE_SIZE))
    y = 0
    for x in range(len(key_units) - 1, -1, -1):
        y = (key_units[x] + state[x] + y) % _STATE_SIZE
        state[x], state[y] = state[y], state[x]

    out: list[str] = []
    y = 0
    for position, unit in enumerate(utf16_units(text)):
        i = position % _STATE_SIZE
        y = (state[i] + y) % _STATE_SIZE
        state[i], state[y] = state[y], state[i]
        out.append(f"{unit ^ state[(state[i] + state[y]) % _STATE_SIZE]:02X}")
    return "".join(out)


def encrypt_credentials(
    session_value: str, username: str, password: str
) -> tuple[str, str]:
    """
    Return ``(u, p)``, the query parameters of ``GET /default.html``.

    Raises ``ValueError`` when ``session_value`` makes the RC4 key longer than 256
    code units.
    """
    key = hex_md5(keeplowbyte(password)) + session_value
    return rc4(key, username), hex_md5(key)
