import argparse
import sys
from pathlib import Path

import duckdb

from remessa.layout import check, sql


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="remessa", description="Layout YAML -> tabelas no DuckDB.")
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

    if a.cmd == "sql":
        for name, q in sql(a.layout, a.file).items():
            print(f"-- {name}\n{q};\n")
        return 0
    if a.cmd == "check":
        problems = check(a.layout, a.file)
        for p in problems:
            print(p)
        if not problems:
            print("ok")
        return 1 if problems else 0
    out = a.out.replace("\\", "/").rstrip("/")
    if "://" not in out:  # s3://... não tem pasta para criar
        Path(out).mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    for name, q in sql(a.layout, a.file).items():
        target = f"{out}/{name}.parquet".replace("'", "''")
        con.sql(f"COPY ({q}) TO '{target}' (FORMAT parquet)")
        print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
