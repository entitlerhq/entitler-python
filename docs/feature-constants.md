# Feature constants

A feature constant holds its `key`, its `type` and, for a group, `includes` (its member keys).
Typed constants make a type checker narrow each answer and keep usage methods to metered
features.

## By hand

```python
from entitler import Boolean, Config, Feature, Group, Metered

EXPORT_PDF: Feature[Boolean] = Feature.boolean("export_pdf")
TEAM_SEATS: Feature[Config] = Feature.config("team_seats")
AI_CREDITS: Feature[Metered] = Feature.metered("ai_credits")
COLLABORATION: Feature[Group] = Feature.group("collaboration", ("team_seats", "shared_folders"))
```

Reads also take a plain key (`customer.check("export_pdf")`), answering a `Check`. Usage methods
take only `Feature[Metered]`, so a plain key goes through `Feature.metered(...)`.

## Generated from your catalogue

```sh
python -m entitler generate --out entitler_features.py
```

The command (also installed as `entitler`) reads the catalogue as `features()` does and writes one
`UPPER_SNAKE` constant per feature, sorted by key, each with a docstring from the feature's name,
description, unit and group members. An archived feature's docstring says
`Deprecated: Archived in Entitler.` The file is formatted as Black and Ruff leave it at their
default line length.

```python
import entitler_features as features

seats = customer.check(features.TEAM_SEATS)  # a ConfigCheck
```

| Option | Meaning |
| --- | --- |
| `--out <file>` | the file to write, `entitler_features.py` by default |
| `--key <key>` | an API key with `plans:read`; `ENTITLER_KEY` by default |
| `--base-url <url>` | the API's base URL |
| `--check` | fail when the file is out of date, ignoring the `Read from` line |
| `--allow-empty` | write the file even when Entitler lists no features |

Options take `--flag value` or `--flag=value`. It exits 0 on success and 1 otherwise.

- **Stable names.** When the file exists, each constant keeps its name for its key, so adding a
  feature never renames another. A name that clashes or is a keyword receives a deterministic
  escape.
- **No empty overwrite.** When Entitler lists no features and the file holds constants, nothing is
  written and the command fails, unless you pass `--allow-empty`.
- **Untrusted text.** Names and descriptions come from whoever edits the catalogue, so the command
  escapes them in docstrings and literals, and builds names from ASCII letters, digits and
  underscores only.

Commit the generated file. Generate from the live environment, with a key holding only
`plans:read`, since a test environment's All customers track may follow an open change. Run
`--check` nightly and on `main` rather than as a required pull request check, since pull requests
from forks have no key:

```sh
python -m entitler generate --check --out entitler_features.py
```

Build tooling can call `entitler.cli.render_features` directly.

## Snapshot keys

`python -m entitler snapshot-keys --out entitler-snapshot-keys.json` writes the keys an app ships
to verify [offline snapshots](offline-snapshots.md). It needs no key.
