"""YAML layouts for fixed-width, CSV and Excel files, executed by DuckDB."""

from gabarito._sql import detect_encoding
from gabarito.check import check, rejects, rejects_sql
from gabarito.draft import init
from gabarito.layout import LayoutError, load_layout
from gabarito.read import load, sql
from gabarito.write import write

__all__ = [
    "LayoutError",
    "check",
    "detect_encoding",
    "init",
    "load",
    "load_layout",
    "rejects",
    "rejects_sql",
    "sql",
    "write",
]
