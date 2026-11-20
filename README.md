# remessa

A YAML file describes the file your client sends. DuckDB gives you typed tables.

Works with fixed-width files (several record types per file), CSV and Excel, from disk or S3, with nothing imported into a database first. Ships with a CNAB 240 layout.

[Português](README.pt-BR.md)

## Why

Companies that receive files from clients end up with one hand-written parser per client and a staging database full of untyped tables. The query engine was never the missing piece: DuckDB, Trino and Athena already join a file with a database. What is missing sits before the query:

- DuckDB has no fixed-width reader, and nothing handles files with header, detail and trailer records in one layout.
- A trailer that says "120 records" is usually parsed and never compared with the file.
- A value that fails to convert is logged, or silently becomes garbage.

`remessa` is the small layer that covers those three. The layout is data, the SQL is generated, and `check` tells you whether today's file matches the layout before anything is loaded.

## Try it

```bash
git clone <this repo> && cd remessa
pip install .
remessa check  examples/cobranca.yml examples/cobranca.txt
remessa export examples/cobranca.yml examples/cobranca.txt out/
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
import duckdb, remessa

con = duckdb.connect()
tables = remessa.load("examples/cobranca.yml", "examples/cobranca.txt", con)
detalhe = tables["detalhe"]
con.sql("SELECT nome, valor, vencimento FROM detalhe WHERE valor > 1000").show()
```

## Command line

```bash
remessa init   file.txt                     # draft a layout from a sample of the file
remessa check  layout.yml file.txt          # does the file match the layout?
remessa sql    layout.yml file.txt          # print the generated SQL
remessa export layout.yml file.txt out/     # one Parquet file per record
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
| `expect` | `count: <record or '*'>` must equal `equals: <record>.<field>` |
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

## CNAB 240

A ready-made layout for bank collection (cobrança): file and batch headers and trailers, segments P, Q and R (remessa), T and U (retorno).

```bash
remessa check  cnab240 retorno.ret
remessa export cnab240 retorno.ret out/
```

Positions follow the FEBRABAN standard layout, version 10.1. It was checked against sample remessa files from eight banks (public fixtures of the brcobranca project). Banks deviate from the standard: of the sample retorno files, Sicredi's passes as is and Santander's needs its own layout for segment T. Copy `src/remessa/layouts/cnab240.yml` and adjust.

CNAB 400 has no single standard across banks and is not included.

## S3

`s3://` paths work for reading, with globs, and for writing Parquet. Credentials live on the DuckDB connection:

```python
con.sql("CREATE SECRET (TYPE s3, PROVIDER credential_chain)")
remessa.load("layout.yml", "s3://bucket/remessas/*.txt", con)
```

On the command line, `--init` runs SQL on the connection before the command:

```bash
remessa --init "CREATE SECRET (TYPE s3, PROVIDER credential_chain)" \
  export layout.yml "s3://bucket/remessas/*.txt" s3://bucket/out
```

Tested against a local S3 (S3Mock) with a fixed key and custom endpoint. Not tested against AWS.

## Other databases

DuckDB only executes. Two ways out:

**Parquet.** `remessa export` writes Parquet, which Redshift, Snowflake, BigQuery, Athena and Spark read directly.

**Straight through DuckDB.** It attaches Postgres and MySQL and writes to them:

```python
con.sql("ATTACH 'postgresql://user:pass@host/db' AS pg (TYPE postgres)")
q = remessa.sql("layout.yml", "file.txt")["detalhe"]
con.sql(f"CREATE TABLE pg.public.detalhe AS {q}")
```

## Large files and memory

No Python code touches the rows. The library builds SQL; DuckDB reads the file in streaming, in parallel, and spills to disk when memory runs out. So there is no chunking code here, and the memory ceiling is a connection setting:

```bash
remessa --init "SET memory_limit='500MB'; SET threads=2" check layout.yml file.txt
```

`memory_limit` does not cover read buffers, which grow with the thread count. For a low ceiling, lower `threads` too.

To consume rows in Python in batches instead of all at once:

```python
cur = con.execute(remessa.sql("layout.yml", "file.txt")["detalhe"])
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
