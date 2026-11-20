# Contributing

```bash
uv sync
uv run pytest
uvx ruff check . && uvx ruff format .
```

## Adding a bank or file layout

Layouts live in `src/gabarito/layouts/` and are plain YAML. To add one:

1. Start from the closest existing file (`cnab240.yml` for a bank that deviates from FEBRABAN), or run `gabarito init` on a sample.
2. Take positions from the bank's published manual, and say which document and version in a comment at the top of the file.
3. Add `length` to every record and an `expect` rule for the trailer count, so `check` can catch drift.
4. Add a test with a synthetic file. Do not commit real files: they carry names, documents and amounts.

Name the file after what it describes, for example `cnab240_santander.yml` or `cnab400_bradesco.yml`.

## Code

- The library builds SQL and DuckDB runs it. Changes that loop over rows in Python are out of scope.
- Keep it working on DuckDB 1.5 and 2.0. CI runs both, on Linux, Windows and macOS.
- One test per behaviour, in `tests/test_gabarito.py`.
