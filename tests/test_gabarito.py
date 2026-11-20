import datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
import yaml

import gabarito
from gabarito.cli import main

LAYOUT = {
    "source": "fixed",
    "encoding": "latin-1",
    "records": {
        "header": {
            "match": "^0",
            "fields": {"data_geracao": {"pos": [2, 8], "type": "date", "format": "%d%m%Y"}},
        },
        "detalhe": {
            "match": "^1",
            "length": 50,
            "fields": {
                "nome": {"pos": [2, 20]},
                "valor": {"pos": [22, 13], "type": "decimal", "scale": 2},
                "vencimento": {"pos": [35, 8], "type": "date", "format": "%Y%m%d"},
                "obs": {"pos": [43, 8]},
            },
        },
        "trailer": {"match": "^9", "fields": {"qtd": {"pos": [2, 6], "type": "int"}}},
    },
    "expect": [{"count": "detalhe", "equals": "trailer.qtd"}],
}


def detalhe(nome, valor, venc, obs=""):
    return "1" + nome.ljust(20) + valor.rjust(13, "0") + venc + obs.ljust(8)


def write(tmp_path, lines, name="arquivo.txt"):
    p = tmp_path / name
    # latin-1 + CRLF, the way legacy files usually arrive
    p.write_bytes("\r\n".join(lines).encode("latin-1") + b"\r\n")
    return p


@pytest.fixture
def good(tmp_path):
    return write(
        tmp_path,
        [
            "001022026",
            detalhe('JOÃO;"da",Silva', "123456", "20260315", "a|b\tc"),
            detalhe("MARIA", "5", "00000000"),
            "9000002",
        ],
    )


def test_types(good):
    rels = gabarito.load(LAYOUT, good)
    assert rels["header"].fetchall()[0][1] == datetime.date(2026, 2, 1)
    rows = rels["detalhe"].project("nome, valor, vencimento, obs").fetchall()
    assert rows == [
        ('JOÃO;"da",Silva', Decimal("1234.56"), datetime.date(2026, 3, 15), "a|b\tc"),
        ("MARIA", Decimal("0.05"), None, None),  # all-zero date and blank field become NULL
    ]
    assert rels["trailer"].project("qtd").fetchall() == [(2,)]
    assert rels["detalhe"].project("_file").fetchall()[0][0].endswith("arquivo.txt")


def test_check_ok(good):
    assert gabarito.check(LAYOUT, good) == []


def test_check_reports_problems(tmp_path):
    p = write(
        tmp_path,
        [
            "001022026",
            detalhe("JOAO", "100", "20260315"),
            detalhe("CURTA", "100", "20260315")[:-3],  # wrong length
            detalhe("RUIM", "12X45", "20261345"),  # invalid amount and date
            "7 unknown record type",
            "9000002",  # trailer says 2, file has 3
        ],
    )
    problems = "\n".join(gabarito.check(LAYOUT, p))
    assert "1 line(s) match no record (lines 5)" in problems
    assert "detalhe: 1 line(s) with length other than 50 (lines 3)" in problems
    assert "detalhe.valor: 1 value(s) do not convert to decimal" in problems
    assert "detalhe.vencimento: 1 value(s) do not convert to date" in problems
    assert "trailer.qtd says 2, but the file has 3 detalhe record(s)" in problems


CSV_LAYOUT = {
    "source": "csv",
    "delimiter": ";",
    "records": {
        "dividas": {
            "fields": {
                "contrato": {"column": "Contrato"},
                "valor": {"column": "Valor", "type": "decimal"},
                "vencimento": {"column": "Vencimento", "type": "date", "format": "%d/%m/%Y"},
            }
        }
    },
}


def test_csv(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Contrato;Valor;Vencimento\n000123;1.234,56;15/03/2026\n", encoding="utf-8")
    rows = gabarito.load(CSV_LAYOUT, p)["dividas"].project("contrato, valor, vencimento").fetchall()
    assert rows == [("000123", Decimal("1234.56"), datetime.date(2026, 3, 15))]  # leading zeros kept
    assert gabarito.check(CSV_LAYOUT, p) == []


def test_csv_missing_and_unexpected_columns(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Contrato;Valor;Novo\n1;1,00;x\n", encoding="utf-8")
    assert gabarito.check(CSV_LAYOUT, p) == ["missing columns: vencimento", "unexpected columns: novo"]


def test_xlsx(tmp_path):
    con = duckdb.connect()
    try:
        con.sql("INSTALL excel; LOAD excel")
    except duckdb.Error:
        pytest.skip("DuckDB excel extension unavailable (no network?)")
    p = (tmp_path / "d.xlsx").as_posix()
    con.sql(
        'COPY (SELECT \'000123\' AS "Contrato", 1234.56 AS "Valor", \'15/03/2026\' AS "Vencimento") '
        f"TO '{p}' WITH (FORMAT xlsx, HEADER true)"
    )
    layout = {**CSV_LAYOUT, "source": "xlsx"}
    rows = gabarito.load(layout, p)["dividas"].fetchall()
    assert rows == [("000123", Decimal("1234.56"), datetime.date(2026, 3, 15))]
    assert gabarito.check(layout, p) == []


@pytest.mark.parametrize(
    "layout, message",
    [
        ({"records": {"a": {"fields": {"x": {"pos": [1, 2]}}}}}, "missing 'match'"),
        ({"records": {"a": {"match": "^1", "fields": {"x": {}}}}}, "'pos' must be"),
        ({"records": {"a": {"match": "^1", "fields": {"x": {"pos": [1, 2], "typ": "int"}}}}}, "did you mean 'type'"),
        ({"records": {"a": {"match": "^(", "fields": {"x": {"pos": [1, 2]}}}}}, "not a valid regex"),
        ({"records": {"a": {"match": "^1", "length": 5, "fields": {"x": {"pos": [4, 3]}}}}}, "past the record length"),
        ({**LAYOUT, "expect": [{"count": "detalhe", "equals": "trailer.nope"}]}, "equals must be"),
    ],
)
def test_invalid_layout(layout, message):
    with pytest.raises(gabarito.LayoutError, match=message):
        gabarito.load_layout(layout)


def test_cli_check_and_export(good, tmp_path):
    layout = tmp_path / "layout.yml"
    layout.write_text(yaml.safe_dump(LAYOUT), encoding="utf-8")
    assert main(["check", str(layout), str(good)]) == 0
    out = tmp_path / "out"
    assert main(["export", str(layout), str(good), str(out)]) == 0
    n = duckdb.sql(f"SELECT count(*) FROM '{(out / 'detalhe.parquet').as_posix()}'").fetchall()[0][0]
    assert n == 2


def test_cli_errors_are_one_line(good, tmp_path, capsys):
    bad = tmp_path / "bad.yml"
    bad.write_text("records: {a: {match: '^1', fields: {x: {pos: [0, 2]}}}}", encoding="utf-8")
    assert main(["check", str(bad), str(good)]) == 2
    assert capsys.readouterr().err.startswith("error: field a.x: 'pos' must be")
    assert main(["check", "no-such-layout", str(good)]) == 2


def test_init_fixed_draft_matches_the_file(good):
    draft = yaml.safe_load(gabarito.init(good))
    assert set(draft["records"]) == {"record_0", "record_1", "record_9"}
    assert draft["records"]["record_1"]["length"] == 50
    assert gabarito.check(draft, good) == []


def test_init_csv(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Contrato;Valor Total;Vencimento\n000123;1.234,56;15/03/2026\n", encoding="utf-8")
    draft = yaml.safe_load(gabarito.init(p))
    assert draft["delimiter"] == ";"
    assert draft["records"]["rows"]["fields"]["valor_total"] == {"column": "Valor Total"}
    assert gabarito.check(draft, p) == []


def cnab_line(**at):
    """A 240-position line; keys are p<start>, values are written from there."""
    line = [" "] * 240
    for k, v in at.items():
        start = int(k[1:]) - 1
        line[start : start + len(v)] = v
    return "".join(line)


def test_builtin_cnab240(tmp_path):
    p = write(
        tmp_path,
        [
            cnab_line(p1="0010000", p8="0", p73="EMPRESA SINTÉTICA", p144="19112026"),
            cnab_line(p1="0010001", p8="1", p9="R01"),
            cnab_line(p1="0010001", p8="3", p9="00001P", p38="12345", p78="15122026", p86="000000000123456"),
            cnab_line(p1="0010001", p8="3", p9="00002Q", p18="1", p19="000012345678901", p34="FULANO DE TAL"),
            cnab_line(p1="0010001", p8="5", p18="000004"),
            cnab_line(p1="0019999", p8="9", p18="000001", p24="000006"),
        ],
        "cnab.rem",
    )
    assert gabarito.check("cnab240", p) == []
    t = gabarito.load("cnab240", p)
    assert t["header_arquivo"].project("nome_empresa, data_geracao").fetchall() == [
        ("EMPRESA SINTÉTICA", datetime.date(2026, 11, 19))
    ]
    assert t["segmento_p"].project("nosso_numero, data_vencimento, valor_nominal, data_emissao").fetchall() == [
        ("12345", datetime.date(2026, 12, 15), Decimal("1234.56"), None)
    ]
    assert t["segmento_q"].project("pagador_numero_inscricao, pagador_nome").fetchall() == [
        ("000012345678901", "FULANO DE TAL")
    ]
    assert t["trailer_arquivo"].project("qtd_lotes, qtd_registros").fetchall() == [(1, 6)]


def test_expect_sum(tmp_path):
    layout = {
        "records": {
            "d": {"match": "^1", "fields": {"valor": {"pos": [2, 6], "type": "decimal", "scale": 2}}},
            "t": {"match": "^9", "fields": {"total": {"pos": [2, 8], "type": "decimal", "scale": 2}}},
        },
        "expect": [{"sum": "d.valor", "equals": "t.total"}],
    }
    assert gabarito.check(layout, write(tmp_path, ["1001050", "1000250", "900001300"])) == []
    assert gabarito.check(layout, write(tmp_path, ["1001050", "1000250", "900009999"])) == [
        "expect: t.total says 99.99, but d.valor adds up to 13.00"
    ]


def test_rejects(tmp_path):
    p = write(
        tmp_path,
        [
            "001022026",
            detalhe("JOAO", "100", "20260315"),
            detalhe("CURTA", "100", "20260315")[:-3],
            detalhe("RUIM", "12X45", "20261345"),
            "7 unknown record type",
            "9000003",
        ],
    )
    rows = gabarito.rejects(LAYOUT, p).project("_line, reasons, line").fetchall()
    assert [(n, why) for n, why, _ in rows] == [
        (3, "detalhe: length 47, expected 50"),
        (4, "detalhe.valor: not a valid decimal; detalhe.vencimento: not a valid date"),
        (5, "matches no record"),
    ]
    assert rows[2][2] == "7 unknown record type"  # the original line comes along


def test_rejects_csv(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Contrato;Valor;Vencimento\n1;1,00;15/03/2026\n2;abc;99/99/2026\n", encoding="utf-8")
    rows = gabarito.rejects(CSV_LAYOUT, p).project("reasons, Contrato").fetchall()
    assert rows == [("dividas.valor: not a valid decimal; dividas.vencimento: not a valid date", "2")]


WRITE_LAYOUT = {
    "encoding": "latin-1",
    "records": {
        "header": {
            "match": "^0",
            "length": 30,
            "fields": {
                "tipo": {"pos": [1, 1], "value": "0"},
                "data": {"pos": [2, 8], "type": "date", "format": "%d%m%Y"},
            },
        },
        "detalhe": {
            "match": "^1",
            "length": 30,
            "fields": {
                "tipo": {"pos": [1, 1], "value": "1"},
                "nome": {"pos": [2, 10]},
                "valor": {"pos": [12, 9], "type": "decimal", "scale": 2},
                "vencimento": {"pos": [21, 8], "type": "date", "format": "%Y%m%d"},
            },
        },
        "trailer": {
            "match": "^9",
            "length": 30,
            "fields": {"tipo": {"pos": [1, 1], "value": "9"}, "qtd": {"pos": [2, 6], "type": "int"}},
        },
    },
    "expect": [{"count": "detalhe", "equals": "trailer.qtd"}],
}


def test_write_round_trip(tmp_path):
    con = duckdb.connect()
    con.sql(
        "CREATE TABLE dividas AS SELECT * FROM (VALUES "
        "('JOÃO DA SILVA SAURO', 1234.56, DATE '2026-03-15'), ('MARIA', 0.05, NULL)) t(nome, valor, vencimento)"
    )
    out = tmp_path / "saida.txt"
    n = gabarito.write(
        WRITE_LAYOUT,
        {
            "header": "SELECT DATE '2026-02-01' AS data",
            "detalhe": "dividas",
            "trailer": "SELECT count(*) AS qtd FROM dividas",
        },
        out,
        con,
    )
    assert n == 4
    assert out.read_bytes().decode("latin-1").split("\r\n") == [
        "001022026" + " " * 21,
        "1JOÃO DA SI00012345620260315  ",  # name cut to 10, implied decimal, date formatted
        "1MARIA     00000000500000000  ",  # NULL date written as zeros
        "9000002" + " " * 23,
        "",
    ]
    assert gabarito.check(WRITE_LAYOUT, out) == []
    back = gabarito.load(WRITE_LAYOUT, out)["detalhe"].project("nome, valor, vencimento").fetchall()
    assert back == [("JOÃO DA SI", Decimal("1234.56"), datetime.date(2026, 3, 15)), ("MARIA", Decimal("0.05"), None)]


def test_write_refuses_numbers_that_do_not_fit(tmp_path):
    with pytest.raises(ValueError, match="detalhe.valor are negative or do not fit"):
        gabarito.write(WRITE_LAYOUT, {"detalhe": "SELECT 'X' AS nome, 99999999.99 AS valor"}, tmp_path / "o.txt")
    with pytest.raises(ValueError, match="detalhe.valor are negative or do not fit"):
        gabarito.write(WRITE_LAYOUT, {"detalhe": "SELECT 'X' AS nome, -1.00 AS valor"}, tmp_path / "o.txt")


def test_write_cnab240_interleaves_segments_and_checks_clean(tmp_path):
    out = tmp_path / "gerado.rem"
    titulos = "(VALUES (1, 'ANA', 150.00), (2, 'BRUNO', 32.99)) t(i, nome, valor)"
    gabarito.write(
        "cnab240",
        {
            "header_arquivo": "SELECT '001' AS banco, 'EMPRESA' AS nome_empresa, DATE '2026-11-20' AS data_geracao",
            "header_lote": "SELECT '001' AS banco, '0001' AS lote",
            "segmento_p": f"SELECT i AS _order, '001' AS banco, '0001' AS lote, i * 2 - 1 AS sequencial_no_lote, "
            f"valor AS valor_nominal, DATE '2026-12-15' AS data_vencimento FROM {titulos}",
            "segmento_q": f"SELECT i AS _order, '001' AS banco, '0001' AS lote, i * 2 AS sequencial_no_lote, "
            f"nome AS pagador_nome FROM {titulos}",
            "trailer_lote": "SELECT '001' AS banco, '0001' AS lote, 6 AS qtd_registros",
            "trailer_arquivo": "SELECT '001' AS banco, 1 AS qtd_lotes, 8 AS qtd_registros",
        },
        out,
    )
    lines = out.read_text(encoding="latin-1").splitlines()
    assert [ln[7] + ln[13] for ln in lines] == ["0 ", "1 ", "3P", "3Q", "3P", "3Q", "5 ", "9 "]
    assert gabarito.check("cnab240", out) == []
    t = gabarito.load("cnab240", out)
    assert t["segmento_p"].project("valor_nominal").fetchall() == [(Decimal("150.00"),), (Decimal("32.99"),)]
    assert t["segmento_q"].project("pagador_nome").fetchall() == [("ANA",), ("BRUNO",)]


def test_cli_rejects_and_write(good, tmp_path, capsys):
    layout = tmp_path / "layout.yml"
    layout.write_text(yaml.safe_dump(WRITE_LAYOUT), encoding="utf-8")
    out = tmp_path / "cli.txt"
    args = ["--from", "detalhe=SELECT 'ANA' AS nome, 10.5 AS valor", "--from", "trailer=SELECT 1 AS qtd"]
    assert main(["write", str(layout), str(out), *args]) == 0
    assert out.read_text(encoding="latin-1").splitlines()[0].startswith("1ANA       000001050")
    assert main(["rejects", str(layout), str(out)]) == 0
    out.write_text("1ANA       00000X05000000000  \n", encoding="latin-1")
    rej = tmp_path / "rejects.csv"
    assert main(["rejects", str(layout), str(out), str(rej)]) == 1
    assert "detalhe.valor: not a valid decimal" in rej.read_text(encoding="utf-8")


BUILTIN = sorted(p.stem for p in (Path(gabarito.__file__).parent / "layouts").glob("*.yml"))


@pytest.mark.parametrize("name", BUILTIN)
def test_builtin_layout_writes_what_it_reads(name, tmp_path):
    """One line per record, written from the layout's own literals, must read back as that record."""
    layout = gabarito.load_layout(name)
    out = tmp_path / "f.txt"
    assert gabarito.write(name, {r: "SELECT 1 AS _unused" for r in layout["records"]}, out) == len(layout["records"])
    problems = [p for p in gabarito.check(name, out) if not p.startswith("expect:")]
    assert problems == []
    for record, rel in gabarito.load(name, out).items():
        assert len(rel.fetchall()) == 1, record


A = {"match": "^1", "fields": {"x": {"pos": [1, 2]}}}


@pytest.mark.parametrize(
    "layout, message",
    [
        (["not", "a", "mapping"], "must be a mapping"),
        ({"source": "pdf", "records": {"a": A}}, "invalid source 'pdf'"),
        ({"source": "fixed"}, "has no 'records'"),
        ({"source": "csv", "records": {"a": A, "b": A}}, "takes exactly one record"),
        ({"records": {"a": "oops"}}, "must be a mapping with 'fields'"),
        ({"records": {"a": {"match": "^1"}}}, "has no 'fields'"),
        ({"records": {"a": {"match": "^1", "fields": {"x": "oops"}}}}, "must be a mapping, e.g."),
        ({"records": {"a": {"match": "^1", "fields": {"x": {"pos": [1, 2], "type": "money"}}}}}, "invalid type"),
        ({"records": {"a": {"match": "^1", "fields": {"x": {"pos": [1, 8], "type": "date"}}}}}, "date needs 'format'"),
        ({"records": {"a": A}, "expect": [{"count": "a"}]}, "each rule needs 'equals'"),
        ({"records": {"a": A}, "expect": [{"count": "zz", "equals": "a.x"}]}, "unknown record 'zz'"),
        ({"records": {"a": A}, "recods": 1}, "did you mean 'records'"),
    ],
)
def test_more_invalid_layouts(layout, message):
    with pytest.raises(gabarito.LayoutError, match=message):
        gabarito.load_layout(layout)


def test_invalid_yaml_file(tmp_path):
    bad = tmp_path / "bad.yml"
    bad.write_text("records: [unclosed", encoding="utf-8")
    with pytest.raises(gabarito.LayoutError, match="invalid YAML"):
        gabarito.load_layout(bad)


def test_write_rejects_bad_requests(tmp_path):
    out = tmp_path / "o.txt"
    with pytest.raises(gabarito.LayoutError, match="only produces fixed-width"):
        gabarito.write(CSV_LAYOUT, {}, out)
    with pytest.raises(gabarito.LayoutError, match="no record named 'nope'"):
        gabarito.write(WRITE_LAYOUT, {"nope": "SELECT 1"}, out)
    overlap = {"records": {"a": {"match": "^1", "fields": {"x": {"pos": [1, 5]}, "y": {"pos": [3, 5]}}}}}
    with pytest.raises(gabarito.LayoutError, match="a.y overlaps"):
        gabarito.write(overlap, {"a": "SELECT 'v' AS x"}, out)


def test_init_needs_lines(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(gabarito.LayoutError, match="no lines to sample"):
        gabarito.init(empty)


def test_cli_sql_init_and_rejects_to_stdout(good, tmp_path, capsys):
    layout = tmp_path / "layout.yml"
    layout.write_text(yaml.safe_dump(LAYOUT), encoding="utf-8")
    assert main(["sql", str(layout), str(good)]) == 0
    assert "-- detalhe\nSELECT" in capsys.readouterr().out
    assert main(["init", str(good)]) == 0
    assert "record_1:" in capsys.readouterr().out
    assert main(["--init", "SET threads=1", "rejects", str(layout), str(good)]) == 0
    assert capsys.readouterr().out.strip() == "ok"
    bad = write(tmp_path, ["7 unknown"], "bad.txt")
    assert main(["rejects", str(layout), str(bad)]) == 1
    assert "matches no record" in capsys.readouterr().out
    assert main(["check", str(layout), str(bad)]) == 1
    assert main(["write", str(layout), str(tmp_path / "w.txt"), "--from", "no-equals-sign"]) == 2
    assert "expects RECORD=SQL" in capsys.readouterr().err


def test_example_runs():
    ex = Path(__file__).parent.parent / "examples"
    assert gabarito.check(ex / "cobranca.yml", ex / "cobranca.txt") == []
    assert len(gabarito.load(ex / "cobranca.yml", ex / "cobranca.txt")["detalhe"].fetchall()) == 3


def test_encoding_auto(tmp_path):
    text = "0HEADER\n1JOÃO      00000105002026031500\n9000001\n"
    layout = {**LAYOUT, "encoding": "auto"}
    for enc in ("utf-8", "latin-1"):
        p = tmp_path / f"{enc}.txt"
        p.write_bytes(text.encode(enc))
        assert gabarito.detect_encoding(p) == enc
        assert f"encoding='{enc}'" in gabarito.sql(layout, p)["detalhe"]
        assert f"encoding: {enc}" in gabarito.init(p)
    # A late accent still counts: the whole file is scanned, not a sample.
    late = tmp_path / "late.txt"
    late.write_bytes(b"1MARIA\n" * 50_000 + "1JOÃO\n".encode("latin-1"))
    assert gabarito.detect_encoding(late) == "latin-1"
    with pytest.raises(gabarito.LayoutError, match="different encodings"):
        gabarito.detect_encoding(tmp_path / "*.txt")  # utf-8.txt next to latin-1.txt
    with pytest.raises(gabarito.LayoutError, match="auto"):
        gabarito.write({**WRITE_LAYOUT, "encoding": "auto"}, {}, tmp_path / "out.txt")


def test_encoding_cp1252(tmp_path):
    con = duckdb.connect()
    try:
        con.execute("INSTALL encodings")  # downloaded once; no network, no test
    except duckdb.Error:
        pytest.skip("encodings extension not available")
    p = tmp_path / "win.txt"
    p.write_bytes("1JOSÉ € 10\n".encode("cp1252"))  # 0x80 is not latin-1 for DuckDB
    assert gabarito.detect_encoding(p, con) == "CP1252"
