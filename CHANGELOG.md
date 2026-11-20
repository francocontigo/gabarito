# Changelog

## Unreleased

- `encoding: auto` and `detect_encoding()`: utf-8, latin-1 (ISO-8859-1) or CP1252, by decoding the whole file.
- `init` detects the encoding instead of assuming latin-1.
- `sql()` and `rejects_sql()` take an optional connection, used by `encoding: auto`.
- Removed `rejects()`: it was `con.sql(rejects_sql(...))`. Use that.

## 0.2.0

- `gabarito write`: write a fixed-width file from queries, with the same layout that reads it. `value` literals, `_order` to interleave records, and an error instead of a truncated number.
- `gabarito rejects`: the lines that break the layout, with line number and reasons.
- `expect` accepts `sum: <record>.<field>`.
- New built-in layouts: `cnab240_pagamentos`, `cnab240_santander`, `cnab400_bradesco_remessa`, `cnab400_bradesco_retorno`, `cnab400_itau_remessa`, `cnab400_itau_retorno`.
- `examples/workflow`: check, rejects, load into Postgres, query, Parquet and return file in one script.

## 0.1.0

First version.

- YAML layout for fixed-width files with several record types, CSV and Excel.
- Types: `str`, `int`, `decimal` (implied decimals in fixed-width) and `date`.
- `gabarito check`: unmatched lines, wrong line length, values that do not convert, trailer counts, missing and unexpected columns. Single pass over the file.
- `gabarito sql`, `gabarito export` (Parquet) and `gabarito init` (layout draft from a sample).
- Built-in `cnab240` layout: FEBRABAN v10.1, segments P, Q, R, T and U.
- Reads and writes `s3://` through DuckDB; `--init` runs setup SQL on the connection.
- Works with DuckDB 1.5 and 2.0.
