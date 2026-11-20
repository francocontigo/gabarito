import argparse
import sys
from pathlib import Path

import duckdb

from remessa.layout import _posix, check, sql


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="remessa", description="Layout YAML -> tabelas no DuckDB.")
    ap.add_argument("--init", metavar="SQL", help="SQL rodado na conexão antes de tudo (ex.: CREATE SECRET para S3)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (
        ("sql", "imprime o SQL gerado para cada registro"),
        ("check", "confere o arquivo contra o layout"),
        ("export", "grava um Parquet por registro"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("layout", help="caminho do YAML ou nome de layout embutido (ex.: cnab240)")
        p.add_argument("file", help="arquivo, glob ou s3://...")
        if name == "export":
            p.add_argument("out", help="pasta de saída")
    a = ap.parse_args(argv)
    # Saída redirecionada no Windows usa a codepage local e quebra acentos; UTF-8 em todo lugar.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    con = duckdb.connect()
    if a.init:
        con.execute(a.init)

    if a.cmd == "sql":
        for name, q in sql(a.layout, a.file).items():
            print(f"-- {name}\n{q};\n")
        return 0
    if a.cmd == "check":
        problems = check(a.layout, a.file, con)
        for p in problems:
            print(p)
        if not problems:
            print("ok")
        return 1 if problems else 0
    out = _posix(a.out).rstrip("/")
    if "://" not in out:  # s3://... não tem pasta para criar
        Path(out).mkdir(parents=True, exist_ok=True)
    for name, q in sql(a.layout, a.file).items():
        target = f"{out}/{name}.parquet".replace("'", "''")
        con.sql(f"COPY ({q}) TO '{target}' (FORMAT parquet)")
        print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
