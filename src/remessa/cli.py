import argparse
import sys
from pathlib import Path

import duckdb

from remessa.layout import LayoutError, _posix, check, init, sql


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="remessa", description="YAML layout -> typed tables in DuckDB.")
    ap.add_argument(
        "--init",
        metavar="SQL",
        dest="setup",
        help="SQL to run on the connection first (e.g. CREATE SECRET for S3, SET memory_limit)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (
        ("sql", "print the generated SQL for each record"),
        ("check", "check the file against the layout"),
        ("export", "write one Parquet file per record"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("layout", help="YAML path or built-in layout name (e.g. cnab240)")
        p.add_argument("file", help="file, glob or s3://...")
        if name == "export":
            p.add_argument("out", help="output directory")
    p = sub.add_parser("init", help="draft a layout from a sample of the file")
    p.add_argument("file")
    p.add_argument("--source", choices=("fixed", "csv", "xlsx"), help="default: guessed from the extension")
    p.add_argument("--encoding", default="latin-1")
    a = ap.parse_args(argv)
    # Redirected output on Windows uses the local code page and breaks non-ASCII text; UTF-8 everywhere.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        return _run(a)
    except (LayoutError, FileNotFoundError, duckdb.Error) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _run(a) -> int:
    con = duckdb.connect()
    if a.setup:
        con.execute(a.setup)

    if a.cmd == "init":
        print(init(a.file, a.source, a.encoding, con), end="")
        return 0
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
    if "://" not in out:  # s3://... has no directory to create
        Path(out).mkdir(parents=True, exist_ok=True)
    for name, q in sql(a.layout, a.file).items():
        target = f"{out}/{name}.parquet".replace("'", "''")
        con.sql(f"COPY ({q}) TO '{target}' (FORMAT parquet)")
        print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
