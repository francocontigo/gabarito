import datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
import yaml

import remessa
from remessa.cli import main

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
    rels = remessa.load(LAYOUT, good)
    assert rels["header"].fetchall()[0][1] == datetime.date(2026, 2, 1)
    rows = rels["detalhe"].project("nome, valor, vencimento, obs").fetchall()
    assert rows == [
        ('JOÃO;"da",Silva', Decimal("1234.56"), datetime.date(2026, 3, 15), "a|b\tc"),
        ("MARIA", Decimal("0.05"), None, None),  # all-zero date and blank field become NULL
    ]
    assert rels["trailer"].project("qtd").fetchall() == [(2,)]
    assert rels["detalhe"].project("_file").fetchall()[0][0].endswith("arquivo.txt")


def test_check_ok(good):
    assert remessa.check(LAYOUT, good) == []


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
    problems = "\n".join(remessa.check(LAYOUT, p))
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
    rows = remessa.load(CSV_LAYOUT, p)["dividas"].project("contrato, valor, vencimento").fetchall()
    assert rows == [("000123", Decimal("1234.56"), datetime.date(2026, 3, 15))]  # leading zeros kept
    assert remessa.check(CSV_LAYOUT, p) == []


def test_csv_missing_and_unexpected_columns(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Contrato;Valor;Novo\n1;1,00;x\n", encoding="utf-8")
    assert remessa.check(CSV_LAYOUT, p) == ["missing columns: vencimento", "unexpected columns: novo"]


def test_xlsx(tmp_path):
    con = duckdb.connect()
    try:
        con.sql("INSTALL excel; LOAD excel")
    except duckdb.Error:
        pytest.skip("DuckDB excel extension unavailable (no network?)")
    p = (tmp_path / "d.xlsx").as_posix()
    con.sql(
        "COPY (SELECT '000123' AS \"Contrato\", 1234.56 AS \"Valor\", '15/03/2026' AS \"Vencimento\") "
        f"TO '{p}' WITH (FORMAT xlsx, HEADER true)"
    )
    layout = {**CSV_LAYOUT, "source": "xlsx"}
    rows = remessa.load(layout, p)["dividas"].fetchall()
    assert rows == [("000123", Decimal("1234.56"), datetime.date(2026, 3, 15))]
    assert remessa.check(layout, p) == []


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
    with pytest.raises(remessa.LayoutError, match=message):
        remessa.load_layout(layout)


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
    draft = yaml.safe_load(remessa.init(good))
    assert set(draft["records"]) == {"record_0", "record_1", "record_9"}
    assert draft["records"]["record_1"]["length"] == 50
    assert remessa.check(draft, good) == []


def test_init_csv(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Contrato;Valor Total;Vencimento\n000123;1.234,56;15/03/2026\n", encoding="utf-8")
    draft = yaml.safe_load(remessa.init(p))
    assert draft["delimiter"] == ";"
    assert draft["records"]["rows"]["fields"]["valor_total"] == {"column": "Valor Total"}
    assert remessa.check(draft, p) == []


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
    assert remessa.check("cnab240", p) == []
    t = remessa.load("cnab240", p)
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


def test_example_runs():
    ex = Path(__file__).parent.parent / "examples"
    assert remessa.check(ex / "cobranca.yml", ex / "cobranca.txt") == []
    assert len(remessa.load(ex / "cobranca.yml", ex / "cobranca.txt")["detalhe"].fetchall()) == 3
