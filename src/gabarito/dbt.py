"""dbt-duckdb plugin: each table of a dbt source is a record of a gabarito layout.

Setup and options are in the README, section "With dbt".
"""

from __future__ import annotations

import threading
from typing import Any

import duckdb
from dbt.adapters.duckdb.plugins import BasePlugin
from dbt.adapters.duckdb.utils import SourceConfig

from gabarito.check import check
from gabarito.layout import LayoutError
from gabarito.read import sql


class Plugin(BasePlugin):  # type: ignore[misc]  # dbt is not installed when mypy runs
    def initialize(self, plugin_config: dict[str, Any]) -> None:
        self._local = threading.local()

    def configure_cursor(self, cursor: duckdb.DuckDBPyConnection) -> None:
        # dbt-duckdb registers what load() returns on the cursor it has just created, and a
        # DuckDB relation only works on the connection that made it. This hook sees that cursor
        # first, on the same thread.
        self._local.cursor = cursor

    def load(self, source_config: SourceConfig) -> duckdb.DuckDBPyRelation:
        con: duckdb.DuckDBPyConnection = self._local.cursor
        meta = source_config.meta
        layout, path = meta["layout"], meta["path"]
        record = source_config.identifier
        if meta.get("check"):
            problems = check(layout, path, con)
            if problems:
                raise LayoutError(f"{path} does not match {layout}: " + "; ".join(problems))
        queries = sql(layout, path, con)
        if record not in queries:
            raise LayoutError(f"{layout} has no record {record!r} (it has {', '.join(queries)})")
        return con.sql(queries[record])
