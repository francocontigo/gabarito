"""Layout YAML -> SQL do DuckDB. O layout descreve o arquivo; o DuckDB lê e fatia."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import duckdb
import yaml

SOURCES = ("fixed", "csv", "xlsx")
TYPES = ("str", "int", "decimal", "date")


def _q(s) -> str:
    """Literal de string SQL."""
    return "'" + str(s).replace("'", "''") + "'"


def _id(s) -> str:
    """Identificador SQL."""
    return '"' + str(s).replace('"', '""') + '"'


def load_layout(layout) -> dict:
    """Aceita dict, caminho de YAML ou nome de layout embutido (ex.: 'cnab240')."""
    if not isinstance(layout, dict):
        p = Path(layout)
        if not p.exists():
            builtin = resources.files("remessa") / "layouts" / f"{layout}.yml"
            if not builtin.is_file():
                raise FileNotFoundError(f"layout não encontrado: {layout}")
            p = builtin
        layout = yaml.safe_load(p.read_text(encoding="utf-8"))
    _validate(layout)
    return layout


def _validate(layout: dict) -> None:
    src = layout.get("source", "fixed")
    if src not in SOURCES:
        raise ValueError(f"source inválido: {src!r} (use {', '.join(SOURCES)})")
    records = layout.get("records")
    if not records:
        raise ValueError("layout sem 'records'")
    if src != "fixed" and len(records) != 1:
        raise ValueError(f"source {src} aceita um único registro")
    for rname, rec in records.items():
        if src == "fixed" and not rec.get("match"):
            raise ValueError(f"registro {rname!r}: falta 'match' (regex que identifica a linha)")
        if not rec.get("fields"):
            raise ValueError(f"registro {rname!r}: sem 'fields'")
        for fname, f in rec["fields"].items():
            where = f"campo {rname}.{fname}"
            f = f or {}
            t = f.get("type", "str")
            if t not in TYPES:
                raise ValueError(f"{where}: type inválido {t!r} (use {', '.join(TYPES)})")
            if src == "fixed":
                pos = f.get("pos")
                if not (isinstance(pos, list) and len(pos) == 2 and pos[0] >= 1 and pos[1] >= 1):
                    raise ValueError(f"{where}: 'pos' deve ser [início a partir de 1, tamanho]")
            if t == "date" and not f.get("format"):
                raise ValueError(f"{where}: date exige 'format' (ex.: '%d%m%Y')")


def _source(layout: dict, path) -> str:
    """SQL da relação bruta. Tudo chega como texto; os tipos vêm depois."""
    src = layout.get("source", "fixed")
    p = _q(str(path).replace("\\", "/"))
    enc = _q(layout.get("encoding", "utf-8"))
    if src == "fixed":
        # sep='' + sem aspas: a linha inteira vira uma coluna, qualquer que seja o conteúdo.
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
    """Valor do campo como texto: sem espaços nas pontas, vazio vira NULL."""
    if layout.get("source", "fixed") == "fixed":
        start, length = f["pos"]
        cut = f"substr(line, {int(start)}, {int(length)})"
    else:
        cut = _id(f.get("column", fname))
    raw = f"nullif(trim({cut}), '')"
    if f.get("type") == "date":
        # Data toda em zeros é o "sem data" dos arquivos posicionais: vira NULL, não erro.
        raw = f"CASE WHEN ltrim({raw}, '0') = '' THEN NULL ELSE {raw} END"
    return raw


def _typed(layout: dict, raw: str, f: dict) -> str:
    t = f.get("type", "str")
    if t == "int":
        return f"TRY_CAST({raw} AS BIGINT)"
    if t == "decimal":
        scale = int(f.get("scale", 2))
        if layout.get("source", "fixed") == "fixed":
            # Decimal implícito: '0000012345' com scale 2 é 123.45. Multiplicação de DECIMAL é exata.
            unit = "0." + "0" * (scale - 1) + "1" if scale else "1"
            return f"CAST(TRY_CAST({raw} AS DECIMAL(18,0)) * {unit} AS DECIMAL(18,{scale}))"
        # Célula numérica de Excel chega como '1234.56'; CSV brasileiro, como '1.234,56'.
        if layout.get("decimal_comma", layout["source"] == "csv"):
            raw = f"replace(replace({raw}, '.', ''), ',', '.')"
        return f"TRY_CAST({raw} AS DECIMAL(18,{scale}))"
    if t == "date":
        return f"CAST(TRY_STRPTIME({raw}, {_q(f['format'])}) AS DATE)"
    return raw


def _where(rec: dict) -> str:
    return f" WHERE regexp_matches(line, {_q(rec['match'])})" if rec.get("match") else ""


def sql(layout, path) -> dict[str, str]:
    """Um SELECT por registro do layout."""
    layout = load_layout(layout)
    src = _source(layout, path)
    file_col = "" if layout.get("source") == "xlsx" else "filename AS _file, "
    out = {}
    for rname, rec in layout["records"].items():
        cols = ", ".join(
            f"{_typed(layout, _raw(layout, n, f or {}), f or {})} AS {_id(n)}"
            for n, f in rec["fields"].items()
        )
        out[rname] = f"SELECT {file_col}{cols} FROM {src}{_where(rec)}"
    return out


def load(layout, path, con=None) -> dict[str, duckdb.DuckDBPyRelation]:
    """Uma relação DuckDB por registro."""
    con = con or duckdb.connect()
    return {name: con.sql(q) for name, q in sql(layout, path).items()}


def check(layout, path, con=None) -> list[str]:
    """Compara o arquivo com o layout. Lista vazia = arquivo bate."""
    layout = load_layout(layout)
    con = con or duckdb.connect()
    src = _source(layout, path)
    fixed = layout.get("source", "fixed") == "fixed"
    records = layout["records"]
    problems: list[str] = []

    def one(q):
        return con.sql(q).fetchone()[0]

    if fixed:
        # ponytail: row_number() sem ORDER BY segue a ordem do arquivo na prática
        # (preserve_insertion_order); usado só para apontar linhas no relatório.
        con.sql(f"CREATE OR REPLACE TEMP VIEW _remessa_lines AS SELECT line, row_number() OVER () AS n FROM {src}")
        lines = "_remessa_lines"

        def sample(cond):
            rows = con.sql(f"SELECT n FROM {lines} WHERE {cond} ORDER BY n LIMIT 5").fetchall()
            return ", ".join(str(r[0]) for r in rows)

        any_match = " OR ".join(f"regexp_matches(line, {_q(r['match'])})" for r in records.values())
        n = one(f"SELECT count(*) FROM {lines} WHERE NOT ({any_match})")
        if n:
            problems.append(f"{n} linha(s) não casam com nenhum registro (linhas {sample(f'NOT ({any_match})')})")
        for rname, rec in records.items():
            if rec.get("length"):
                cond = f"regexp_matches(line, {_q(rec['match'])}) AND length(line) <> {int(rec['length'])}"
                n = one(f"SELECT count(*) FROM {lines} WHERE {cond}")
                if n:
                    problems.append(
                        f"{rname}: {n} linha(s) com tamanho diferente de {rec['length']} (linhas {sample(cond)})"
                    )
    else:
        have = {r[0].lower() for r in con.sql(f"DESCRIBE SELECT * FROM {src}").fetchall()} - {"filename"}
        (rec,) = records.values()
        want = {str((f or {}).get("column", n)).lower() for n, f in rec["fields"].items()}
        if want - have:
            problems.append(f"colunas faltando: {', '.join(sorted(want - have))}")
        if have - want:
            problems.append(f"colunas a mais: {', '.join(sorted(have - want))}")
        if want - have:
            return problems  # sem as colunas, as checagens seguintes não rodam
        lines = src

    for rname, rec in records.items():
        typed = {n: f for n, f in rec["fields"].items() if (f or {}).get("type", "str") != "str"}
        if not typed:
            continue
        counts = ", ".join(
            f"count(*) FILTER (WHERE {_raw(layout, n, f)} IS NOT NULL AND {_typed(layout, _raw(layout, n, f), f)} IS NULL)"
            for n, f in typed.items()
        )
        row = con.sql(f"SELECT {counts} FROM {lines}{_where(rec)}").fetchone()
        for (n, f), bad in zip(typed.items(), row):
            if bad:
                problems.append(f"{rname}.{n}: {bad} valor(es) não convertem para {f['type']}")

    for rule in layout.get("expect", []):
        target = rule["count"]
        where = "" if target == "*" else _where(records[target])
        got = one(f"SELECT count(*) FROM {lines}{where}")
        rname, fname = rule["equals"].split(".")
        rec = records[rname]
        f = rec["fields"][fname] or {}
        vals = con.sql(f"SELECT {_typed(layout, _raw(layout, fname, f), f)} FROM {lines}{_where(rec)}").fetchall()
        label = "linhas" if target == "*" else target
        if len(vals) != 1:
            problems.append(f"expect: esperava 1 registro {rname}, achei {len(vals)}")
        elif vals[0][0] != got:
            problems.append(f"expect: {rule['equals']} diz {vals[0][0]}, mas o arquivo tem {got} {label}")
    return problems
