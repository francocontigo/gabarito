"""The layout: what a YAML file may say, and the errors when it says something else."""

from __future__ import annotations

import difflib
import os
import re
from collections.abc import Iterable
from importlib import resources
from pathlib import Path
from typing import Any

import duckdb
import yaml

Layout = dict[str, Any]
LayoutRef = Layout | str | os.PathLike[str]  # a parsed layout, a YAML path, or a built-in name
PathRef = str | os.PathLike[str]
Connection = duckdb.DuckDBPyConnection
SOURCES = ("fixed", "csv", "xlsx")
TYPES = ("str", "int", "decimal", "date")
TOP_KEYS = ("source", "encoding", "records", "expect", "delimiter", "skip_rows", "decimal_comma", "sheet")
RECORD_KEYS = ("match", "length", "fields")
FIELD_KEYS = ("pos", "column", "type", "scale", "format", "value")


class LayoutError(ValueError):
    """The layout file is invalid."""


def load_layout(layout: LayoutRef) -> Layout:
    """Accepts a dict, a YAML path or a built-in layout name (e.g. 'cnab240')."""
    parsed: object = layout
    if isinstance(layout, (str, os.PathLike)):
        path = Path(layout)
        builtin = resources.files("gabarito") / "layouts" / f"{layout}.yml"
        if path.exists():
            text = path.read_text(encoding="utf-8")
        elif builtin.is_file():
            text = builtin.read_text(encoding="utf-8")
        else:
            raise FileNotFoundError(f"layout not found: {layout}")
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise LayoutError(f"{layout}: invalid YAML: {e}") from e
    return _validate(parsed)


def _known(keys: Iterable[object], allowed: tuple[str, ...], where: str) -> None:
    for k in keys:
        if k not in allowed:
            hint = difflib.get_close_matches(str(k), allowed, n=1)
            tip = f"did you mean {hint[0]!r}?" if hint else f"use {', '.join(allowed)}"
            raise LayoutError(f"{where}: unknown key {k!r} ({tip})")


def _validate(layout: object) -> Layout:
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
                if not (isinstance(pos, list) and len(pos) == 2 and all(isinstance(x, int) and x >= 1 for x in pos)):
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
    return layout
