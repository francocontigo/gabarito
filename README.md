# gabarito

A YAML file describes the file your client sends. DuckDB gives you typed tables.

Works with fixed-width files (several record types per file), CSV and Excel, from disk or S3, with nothing imported into a database first. It also writes fixed-width files from a query. Ships with CNAB 240 and CNAB 400 layouts.

[Português](README.pt-BR.md)

![workflow](docs/workflow.svg)

The picture as a runnable script, with Postgres in Docker: [`examples/workflow`](examples/workflow).

## Why

Companies that receive files from clients end up with one hand-written parser per client and a staging database full of untyped tables. The query engine was never the missing piece: DuckDB, Trino and Athena already join a file with a database. What is missing sits before the query:

- DuckDB has no fixed-width reader, and nothing handles files with header, detail and trailer records in one layout.
- A trailer that says "120 records" is usually parsed and never compared with the file.
- A value that fails to convert is logged, or silently becomes garbage.

`gabarito` is the small layer that covers those three. The layout is data, the SQL is generated, and `check` tells you whether today's file matches the layout before anything is loaded.

## Try it

```bash
git clone <this repo> && cd gabarito
pip install .
gabarito check  examples/cobranca.yml examples/cobranca.txt
gabarito export examples/cobranca.yml examples/cobranca.txt out/
```

[`examples/cobranca.yml`](examples/cobranca.yml):

```yaml
source: fixed
encoding: latin-1
records:
  header:
    match: '^0'
    length: 50
    fields:
      data_geracao: {pos: [2, 8], type: date, format: '%d%m%Y'}
      carteira:     {pos: [10, 41]}
  detalhe:
    match: '^1'
    length: 50
    fields:
      nome:       {pos: [2, 20]}
      contrato:   {pos: [22, 8]}
      valor:      {pos: [30, 13], type: decimal, scale: 2}
      vencimento: {pos: [43, 8], type: date, format: '%Y%m%d'}
  trailer:
    match: '^9'
    length: 50
    fields:
      qtd: {pos: [2, 6], type: int}
expect:
  - count: detalhe
    equals: trailer.qtd
```

```python
import duckdb, gabarito

con = duckdb.connect()
tables = gabarito.load("examples/cobranca.yml", "examples/cobranca.txt", con)
detalhe = tables["detalhe"]
con.sql("SELECT nome, valor, vencimento FROM detalhe WHERE valor > 1000").show()
```

## Command line

```bash
gabarito init   file.txt                     # draft a layout from a sample of the file
gabarito check  layout.yml file.txt          # does the file match the layout?
gabarito sql    layout.yml file.txt          # print the generated SQL
gabarito export layout.yml file.txt out/     # one Parquet file per record
gabarito rejects layout.yml file.txt bad.csv # the lines that break the layout, with the reasons
gabarito write  layout.yml out.txt --from detalhe="SELECT ..."   # tables -> fixed-width file
```

`check` exits with 1 and lists what it found:

```
1 line(s) match no record (lines 5)
detalhe: 1 line(s) with length other than 50 (lines 3)
detalhe.valor: 1 value(s) do not convert to decimal
expect: trailer.qtd says 2, but the file has 3 detalhe record(s)
```

For CSV and Excel it also reports missing and unexpected columns. An invalid layout exits with 2 and a one-line message, for example `field detalhe.valor: unknown key 'typ' (did you mean 'type'?)`.

`init` guesses record types from the first character and field boundaries from the sample. It is a starting point: names and types are yours to fill in.

## Layout format

| Key | Meaning |
|---|---|
| `source` | `fixed` (default), `csv` or `xlsx` |
| `encoding` | Default `utf-8`. Legacy files are often `latin-1` |
| `records.<name>.match` | Regex that identifies the record's lines. Required for `fixed` |
| `records.<name>.length` | Expected line length. Optional, used by `check` |
| `fields.<name>.pos` | `[start, length]`, start counted from 1. `fixed` only |
| `fields.<name>.column` | Column name in the file. `csv` and `xlsx` only |
| `fields.<name>.type` | `str` (default), `int`, `decimal`, `date` |
| `fields.<name>.value` | Fixed literal, used by `write` when no column feeds the field |
| `expect` | `count: <record or '*'>` or `sum: <record>.<field>` must equal `equals: <record>.<field>` |
| `delimiter`, `skip_rows` | `csv` only. Default delimiter `;` |
| `decimal_comma` | `1.234,56` instead of `1234.56`. Default: on for `csv`, off for `xlsx` |
| `sheet` | Worksheet name. `xlsx` only |

How types behave:

- Every field is trimmed. An empty field becomes `NULL`.
- `str` never becomes a number, so leading zeros survive.
- `decimal` in a `fixed` file is an implied decimal: `0000000123456` with `scale: 2` is `1234.56`.
- `date` takes a `strptime` format. An all-zero date becomes `NULL`.
- A value that does not convert becomes `NULL` in the table and shows up in `check`.

Each table has a `_file` column with the source file (except `xlsx`).

## Rejected lines

`check` counts problems; `rejects` gives you the lines, so a pipeline can load the good ones and send the bad ones back.

```python
gabarito.rejects("layout.yml", "file.txt", con)   # _file, _line, reasons, line
```

```
_line | reasons                                                                  | line
3     | detalhe: length 47, expected 50                                          | 1CURTA ...
4     | detalhe.valor: not a valid decimal; detalhe.vencimento: not a valid date | 1RUIM ...
5     | matches no record                                                        | 7 unknown record type
```

## Writing files

The same layout that reads a file writes one. Each record is fed by a query or a table; columns are matched to fields by name.

```python
gabarito.write(
    "retorno.yml",
    {
        "header": "SELECT current_date AS data",
        "detalhe": "SELECT contrato, status, valor FROM aceitos ORDER BY contrato",
        "trailer": "SELECT count(*) AS qtd, sum(valor) AS total FROM aceitos",
    },
    "retorno.txt",
    con,
)
```

- Text is padded with spaces and cut to the field; numbers are zero-padded; decimals lose the point (`scale`); dates use the field's `format`; `NULL` becomes blanks or zeros.
- A number that does not fit its field, or is negative, raises an error instead of being cut.
- A field with no column takes its `value` (record type codes, for example), or blanks.
- Records come out in layout order. To interleave them (CNAB: each segment P followed by its Q), add an `_order` column to those queries.
- Lines end in CRLF by default (`newline=` changes it). Output is a local file.

## CNAB layouts

Ready-made layouts for Brazilian bank files. Use the name in place of a YAML path:

```bash
gabarito check  cnab240 retorno.ret
gabarito export cnab400_itau_retorno retorno.ret out/
```

| Layout | Covers | Source | Checked against |
|---|---|---|---|
| `cnab240` | Collection: segments P, Q, R (remessa), T, U (retorno) | FEBRABAN standard v10.1 | Sample remessas from eight banks; Sicredi's sample retorno |
| `cnab240_pagamentos` | Payments: segments A, B, J | FEBRABAN standard v10.1 | Synthetic file only |
| `cnab240_santander` | Santander collection, remessa and retorno | Santander H7815 v3.8 (Nov 2021) | Santander's sample remessa and retorno |
| `cnab400_bradesco_remessa`, `cnab400_bradesco_retorno` | Bradesco collection | Bradesco manual, March 2023 | Bradesco's sample remessa and retorno |
| `cnab400_itau_remessa`, `cnab400_itau_retorno` | Itaú collection | Itaú manual, January 2017 | Itaú's sample remessa and retorno |

The samples are public test fixtures of the brcobranca project, not production files. Before relying on a layout, run `check` on a real file from your bank and compare with the bank's current manual:

- Banks deviate from FEBRABAN. Santander shifts most of segment T, which is why it has its own layout.
- The Santander layout comes from the 2021 manual; the current edition could not be downloaded.
- CNAB 400 has no standard across banks, and remessa and retorno are separate layouts because the detail line carries no direction flag.
- Optional record types (CNAB 400 types 2 to 7, CNAB 240 segments not listed above) are not transcribed. They show up in `check` as lines that match no record.

To adapt one, copy the file from `src/gabarito/layouts/` and edit it. Every layout carries the `value` literals needed to write files as well as read them.

## S3

`s3://` paths work for reading, with globs, and for writing Parquet. Credentials live on the DuckDB connection:

```python
con.sql("CREATE SECRET (TYPE s3, PROVIDER credential_chain)")
gabarito.load("layout.yml", "s3://bucket/remessas/*.txt", con)
```

On the command line, `--init` runs SQL on the connection before the command:

```bash
gabarito --init "CREATE SECRET (TYPE s3, PROVIDER credential_chain)" \
  export layout.yml "s3://bucket/remessas/*.txt" s3://bucket/out
```

Tested against a local S3 (S3Mock) with a fixed key and custom endpoint. Not tested against AWS.

## Other databases

DuckDB only executes. Two ways out:

**Parquet.** `gabarito export` writes Parquet, which Redshift, Snowflake, BigQuery, Athena and Spark read directly.

**Straight through DuckDB.** It attaches Postgres and MySQL and writes to them (Postgres is exercised by [`examples/workflow`](examples/workflow); MySQL was not tested):

```python
con.sql("ATTACH 'postgresql://user:pass@host/db' AS pg (TYPE postgres)")
q = gabarito.sql("layout.yml", "file.txt")["detalhe"]
con.sql(f"CREATE TABLE pg.public.detalhe AS {q}")
```

## Large files and memory

No Python code touches the rows. The library builds SQL; DuckDB reads the file in streaming, in parallel, and spills to disk when memory runs out. So there is no chunking code here, and the memory ceiling is a connection setting:

```bash
gabarito --init "SET memory_limit='500MB'; SET threads=2" check layout.yml file.txt
```

`memory_limit` does not cover read buffers, which grow with the thread count. For a low ceiling, lower `threads` too.

To consume rows in Python in batches instead of all at once:

```python
cur = con.execute(gabarito.sql("layout.yml", "file.txt")["detalhe"])
while rows := cur.fetchmany(50_000):
    ...
```

Measured on synthetic CNAB 240 files, 16-thread Windows machine, DuckDB 1.5.6:

| File | Export one segment to Parquet | `check`, whole layout (216 fields) |
|---|---|---|
| 2 GB, 8.6 million lines | 6 s, 0.8 GB peak | 41 s, 0.9 GB peak |
| 2 GB, with `memory_limit='500MB'` and 2 threads | 28 s, 0.3 GB peak | 123 s, 0.2 GB peak |
| 7.3 GB, 30 million lines | 20 s, 1.3 GB peak | 146 s, 1.0 GB peak |

## Limits

- Single machine. For volumes that need a cluster, export Parquet and use another engine.
- `init` is a heuristic and there is no encoding detection.
- Line numbers in `check` follow DuckDB's read order.
- Tested with DuckDB 1.5 and 2.0, on Windows and Linux.

## License

MIT
