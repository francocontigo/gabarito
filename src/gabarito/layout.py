"""YAML layout -> DuckDB SQL. The layout describes the file; DuckDB reads and slices it.

No Python code touches the rows: every function here builds SQL, and DuckDB runs it
vectorized, in parallel and in streaming.
"""

from __future__ import annotations

import difflib
import os
import re
from importlib import resources
from pathlib import Path

import duckdb
import yaml

SOURCES = ("fixed", "csv", "xlsx")
TYPES = ("str", "int", "decimal", "date")
TOP_KEYS = ("source", "encoding", "records", "expect", "delimiter", "skip_rows", "decimal_comma", "sheet")
RECORD_KEYS = ("match", "length", "fields")
FIELD_KEYS = ("pos", "column", "type", "scale", "format", "value")


class LayoutError(ValueError):
    """The layout file is invalid."""


def _q(s) -> str:
    """SQL string literal."""
    return "'" + str(s).replace("'", "''") + "'"


def _posix(path) -> str:
    """Forward slashes on Windows; elsewhere a backslash is a valid file name character."""
    return str(path).replace("\\", "/") if os.sep == "\\" else str(path)


def _id(s) -> str:
    """SQL identifier."""
    return '"' + str(s).replace('"', '""') + '"'


def load_layout(layout) -> dict:
    """Accepts a dict, a YAML path or a built-in layout name (e.g. 'cnab240')."""
    if not isinstance(layout, dict):
        p = Path(layout)
        if not p.exists():
            builtin = resources.files("gabarito") / "layouts" / f"{layout}.yml"
            if not builtin.is_file():
                raise FileNotFoundError(f"layout not found: {layout}")
            p = builtin
        try:
            layout = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise LayoutError(f"{p}: invalid YAML: {e}") from e
    _validate(layout)
    return layout


def _known(keys, allowed, where: str) -> None:
    for k in keys:
        if k not in allowed:
            hint = difflib.get_close_matches(str(k), allowed, n=1)
            tip = f"did you mean {hint[0]!r}?" if hint else f"use {', '.join(allowed)}"
            raise LayoutError(f"{where}: unknown key {k!r} ({tip})")


def _validate(layout) -> None:
    if not isinstance(layout, dict):
        raise LayoutError("layout must be a mapping with a 'records' key")
    _known(layout, TOP_KEYS, "layout")
    src = layout.get("source", "fixed")
    if src not in SOURCES:
        raise LayoutError(f"invalid source {src!r} (use {', '.join(SOURCES)})")
    records = layout.get("records")
    if not isinstance(records, dict) or not records:
        raise LayoutError("layout has no 'records'")
    if src != "fixed" and len(records) != 1:
        raise LayoutError(f"source {src} takes exactly one record, got {len(records)}")
    for rname, rec in records.items():
        where = f"record {rname!r}"
        if not isinstance(rec, dict):
            raise LayoutError(f"{where}: must be a mapping with 'fields'")
        _known(rec, RECORD_KEYS, where)
        if src == "fixed":
            if not rec.get("match"):
                raise LayoutError(f"{where}: missing 'match' (regex that identifies the line, e.g. '^1')")
            try:
                re.compile(str(rec["match"]))
            except re.error as e:
                raise LayoutError(f"{where}: 'match' is not a valid regex: {e}") from e
        if not isinstance(rec.get("fields"), dict) or not rec["fields"]:
            raise LayoutError(f"{where}: has no 'fields'")
        for fname, f in rec["fields"].items():
            where = f"field {rname}.{fname}"
            f = f or {}
            if not isinstance(f, dict):
                raise LayoutError(f"{where}: must be a mapping, e.g. {{pos: [1, 10]}}")
            _known(f, FIELD_KEYS, where)
            t = f.get("type", "str")
            if t not in TYPES:
                raise LayoutError(f"{where}: invalid type {t!r} (use {', '.join(TYPES)})")
            if src == "fixed":
                pos = f.get("pos")
                ok = isinstance(pos, list) and len(pos) == 2 and all(isinstance(x, int) and x >= 1 for x in pos)
                if not ok:
                    raise LayoutError(f"{where}: 'pos' must be [start, length], start counted from 1")
                if rec.get("length") and pos[0] + pos[1] - 1 > rec["length"]:
                    raise LayoutError(f"{where}: ends at {pos[0] + pos[1] - 1}, past the record length {rec['length']}")
            if t == "date" and not f.get("format"):
                raise LayoutError(f"{where}: date needs 'format' (e.g. '%d%m%Y')")
    for rule in layout.get("expect") or []:
        if not isinstance(rule, dict) or set(rule) not in ({"count", "equals"}, {"sum", "equals"}):
            raise LayoutError("expect: each rule needs 'equals' and either 'count' or 'sum'")
        if "count" in rule and rule["count"] != "*" and rule["count"] not in records:
            raise LayoutError(f"expect: count refers to unknown record {rule['count']!r} (use a record name or '*')")
        for key in ("sum", "equals"):
            if key in rule:
                rname, _, fname = str(rule[key]).partition(".")
                if rname not in records or fname not in records[rname]["fields"]:
                    raise LayoutError(f"expect: {key} must be <record>.<field>, got {rule[key]!r}")


def _source(layout: dict, path) -> str:
    """SQL for the raw relation. Everything arrives as text; types come later."""
    src = layout.get("source", "fixed")
    p = _q(_posix(path))
    enc = _q(layout.get("encoding", "utf-8"))
    if src == "fixed":
        # sep='' and no quoting: the whole line becomes one column, whatever it contains.
        return (
            f"read_csv({p}, sep='', quote='', escape='', header=false, auto_detect=false, "
            f"columns={{'line': 'VARCHAR'}}, encoding={enc}, filename=true)"
        )
    if src == "csv":
        return (
            f"read_csv({p}, delim={_q(layout.get('delimiter', ';'))}, header=true, all_varchar=true, "
            f"skip={int(layout.get('skip_rows', 0))}, encoding={enc}, filename=true)"
        )
    sheet = f", sheet={_q(layout['sheet'])}" if layout.get("sheet") else ""
    return f"read_xlsx({p}, header=true, all_varchar=true{sheet})"


def _raw(layout: dict, fname: str, f: dict) -> str:
    """Field value as text: trimmed, empty becomes NULL."""
    if layout.get("source", "fixed") == "fixed":
        start, length = f["pos"]
        cut = f"substr(line, {int(start)}, {int(length)})"
    else:
        cut = _id(f.get("column", fname))
    raw = f"nullif(trim({cut}), '')"
    if f.get("type") == "date":
        # An all-zero date is how fixed-width files say "no date": NULL, not an error.
        raw = f"CASE WHEN ltrim({raw}, '0') = '' THEN NULL ELSE {raw} END"
    return raw


def _typed(layout: dict, raw: str, f: dict) -> str:
    t = f.get("type", "str")
    if t == "int":
        return f"TRY_CAST({raw} AS BIGINT)"
    if t == "decimal":
        scale = int(f.get("scale", 2))
        if layout.get("source", "fixed") == "fixed":
            # Implied decimal: '0000012345' with scale 2 is 123.45. DECIMAL multiplication is exact.
            unit = "0." + "0" * (scale - 1) + "1" if scale else "1"
            return f"CAST(TRY_CAST({raw} AS DECIMAL(18,0)) * {unit} AS DECIMAL(18,{scale}))"
        # A numeric Excel cell arrives as '1234.56'; a Brazilian CSV as '1.234,56'.
        if layout.get("decimal_comma", layout["source"] == "csv"):
            raw = f"replace(replace({raw}, '.', ''), ',', '.')"
        return f"TRY_CAST({raw} AS DECIMAL(18,{scale}))"
    if t == "date":
        return f"CAST(TRY_STRPTIME({raw}, {_q(f['format'])}) AS DATE)"
    return raw


def _where(rec: dict) -> str:
    return f" WHERE regexp_matches(line, {_q(rec['match'])})" if rec.get("match") else ""


def sql(layout, path) -> dict[str, str]:
    """One SELECT per record in the layout."""
    layout = load_layout(layout)
    src = _source(layout, path)
    file_col = "" if layout.get("source") == "xlsx" else "filename AS _file, "
    out = {}
    for rname, rec in layout["records"].items():
        cols = ", ".join(
            f"{_typed(layout, _raw(layout, n, f or {}), f or {})} AS {_id(n)}" for n, f in rec["fields"].items()
        )
        out[rname] = f"SELECT {file_col}{cols} FROM {src}{_where(rec)}"
    return out


def load(layout, path, con=None) -> dict[str, duckdb.DuckDBPyRelation]:
    """One lazy DuckDB relation per record. Nothing is read until you query it."""
    con = con or duckdb.connect()
    return {name: con.sql(q) for name, q in sql(layout, path).items()}


def check(layout, path, con=None) -> list[str]:
    """Compare the file with the layout. An empty list means the file matches."""
    layout = load_layout(layout)
    con = con or duckdb.connect()
    src = _source(layout, path)
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

    def is_(rec):
        return f"regexp_matches(line, {_q(rec['match'])})" if rec.get("match") else "TRUE"

    # Every count comes out of a single pass over the file.
    aggs = {("total",): "count(*)"}
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

    def field_of(ref):
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

    def lines_where(cond):
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


def rejects_sql(layout, path) -> str:
    """SELECT returning the lines that break the layout, each with the reasons."""
    layout = load_layout(layout)
    src = _source(layout, path)
    fixed = layout.get("source", "fixed") == "fixed"
    records = layout["records"]

    def is_(rec):
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


def rejects(layout, path, con=None) -> duckdb.DuckDBPyRelation:
    """Lazy relation with the lines that break the layout (see rejects_sql)."""
    return (con or duckdb.connect()).sql(rejects_sql(layout, path))


def write(layout, tables: dict[str, str], path, con=None, newline: str = "\r\n") -> int:
    """Write a fixed-width file from tables. Returns the number of lines written.

    `tables` maps record name -> SQL query or table name. Columns are matched to fields by
    name; a field with no column uses its `value`, or blanks. Records are written in layout
    order. To interleave records (CNAB: each P followed by its Q), give those queries an
    `_order` column: they are then merged by it.
    """
    layout = load_layout(layout)
    if layout.get("source", "fixed") != "fixed":
        raise LayoutError("write only produces fixed-width files; for CSV and Excel use DuckDB's COPY")
    con = con or duckdb.connect()
    records = layout["records"]
    unknown = set(tables) - set(records)
    if unknown:
        raise LayoutError(f"write: no record named {sorted(unknown)[0]!r} in the layout")

    names = list(records)
    ordered = [r for r in names if r in tables and "_order" in _columns(con, tables[r])]
    first, last = (names.index(ordered[0]), names.index(ordered[-1])) if ordered else (0, -1)
    parts, guards = [], {}
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


def _columns(con, table: str) -> set[str]:
    return {r[0].lower() for r in con.sql(f"DESCRIBE {_as_query(table)}").fetchall()}


def init(path, source=None, encoding="latin-1", con=None) -> str:
    """Draft a layout from a sample of the file. A starting point to review, not a final answer."""
    con = con or duckdb.connect()
    p = _posix(path)
    source = source or ("csv" if p.lower().endswith(".csv") else "xlsx" if p.lower().endswith(".xlsx") else "fixed")
    out = ["# Draft generated by `gabarito init`. Review names, types and positions.", f"source: {source}"]

    if source != "fixed":
        if source == "csv":
            delim = con.sql(f"SELECT Delimiter FROM sniff_csv({_q(p)})").fetchall()[0][0]
            layout = {"source": "csv", "delimiter": delim, "encoding": encoding}
            out += [f"encoding: {encoding}", f"delimiter: {_q(delim)}"]
        else:
            layout = {"source": "xlsx"}
        cols = [r[0] for r in con.sql(f"DESCRIBE SELECT * FROM {_source(layout, p)}").fetchall() if r[0] != "filename"]
        out += ["records:", "  rows:", "    fields:"]
        for c in cols:
            name = re.sub(r"\W+", "_", c.strip().lower()).strip("_") or "col"
            out.append(f"      {name}: {{column: {_q(c)}}}")
        return "\n".join(out) + "\n"

    src = _source({"source": "fixed", "encoding": encoding}, p)
    lines = [r[0] for r in con.sql(f"SELECT line FROM {src} LIMIT 1000").fetchall() if r[0]]
    if not lines:
        raise LayoutError(f"{path}: no lines to sample")
    # ponytail: record types are guessed from the first character only. Files that
    # discriminate elsewhere (CNAB 240 uses position 8) need the 'match' written by hand.
    firsts = sorted({ln[0] for ln in lines})
    groups = {c: [ln for ln in lines if ln[0] == c] for c in firsts} if 2 <= len(firsts) <= 10 else {None: lines}
    out += [f"encoding: {encoding}", "records:"]
    for i, (first, group) in enumerate(groups.items()):
        width = max(map(len, group))
        out += [
            f"  record_{first if first and first.isalnum() else i}:",
            f"    match: {_q('^' + re.escape(first)) if first else _q('.')}",
        ]
        if len({len(ln) for ln in group}) == 1:
            out.append(f"    length: {width}")
        out.append("    fields:")
        # Class of each position across the sample: all digits, all blank, or anything else.
        # A field is a run of positions with the same class.
        cls = [
            "d"
            if all(ln[j : j + 1].isdigit() for ln in group)
            else "s"
            if all(not ln[j : j + 1].strip() for ln in group)
            else "a"
            for j in range(width)
        ]
        start = 0
        for j in range(1, width + 1):
            if j == width or cls[j] != cls[start]:
                if cls[start] != "s":
                    note = "  # digits: int, decimal or date?" if cls[start] == "d" else ""
                    out.append(f"      f{start + 1}: {{pos: [{start + 1}, {j - start}]}}{note}")
                start = j
    return "\n".join(out) + "\n"
