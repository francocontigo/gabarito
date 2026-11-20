"""Does the file match the layout? Counts in `check`, the offending lines in `rejects_sql`."""

from __future__ import annotations

import duckdb

from gabarito._sql import _is, _q, _raw, _source, _typed
from gabarito.layout import Connection, Layout, LayoutRef, PathRef, load_layout

Key = tuple[str, ...]


def _breaks(layout: Layout) -> dict[Key, tuple[str, str]]:
    """Each way a line can break the layout: key -> (SQL condition, SQL text of the reason)."""
    fixed = layout["source"] == "fixed"
    records = layout["records"]
    out: dict[Key, tuple[str, str]] = {}
    if fixed:
        out[("unmatched",)] = ("NOT (" + " OR ".join(map(_is, records.values())) + ")", "'matches no record'")
    for rname, rec in records.items():
        if fixed and rec.get("length"):
            out[("len", rname)] = (
                f"{_is(rec)} AND length(line) <> {int(rec['length'])}",
                f"{_q(rname + ': length ')} || length(line) || {_q(', expected ' + str(rec['length']))}",
            )
        for n, f in rec["fields"].items():
            if f.get("type", "str") != "str":
                raw = _raw(layout, n, f)
                out[("bad", rname, n)] = (
                    f"{_is(rec)} AND {raw} IS NOT NULL AND {_typed(layout, raw, f)} IS NULL",
                    _q(f"{rname}.{n}: not a valid {f['type']}"),
                )
    return out


def check(layout: LayoutRef, path: PathRef, con: Connection | None = None) -> list[str]:
    """Compare the file with the layout. An empty list means the file matches."""
    layout = load_layout(layout)
    con = con or duckdb.connect()
    src = _source(layout, path, con)
    records = layout["records"]
    problems: list[str] = []

    if layout["source"] != "fixed":
        have = {r[0].lower() for r in con.sql(f"DESCRIBE SELECT * FROM {src}").fetchall()} - {"filename"}
        (rec,) = records.values()
        want = {str(f.get("column", n)).lower() for n, f in rec["fields"].items()}
        if want - have:
            problems.append(f"missing columns: {', '.join(sorted(want - have))}")
        if have - want:
            problems.append(f"unexpected columns: {', '.join(sorted(have - want))}")
        if want - have:
            return problems  # without the columns the checks below cannot run

    # Every count comes out of a single pass over the file.
    breaks = _breaks(layout)
    aggs: dict[Key, str] = {("total",): "count(*)"}
    aggs |= {("n", rname): f"count(*) FILTER (WHERE {_is(rec)})" for rname, rec in records.items()}
    aggs |= {key: f"count(*) FILTER (WHERE {cond})" for key, (cond, _) in breaks.items()}

    def field_of(ref: str) -> tuple[str, str]:
        rname, fname = ref.split(".")
        f = records[rname]["fields"][fname]
        return _typed(layout, _raw(layout, fname, f), f), _is(records[rname])

    for rule in layout.get("expect") or []:
        expr, cond = field_of(rule["equals"])
        aggs[("val", rule["equals"])] = f"any_value({expr}) FILTER (WHERE {cond})"
        if "sum" in rule:
            expr, cond = field_of(rule["sum"])
            aggs[("sum", rule["sum"])] = f"coalesce(sum({expr}) FILTER (WHERE {cond}), 0)"
    got = dict(zip(aggs, con.sql(f"SELECT {', '.join(aggs.values())} FROM {src}").fetchall()[0], strict=True))

    def lines_where(cond: str) -> str:
        # ponytail: row_number() without ORDER BY follows file order in practice
        # (preserve_insertion_order). Only runs when there is a problem to point at.
        rows = con.sql(
            f"SELECT n FROM (SELECT line, row_number() OVER () AS n FROM {src}) WHERE {cond} ORDER BY n LIMIT 5"
        ).fetchall()
        return ", ".join(str(r[0]) for r in rows)

    for kind in ("unmatched", "len", "bad"):  # reported in this order
        for key, (cond, _) in breaks.items():
            n = got[key]
            if key[0] != kind or not n:
                continue
            if kind == "unmatched":
                problems.append(f"{n} line(s) match no record (lines {lines_where(cond)})")
            elif kind == "len":
                length = records[key[1]]["length"]
                problems.append(f"{key[1]}: {n} line(s) with length other than {length} (lines {lines_where(cond)})")
            else:
                type_ = records[key[1]]["fields"][key[2]]["type"]
                problems.append(f"{key[1]}.{key[2]}: {n} value(s) do not convert to {type_}")
    for rule in layout.get("expect") or []:
        rname = rule["equals"].split(".")[0]
        said = got[("val", rule["equals"])]
        if got[("n", rname)] != 1:
            problems.append(f"expect: expected exactly 1 {rname} record, found {got[('n', rname)]}")
        elif "sum" in rule:
            total = got[("sum", rule["sum"])]
            if said != total:
                problems.append(f"expect: {rule['equals']} says {said}, but {rule['sum']} adds up to {total}")
        else:
            target = rule["count"]
            count = got[("total",)] if target == "*" else got[("n", target)]
            label = "lines" if target == "*" else f"{target} record(s)"
            if said != count:
                problems.append(f"expect: {rule['equals']} says {said}, but the file has {count} {label}")
    return problems


def rejects_sql(layout: LayoutRef, path: PathRef, con: Connection | None = None) -> str:
    """SELECT returning the lines that break the layout, each with the reasons.

    Run it with `con.sql(...)`, or wrap it in a COPY to save the lines.
    """
    layout = load_layout(layout)
    src = _source(layout, path, con)
    cases = [f"CASE WHEN {cond} THEN {reason} END" for cond, reason in _breaks(layout).values()]
    reasons = f"concat_ws('; ', {', '.join(cases)})" if cases else "''"
    if layout["source"] == "fixed":
        # ponytail: same line-number caveat as check(); with a glob the numbering runs across files.
        inner = f"SELECT filename AS _file, row_number() OVER () AS _line, {reasons} AS reasons, line FROM {src}"
    else:
        inner = f"SELECT {reasons} AS reasons, * FROM {src}"
    return f"SELECT * FROM ({inner}) WHERE reasons <> ''"
