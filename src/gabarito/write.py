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
    selects: list[str] = []  # run one after the other, each streamed to the file
    merged: list[str] = []  # the records interleaved by _order, and any record between them
    merge_at = 0
    guards: dict[str, tuple[str, dict[str, str]]] = {}
    for i, (rname, rec) in enumerate(records.items()):
        if rname not in tables:
            continue
        query = _as_query(tables[rname])
        cols = _columns(con, tables[rname])
        bad = guards.setdefault(rname, (query, {}))[1]
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
                # A line break inside a value would split the record in two: it becomes a space.
                text = f"regexp_replace(coalesce(CAST({x} AS VARCHAR), ''), '[\\r\\n]', ' ', 'g')"
                pieces.append(f"rpad({text}, {length}, ' ')")
                bad[n] = f"length(rtrim({text})) > {length}"  # trailing blanks are padding, not content
            elif t == "date":
                text = f"strftime(CAST({x} AS DATE), {_q(f['format'])})"
                pieces.append(f"coalesce({text}, repeat('0', {length}))")
                bad[n] = f"{x} IS NOT NULL AND length({text}) <> {length}"
            else:
                scale = int(f.get("scale", 2)) if t == "decimal" else 0
                # Exact: DECIMAL times a power of ten, then the digits. NULL is written as zeros.
                text = f"CAST(CAST(CAST(coalesce({x}, 0) AS DECIMAL(38,{scale})) * {10**scale} AS HUGEINT) AS VARCHAR)"
                pieces.append(f"lpad({text}, {length}, '0')")
                bad[n] = f"NOT regexp_full_match({text}, '[0-9]{{1,{length}}}')"
            at = start + length
        if rec.get("length", at - 1) > at - 1:
            pieces.append(f"repeat(' ', {rec['length'] - at + 1})")
        line = " || ".join(pieces)
        if first <= i <= last:
            order = "_order" if rname in ordered else "NULL"
            merge_at = merge_at if merged else len(selects)
            merged.append(f"SELECT {order} AS o, {i} AS r, row_number() OVER () AS n, {line} AS line FROM ({query})")
        else:
            # ponytail: no ORDER BY here, the lines come out in the order of the query
            # (preserve_insertion_order), and nothing is sorted or held in memory.
            selects.append(f"SELECT {line} AS line FROM ({query})")
    if merged:
        # Interleaving is a sort over all those lines: it needs memory, or temp space, for them.
        selects.insert(merge_at, f"SELECT line FROM ({' UNION ALL '.join(merged)}) ORDER BY o, r, n")

    # A value that does not fit its field would be silently cut by the padding: refuse instead.
    # One pass per record, with a count per field.
    for rname, (query, conds) in guards.items():
        if not conds:
            continue
        counts = ", ".join(f"count(*) FILTER (WHERE {cond})" for cond in conds.values())
        for n, count in zip(conds, con.sql(f"SELECT {counts} FROM ({query})").fetchall()[0], strict=True):
            if count:
                width = records[rname]["fields"][n]["pos"][1]
                raise ValueError(
                    f"write: {count} value(s) of {rname}.{n} do not fit the {width} characters of the field "
                    "(too long, or a negative number); shorten them in the query, e.g. left(col, n)"
                )

    total = 0
    with open(path, "w", encoding=layout.get("encoding", "utf-8"), newline="") as out:
        for select in selects:
            cur = con.execute(select)
            while rows := cur.fetchmany(50_000):
                out.write("".join(r[0] + newline for r in rows))
                total += len(rows)
    return total


def _as_query(table: str) -> str:
    return table if re.search(r"\s", table.strip()) else f"SELECT * FROM {table}"


def _columns(con: Connection, table: str) -> set[str]:
    return {r[0].lower() for r in con.sql(f"DESCRIBE {_as_query(table)}").fetchall()}
