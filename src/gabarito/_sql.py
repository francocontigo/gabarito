"""SQL fragments shared by reading, checking and writing, and the encoding detection they need."""

from __future__ import annotations

import os

import duckdb

from gabarito.layout import Connection, Layout, LayoutError, PathRef

ENCODINGS = ("utf-8", "latin-1", "CP1252")


def _q(s: object) -> str:
    """SQL string literal."""
    return "'" + str(s).replace("'", "''") + "'"


def _posix(path: PathRef) -> str:
    """Forward slashes on Windows; elsewhere a backslash is a valid file name character."""
    return str(path).replace("\\", "/") if os.sep == "\\" else str(path)


def _id(s: object) -> str:
    """SQL identifier."""
    return '"' + str(s).replace('"', '""') + '"'


def detect_encoding(path: PathRef, con: Connection | None = None) -> str:
    """The first of utf-8, latin-1 (ISO-8859-1) and CP1252 that decodes the whole file.

    DuckDB rejects a file that is not valid in the encoding it was told, so this is a scan per
    attempt. The order matters: CP1252 accepts almost any bytes, UTF-8 almost only UTF-8.
    With a glob every file is tested on its own and they must agree.
    """
    con = con or duckdb.connect()
    p = _posix(path)
    # A path that matches nothing is tested as it is, so the reader gives its own "not found".
    files = [r[0] for r in con.execute(f"SELECT file FROM glob({_q(p)})").fetchall()] or [p]
    found: dict[str, str] = {}
    for file in files:
        for enc in ENCODINGS:
            try:
                con.execute(
                    f"SELECT count(*) FROM read_csv({_q(file)}, sep='', quote='', escape='', header=false, "
                    f"auto_detect=false, columns={{'line': 'VARCHAR'}}, encoding={_q(enc)})"
                ).fetchall()
            except duckdb.InvalidInputException:
                continue
            found.setdefault(enc, file)
            break
        else:
            raise LayoutError(f"{file}: not {', '.join(ENCODINGS)}; set `encoding` in the layout")
    if len(found) > 1:
        # ponytail: one encoding per call. Reading each file in its own would need a UNION per file.
        mix = ", ".join(f"{file} is {enc}" for enc, file in found.items())
        raise LayoutError(f"{path}: files in different encodings ({mix}); read them separately")
    return next(iter(found))


def _source(layout: Layout, path: PathRef, con: Connection | None = None) -> str:
    """SQL for the raw relation. Everything arrives as text; types come later."""
    src = layout.get("source", "fixed")
    p = _q(_posix(path))
    enc = layout.get("encoding", "utf-8")
    if enc == "auto" and src != "xlsx":
        enc = detect_encoding(path, con)
    enc = _q(enc)
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


def _raw(layout: Layout, fname: str, f: Layout) -> str:
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


def _typed(layout: Layout, raw: str, f: Layout) -> str:
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


def _where(rec: Layout) -> str:
    return f" WHERE regexp_matches(line, {_q(rec['match'])})" if rec.get("match") else ""
