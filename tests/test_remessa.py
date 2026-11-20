import datetime
from decimal import Decimal

import duckdb
import pytest

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
    # latin-1 + CRLF, como os arquivos reais costumam chegar
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


def test_tipos(good):
    rels = remessa.load(LAYOUT, good)
    assert rels["header"].fetchall()[0][1] == datetime.date(2026, 2, 1)
    rows = rels["detalhe"].project("nome, valor, vencimento, obs").fetchall()
    assert rows == [
        ('JOÃO;"da",Silva', Decimal("1234.56"), datetime.date(2026, 3, 15), "a|b\tc"),
        ("MARIA", Decimal("0.05"), None, None),  # data zerada e campo vazio viram NULL
    ]
    assert rels["trailer"].project("qtd").fetchall() == [(2,)]
    assert rels["detalhe"].project("_file").fetchall()[0][0].endswith("arquivo.txt")


def test_check_ok(good):
    assert remessa.check(LAYOUT, good) == []


def test_check_acusa_problemas(tmp_path):
    p = write(
        tmp_path,
        [
            "001022026",
            detalhe("JOAO", "100", "20260315"),
            detalhe("CURTA", "100", "20260315")[:-3],  # tamanho errado
            detalhe("RUIM", "12X45", "20261345"),  # valor e data inválidos
            "7 linha de tipo desconhecido",
            "9000002",  # trailer diz 2, arquivo tem 3
        ],
    )
    problems = "\n".join(remessa.check(LAYOUT, p))
    assert "1 linha(s) não casam com nenhum registro (linhas 5)" in problems
    assert "detalhe: 1 linha(s) com tamanho diferente de 50 (linhas 3)" in problems
    assert "detalhe.valor: 1 valor(es) não convertem" in problems
    assert "detalhe.vencimento: 1 valor(es) não convertem" in problems
    assert "trailer.qtd diz 2, mas o arquivo tem 3 detalhe" in problems


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
    assert rows == [("000123", Decimal("1234.56"), datetime.date(2026, 3, 15))]  # zero à esquerda preservado
    assert remessa.check(CSV_LAYOUT, p) == []


def test_csv_coluna_faltando_e_sobrando(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Contrato;Valor;Novo\n1;1,00;x\n", encoding="utf-8")
    assert remessa.check(CSV_LAYOUT, p) == ["colunas faltando: vencimento", "colunas a mais: novo"]


def test_xlsx(tmp_path):
    con = duckdb.connect()
    try:
        con.sql("INSTALL excel; LOAD excel")
    except duckdb.Error:
        pytest.skip("extensão excel do DuckDB indisponível (sem rede?)")
    p = (tmp_path / "d.xlsx").as_posix()
    con.sql(
        "COPY (SELECT '000123' AS \"Contrato\", 1234.56 AS \"Valor\", '15/03/2026' AS \"Vencimento\") "
        f"TO '{p}' WITH (FORMAT xlsx, HEADER true)"
    )
    layout = {**CSV_LAYOUT, "source": "xlsx"}
    rows = remessa.load(layout, p)["dividas"].fetchall()
    assert rows == [("000123", Decimal("1234.56"), datetime.date(2026, 3, 15))]
    assert remessa.check(layout, p) == []


def test_layout_invalido():
    with pytest.raises(ValueError, match="falta 'match'"):
        remessa.load_layout({"records": {"a": {"fields": {"x": {"pos": [1, 2]}}}}})
    with pytest.raises(ValueError, match="'pos' deve ser"):
        remessa.load_layout({"records": {"a": {"match": "^1", "fields": {"x": {}}}}})


def test_cli_check_e_export(good, tmp_path, capsys):
    import yaml

    layout = tmp_path / "layout.yml"
    layout.write_text(yaml.safe_dump(LAYOUT), encoding="utf-8")
    assert main(["check", str(layout), str(good)]) == 0
    out = tmp_path / "out"
    assert main(["export", str(layout), str(good), str(out)]) == 0
    n = duckdb.sql(f"SELECT count(*) FROM '{(out / 'detalhe.parquet').as_posix()}'").fetchall()[0][0]
    assert n == 2


def cnab_line(**at):
    """Linha de 240 posições; chaves são p<início>, valores entram a partir dali."""
    line = [" "] * 240
    for k, v in at.items():
        start = int(k[1:]) - 1
        line[start : start + len(v)] = v
    return "".join(line)


def test_cnab240_embutido(tmp_path):
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
