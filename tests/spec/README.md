# Spec tables and captured pages

Copies of the language-neutral case tables and captured panel pages of the Node.js bridge, [`tkislan/paradox`](https://github.com/tkislan/paradox), at commit `e6cb9b8297c1c7a270e441861fa1d8176175ff05`. Copies are never edited: a change belongs in the bridge, and the files are then copied again, byte for byte.

Copied from `tests/spec/` of the bridge into this directory:

- `crypto.json`
- `util.json`
- `status_pages.json`
- `login_cases.json`
- `status_machine.json`

Copied from `tests/data/` of the bridge into `tests/data/`; rows refer to them as `{"file": "data/<name>.html"}`, relative to `tests/`:

- `armed.html`
- `unarmed.html`
- `index_page.html`
- `login_page.html`
- `default_page.html`
- `pretty_default_page.html`

Left out:

- The `known_bug` rows, with the bridge's `tests/spec/known_bugs/` directory. They pin defects of the bridge on purpose.
- `crypto.json` rows with `js_only`: they depend on JavaScript semantics, and the tests skip them.
- `tests/data/login_url.txt` of the bridge: it holds a real host and hashed credentials.
- The bridge's `system_scenarios.json`, `schemas.ts` and `crypto_reference.py`.

`tests/test_status_timelines.py` replays the zone groups of `status_machine.json` in Home Assistant; no test loads its `armed_codes` and `status_poll_cadence` groups yet.

`tests/spec_tables.py` loads these files; a test that loads a group rejects any row of it with a key its format does not allow. The format of every row is defined by `tests/spec/schemas.ts` of the bridge.
