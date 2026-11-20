# Changelog

## 0.1.0

First version.

- YAML layout for fixed-width files with several record types, CSV and Excel.
- Types: `str`, `int`, `decimal` (implied decimals in fixed-width) and `date`.
- `remessa check`: unmatched lines, wrong line length, values that do not convert, trailer counts, missing and unexpected columns. Single pass over the file.
- `remessa sql`, `remessa export` (Parquet) and `remessa init` (layout draft from a sample).
- Built-in `cnab240` layout: FEBRABAN v10.1, segments P, Q, R, T and U.
- Reads and writes `s3://` through DuckDB; `--init` runs setup SQL on the connection.
- Works with DuckDB 1.5 and 2.0.
