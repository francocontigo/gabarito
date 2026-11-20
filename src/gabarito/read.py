"""Layout + file -> one SELECT (or lazy relation) per record.

No Python code touches the rows: this builds SQL, and DuckDB runs it vectorized,
in parallel and in streaming.
"""

from __future__ import annotations

import duckdb

from gabarito._sql import _id, _raw, _source, _typed, _where
from gabarito.layout import Connection, LayoutRef, PathRef, load_layout


def sql(layout: LayoutRef, path: PathRef, con: Connection | None = None) -> dict[str, str]:
    """One SELECT per record in the layout. `con` is only used by `encoding: auto`, to read the file."""
    layout = load_layout(layout)
    src = _source(layout, path, con)
    file_col = "" if layout.get("source") == "xlsx" else "filename AS _file, "
    out = {}
    for rname, rec in layout["records"].items():
        cols = ", ".join(
            f"{_typed(layout, _raw(layout, n, f or {}), f or {})} AS {_id(n)}" for n, f in rec["fields"].items()
        )
        out[rname] = f"SELECT {file_col}{cols} FROM {src}{_where(rec)}"
    return out


def load(layout: LayoutRef, path: PathRef, con: Connection | None = None) -> dict[str, duckdb.DuckDBPyRelation]:
    """One lazy DuckDB relation per record. Nothing is read until you query it."""
    con = con or duckdb.connect()
    return {name: con.sql(q) for name, q in sql(layout, path, con).items()}
