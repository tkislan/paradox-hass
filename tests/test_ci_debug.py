# ruff: noqa: PTH109, E501, FBT003, PGH004
import hashlib
import locale
import os
import sys
from pathlib import Path

from syrupy.extensions.amber.serializer import AmberDataSerializer


def test_debug() -> None:
    directory = Path(__file__).parent / "snapshots"
    path = directory / "test_diagnostics.ambr"
    info = {
        "cwd": os.getcwd(),
        "file": __file__,
        "dir_exists": directory.exists(),
        "listing": [(p.name, p.stat().st_size) for p in directory.iterdir()]
        if directory.exists()
        else None,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
        if path.exists()
        else None,
        "head": path.read_bytes()[:120] if path.exists() else None,
        "utf8_mode": sys.flags.utf8_mode,
        "preferred": locale.getpreferredencoding(False),
        "fs": sys.getfilesystemencoding(),
        "python": sys.version,
    }
    if path.exists():
        collection = AmberDataSerializer.read_file(str(path))
        info["names"] = [snapshot.name for snapshot in collection]
        info["tainted"] = collection.tainted
    raise AssertionError(repr(info))
