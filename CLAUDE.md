# paradox-hass

Home Assistant integration for Paradox alarm panels (IP100/IP150 web module). The protocol code is a port of the Node.js bridge `tkislan/paradox`.

## Setup

```sh
pyenv install
mise install
uv sync
```

## Checks

CI runs all four on every pull request.

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

## Rules

- Nothing under `custom_components/paradox/panel/` imports `homeassistant`; `tests/panel/test_boundary.py` enforces it.
- The files that `tests/spec/README.md` lists as copies are never edited. Fix the source repository and copy again.
