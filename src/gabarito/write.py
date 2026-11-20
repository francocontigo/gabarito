"""Tables -> fixed-width file, using the same layout that reads one."""

from __future__ import annotations

import re

import duckdb

from gabarito._sql import _id, _q
from gabarito.layout import Connection, LayoutError, LayoutRef, PathRef, load_layout


def write(
    layout: LayoutRef,
    tables: dict[str, str],
    path: PathRef,
    con: Connection | None = None,
    newline: str = "\r\n",
) -> int:
    """Write a fixed-width file from tables. Returns the number of lines written.

    `tables` maps record name -> SQL query or table name. Columns are matched to fields by
    name; a field with no column uses its `value`, or blanks. Records are written in layout
    order. To interleave records (CNAB: each P followed by its Q), give those queries an
    `_order` column: they are then merged by it.
    """
    layout = load_layout(layout)
    if layout["source"] != "fixed":
        raise LayoutError("write only produces fixed-width files; for CSV and Excel use DuckDB's COPY")
    if layout.get("encoding") == "auto":
        raise LayoutError("write: `encoding: auto` only applies to reading; name the encoding to write")
    con = con or duckdb.connect()
    records = layout["records"]
    unknown = set(tables) - set(records)
    if unknown:
        raise LayoutError(f"write: no record named {sorted(unknown)[0]!r} in the layout")

    names = list(records)
    ordered = [r for r in names if r in tables and "_order" in _columns(con, tables[r])]
    first, last = (names.index(ordered[0]), names.index(ordered[-1])) if ordered else (0, -1)
    parts: list[str] = []
    guards: dict[str, tuple[str, str]] = {}
    for i, (rname, rec) in enumerate(records.items()):
        if rname not in tables:
            continue
        query = _as_query(tables[rname])
        cols = _columns(con, tables[rname])
        pieces, at = [], 1
        for n, f in sorted(rec["fields"].items(), key=lambda kv: kv[1]["pos"][0]):
            start, length = f["pos"]
            if start < at:
                raise LayoutError(f"write: field {rname}.{n} overlaps the previous field")
            if start > at:
                pieces.append(f"repeat(' ', {start - at})")
            x = _id(n) if n.lower() in cols else _q(f["value"]) if "value" in f else "NULL"
            t = f.get("type", "str")
            if t == "str":
                pieces.append(f"rpad(left(coalesce(CAST({x} AS VARCHAR), ''), {length}), {length}, ' ')")
            elif t == "date":
                text = f"strftime(CAST({x} AS DATE), {_q(f['format'])})"
                pieces.append(f"coalesce({text}, repeat('0', {length}))")
                guards[f"{rname}.{n}"] = (query, f"{x} IS NOT NULL AND length({text}) <> {length}")
            else:
                scale = int(f.get("scale", 2)) if t == "decimal" else 0
                # Exact: DECIMAL times a power of ten, then the digits. NULL is written as zeros.
                text = f"CAST(CAST(CAST(coalesce({x}, 0) AS DECIMAL(38,{scale})) * {10**scale} AS HUGEINT) AS VARCHAR)"
                pieces.append(f"lpad({text}, {length}, '0')")
                guards[f"{rname}.{n}"] = (query, f"NOT regexp_full_match({text}, '[0-9]{{1,{length}}}')")
            at = start + length
        if rec.get("length", at - 1) > at - 1:
            pieces.append(f"repeat(' ', {rec['length'] - at + 1})")
        stage = 1 if rname in ordered else 0 if i < first else 2 if i > last else 1
        order = "_order" if rname in ordered else "NULL"
        parts.append(
            f"SELECT {stage} AS s, {order} AS o, {i} AS r, row_number() OVER () AS n, "
            f"{' || '.join(pieces)} AS line FROM ({query})"
        )

    # A number that does not fit its field would be silently cut by lpad: refuse instead.
    for name, (query, cond) in guards.items():
        bad = con.sql(f"SELECT count(*) FROM ({query}) WHERE {cond}").fetchall()[0][0]
        if bad:
            raise ValueError(f"write: {bad} value(s) of {name} are negative or do not fit the field")

    cur = con.execute(f"SELECT line FROM ({' UNION ALL '.join(parts)}) ORDER BY s, o, r, n") if parts else None
    total = 0
    with open(path, "w", encoding=layout.get("encoding", "utf-8"), newline="") as out:
        while cur and (rows := cur.fetchmany(50_000)):  # streamed: memory stays flat on large tables
            out.write("".join(r[0] + newline for r in rows))
            total += len(rows)
    return total


def _as_query(table: str) -> str:
    return table if re.search(r"\s", table.strip()) else f"SELECT * FROM {table}"


def _columns(con: Connection, table: str) -> set[str]:
    return {r[0].lower() for r in con.sql(f"DESCRIBE {_as_query(table)}").fetchall()}
