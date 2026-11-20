"""Does the file match the layout? Counts in `check`, the offending lines in `rejects`."""

from __future__ import annotations

import duckdb

from gabarito._sql import _q, _raw, _source, _typed
from gabarito.layout import Connection, Layout, LayoutRef, PathRef, load_layout


def check(layout: LayoutRef, path: PathRef, con: Connection | None = None) -> list[str]:
    """Compare the file with the layout. An empty list means the file matches."""
    layout = load_layout(layout)
    con = con or duckdb.connect()
    src = _source(layout, path, con)
    fixed = layout.get("source", "fixed") == "fixed"
    records = layout["records"]
    problems: list[str] = []

    if not fixed:
        have = {r[0].lower() for r in con.sql(f"DESCRIBE SELECT * FROM {src}").fetchall()} - {"filename"}
        (rec,) = records.values()
        want = {str((f or {}).get("column", n)).lower() for n, f in rec["fields"].items()}
        if want - have:
            problems.append(f"missing columns: {', '.join(sorted(want - have))}")
        if have - want:
            problems.append(f"unexpected columns: {', '.join(sorted(have - want))}")
        if want - have:
            return problems  # without the columns the checks below cannot run

    def is_(rec: Layout) -> str:
        return f"regexp_matches(line, {_q(rec['match'])})" if rec.get("match") else "TRUE"

    # Every count comes out of a single pass over the file.
    aggs: dict[tuple[str, ...], str] = {("total",): "count(*)"}
    if fixed:
        unmatched = "NOT (" + " OR ".join(is_(r) for r in records.values()) + ")"
        aggs[("unmatched",)] = f"count(*) FILTER (WHERE {unmatched})"
    for rname, rec in records.items():
        aggs[("n", rname)] = f"count(*) FILTER (WHERE {is_(rec)})"
        if fixed and rec.get("length"):
            aggs[("len", rname)] = f"count(*) FILTER (WHERE {is_(rec)} AND length(line) <> {int(rec['length'])})"
        for n, f in rec["fields"].items():
            f = f or {}
            if f.get("type", "str") != "str":
                raw = _raw(layout, n, f)
                aggs[("bad", rname, n)] = (
                    f"count(*) FILTER (WHERE {is_(rec)} AND {raw} IS NOT NULL AND {_typed(layout, raw, f)} IS NULL)"
                )

    def field_of(ref: str) -> tuple[str, str]:
        rname, fname = ref.split(".")
        f = records[rname]["fields"][fname] or {}
        return _typed(layout, _raw(layout, fname, f), f), is_(records[rname])

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

    if got.get(("unmatched",)):
        problems.append(f"{got[('unmatched',)]} line(s) match no record (lines {lines_where(unmatched)})")
    for rname, rec in records.items():
        if got.get(("len", rname)):
            cond = f"{is_(rec)} AND length(line) <> {int(rec['length'])}"
            problems.append(
                f"{rname}: {got[('len', rname)]} line(s) with length other than {rec['length']} "
                f"(lines {lines_where(cond)})"
            )
    for rname, rec in records.items():
        for n, f in rec["fields"].items():
            if got.get(("bad", rname, n)):
                problems.append(f"{rname}.{n}: {got[('bad', rname, n)]} value(s) do not convert to {f['type']}")
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
    """SELECT returning the lines that break the layout, each with the reasons."""
    layout = load_layout(layout)
    src = _source(layout, path, con)
    fixed = layout.get("source", "fixed") == "fixed"
    records = layout["records"]

    def is_(rec: Layout) -> str:
        return f"regexp_matches(line, {_q(rec['match'])})" if rec.get("match") else "TRUE"

    cases = []
    if fixed:
        unmatched = "NOT (" + " OR ".join(is_(r) for r in records.values()) + ")"
        cases.append(f"CASE WHEN {unmatched} THEN 'matches no record' END")
    for rname, rec in records.items():
        if fixed and rec.get("length"):
            cases.append(
                f"CASE WHEN {is_(rec)} AND length(line) <> {int(rec['length'])} "
                f"THEN {_q(rname + ': length ')} || length(line) || {_q(', expected ' + str(rec['length']))} END"
            )
        for n, f in rec["fields"].items():
            f = f or {}
            if f.get("type", "str") != "str":
                raw = _raw(layout, n, f)
                cases.append(
                    f"CASE WHEN {is_(rec)} AND {raw} IS NOT NULL AND {_typed(layout, raw, f)} IS NULL "
                    f"THEN {_q(f'{rname}.{n}: not a valid ' + f['type'])} END"
                )
    reasons = f"concat_ws('; ', {', '.join(cases)})" if cases else "''"
    if fixed:
        # ponytail: same line-number caveat as check(); with a glob the numbering runs across files.
        inner = f"SELECT filename AS _file, row_number() OVER () AS _line, {reasons} AS reasons, line FROM {src}"
    else:
        inner = f"SELECT {reasons} AS reasons, * FROM {src}"
    return f"SELECT * FROM ({inner}) WHERE reasons <> ''"


def rejects(layout: LayoutRef, path: PathRef, con: Connection | None = None) -> duckdb.DuckDBPyRelation:
    """Lazy relation with the lines that break the layout (see rejects_sql)."""
    con = con or duckdb.connect()
    return con.sql(rejects_sql(layout, path, con))
