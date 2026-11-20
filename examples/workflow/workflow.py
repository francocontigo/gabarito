"""A client file from arrival to the return file, with Postgres in the middle.

    docker compose up -d --wait
    python workflow.py
    docker compose down

Steps: check the file, set the bad lines aside, load the good ones into Postgres,
query them there, export Parquet, write the return file.
"""

import sys
from pathlib import Path

import duckdb

import gabarito

HERE = Path(__file__).parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
PG = "postgresql://gabarito:gabarito@localhost:55432/gabarito"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

con = duckdb.connect()
con.sql(f"ATTACH '{PG}' AS pg (TYPE postgres)")

# What the system already knows: contracts and their status.
con.sql("DROP TABLE IF EXISTS pg.public.remessa_detalhe")
con.sql("DROP TABLE IF EXISTS pg.public.contratos")
con.sql("CREATE TABLE pg.public.contratos (contrato VARCHAR, status VARCHAR)")
con.sql(
    "INSERT INTO pg.public.contratos VALUES "
    "('CT-0001','ATIVO'), ('CT-0002','ATIVO'), ('CT-0003','QUITADO'), ('CT-0005','ATIVO')"
)

print("1. check")
for problem in gabarito.check(HERE / "entrada.yml", HERE / "entrada.txt", con) or ["ok"]:
    print("  ", problem)

print("2. rejects -> out/rejeitadas.csv")
rejected = con.sql(gabarito.rejects_sql(HERE / "entrada.yml", HERE / "entrada.txt"))
con.sql(f"COPY rejected TO '{(OUT / 'rejeitadas.csv').as_posix()}'")
for line, reasons in rejected.project("_line, reasons").fetchall():
    print(f"   line {line}: {reasons}")

print("3. load into Postgres")
detalhe = gabarito.sql(HERE / "entrada.yml", HERE / "entrada.txt")["detalhe"]
con.sql(f"CREATE TABLE pg.public.remessa_detalhe AS SELECT * FROM ({detalhe}) WHERE valor IS NOT NULL")
print("   rows in Postgres:", con.sql("SELECT count(*) FROM pg.public.remessa_detalhe").fetchall()[0][0])

print("4. query in Postgres (the join runs there, not in DuckDB)")
joined = """
    SELECT r.contrato, r.nome, c.status, r.valor, r.vencimento
    FROM remessa_detalhe r JOIN contratos c USING (contrato)
    ORDER BY r.contrato
"""
con.sql(f"CREATE OR REPLACE TEMP TABLE aceitos AS SELECT * FROM postgres_query('pg', $${joined}$$)")
con.sql("SELECT * FROM aceitos").show()

print("5. export -> out/aceitos.parquet")
con.sql(f"COPY aceitos TO '{(OUT / 'aceitos.parquet').as_posix()}' (FORMAT parquet)")

print("6. write the return file -> out/retorno.txt")
n = gabarito.write(
    HERE / "retorno.yml",
    {
        "header": "SELECT current_date AS data",
        "detalhe": "SELECT contrato, status, valor FROM aceitos ORDER BY contrato",
        "trailer": "SELECT count(*) AS qtd, sum(valor) AS total FROM aceitos",
    },
    OUT / "retorno.txt",
    con,
)
written_ok = gabarito.check(HERE / "retorno.yml", OUT / "retorno.txt") or "ok"
print(f"   {n} lines; check of the file we just wrote:", written_ok)
print((OUT / "retorno.txt").read_text(encoding="latin-1"))
