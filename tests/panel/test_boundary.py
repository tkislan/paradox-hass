import ast
from pathlib import Path

PANEL_DIR = Path(__file__).parents[2] / "custom_components" / "paradox" / "panel"


def homeassistant_imports(path: Path) -> list[str]:
    modules = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return [
        module
        for module in modules
        if module == "homeassistant" or module.startswith("homeassistant.")
    ]


def offenders(root: Path) -> dict[str, list[str]]:
    return {
        str(file.relative_to(root)): found
        for file in sorted(root.rglob("*.py"))
        if (found := homeassistant_imports(file))
    }


def test_panel_modules_do_not_import_homeassistant() -> None:
    assert {file.name for file in PANEL_DIR.rglob("*.py")} >= {
        "crypto.py",
        "errors.py",
        "models.py",
        "pages.py",
    }

    found = offenders(PANEL_DIR)

    assert not found, f"panel modules import homeassistant: {found}"


def test_checker_finds_homeassistant_imports_in_any_module(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "ok.py").write_text(
        "import os\nimport homeassistant_fake\nfrom . import sibling\n",
        encoding="utf-8",
    )
    (tmp_path / "bad.py").write_text(
        "import os, homeassistant\n"
        "import homeassistant.core as core\n"
        "from homeassistant.helpers import entity\n"
        "from homeassistant import const\n",
        encoding="utf-8",
    )
    (tmp_path / "sub" / "nested.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from homeassistant.core import HomeAssistant\n",
        encoding="utf-8",
    )

    assert offenders(tmp_path) == {
        "bad.py": [
            "homeassistant",
            "homeassistant.core",
            "homeassistant.helpers",
            "homeassistant",
        ],
        "sub/nested.py": ["homeassistant.core"],
    }
