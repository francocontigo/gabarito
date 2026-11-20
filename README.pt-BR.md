# remessa

Um YAML descreve o arquivo que o cliente manda. O DuckDB devolve tabelas tipadas.

Serve para arquivo posicional (vários tipos de registro no mesmo arquivo), CSV e Excel, do disco ou do S3, sem importar nada para um banco antes. Também escreve arquivo posicional a partir de uma consulta. Já vem com layouts de CNAB 240 e CNAB 400.

[English](README.md)

![workflow](docs/workflow.svg)

O desenho como script executável, com Postgres no Docker: [`examples/workflow`](examples/workflow).

## Por que existe

Empresa que recebe arquivo de cliente acaba com um parser escrito à mão por cliente e um banco de staging cheio de tabela sem tipo. O motor de consulta nunca foi o que faltava: DuckDB, Trino e Athena já fazem join de arquivo com banco. O que falta fica antes da consulta:

- O DuckDB não lê arquivo posicional, e nada trata header, detalhe e trailer num layout só.
- O trailer que diz "120 registros" costuma ser lido e nunca comparado com o arquivo.
- O valor que não converte vai para um log, ou vira lixo em silêncio.

O `remessa` é a camada pequena que cobre esses três pontos. O layout é dado, o SQL é gerado, e o `check` diz se o arquivo de hoje bate com o layout antes de qualquer carga.

## Para testar

```bash
git clone <este repo> && cd remessa
pip install .
remessa check  examples/cobranca.yml examples/cobranca.txt
remessa export examples/cobranca.yml examples/cobranca.txt saida/
```

O layout do exemplo está em [`examples/cobranca.yml`](examples/cobranca.yml).

```python
import duckdb, remessa

con = duckdb.connect()
tabelas = remessa.load("examples/cobranca.yml", "examples/cobranca.txt", con)
detalhe = tabelas["detalhe"]
con.sql("SELECT nome, valor, vencimento FROM detalhe WHERE valor > 1000").show()
```

## Linha de comando

```bash
remessa init   arquivo.txt                    # rascunho de layout a partir de uma amostra
remessa check  layout.yml arquivo.txt         # o arquivo bate com o layout?
remessa sql    layout.yml arquivo.txt         # imprime o SQL gerado
remessa export layout.yml arquivo.txt saida/  # um Parquet por registro
remessa rejects layout.yml arquivo.txt ruins.csv  # as linhas que quebram o layout, com o motivo
remessa write  layout.yml saida.txt --from detalhe="SELECT ..."  # tabelas -> arquivo posicional
```

O `check` sai com código 1 e lista o que achou (as mensagens são em inglês):

```
1 line(s) match no record (lines 5)
detalhe: 1 line(s) with length other than 50 (lines 3)
detalhe.valor: 1 value(s) do not convert to decimal
expect: trailer.qtd says 2, but the file has 3 detalhe record(s)
```

Em CSV e Excel ele também acusa coluna faltando e coluna a mais. Layout inválido sai com código 2 e uma linha de erro, por exemplo `field detalhe.valor: unknown key 'typ' (did you mean 'type'?)`.

O `init` adivinha os tipos de registro pelo primeiro caractere e os limites dos campos pela amostra. É um ponto de partida: nomes e tipos ficam por sua conta.

## Formato do layout

| Chave | Significado |
|---|---|
| `source` | `fixed` (padrão), `csv` ou `xlsx` |
| `encoding` | Padrão `utf-8`. Arquivo legado costuma ser `latin-1` |
| `records.<nome>.match` | Regex que identifica as linhas do registro. Obrigatório em `fixed` |
| `records.<nome>.length` | Tamanho esperado da linha. Opcional, usado pelo `check` |
| `fields.<nome>.pos` | `[início, tamanho]`, início a partir de 1. Só em `fixed` |
| `fields.<nome>.column` | Nome da coluna no arquivo. Só em `csv` e `xlsx` |
| `fields.<nome>.type` | `str` (padrão), `int`, `decimal`, `date` |
| `fields.<nome>.value` | Literal fixo, usado pelo `write` quando nenhuma coluna alimenta o campo |
| `expect` | `count: <registro ou '*'>` ou `sum: <registro>.<campo>` deve ser igual a `equals: <registro>.<campo>` |
| `delimiter`, `skip_rows` | Só em `csv`. Delimitador padrão `;` |
| `decimal_comma` | `1.234,56` em vez de `1234.56`. Padrão: ligado em `csv`, desligado em `xlsx` |
| `sheet` | Aba da planilha. Só em `xlsx` |

Regras dos tipos:

- Todo campo perde os espaços das pontas. Campo vazio vira `NULL`.
- `str` nunca vira número, então zero à esquerda fica.
- `decimal` em `fixed` é decimal implícito: `0000000123456` com `scale: 2` é `1234.56`.
- `date` usa formato do `strptime`. Data toda em zeros vira `NULL`.
- Valor que não converte vira `NULL` na tabela e aparece no `check`.

Cada tabela traz `_file` com o arquivo de origem (exceto `xlsx`).

## Linhas rejeitadas

O `check` conta os problemas; o `rejects` entrega as linhas, para o pipeline carregar as boas e devolver as ruins.

```python
remessa.rejects("layout.yml", "arquivo.txt", con)   # _file, _line, reasons, line
```

```
_line | reasons                                                                  | line
3     | detalhe: length 47, expected 50                                          | 1CURTA ...
4     | detalhe.valor: not a valid decimal; detalhe.vencimento: not a valid date | 1RUIM ...
5     | matches no record                                                        | 7 unknown record type
```

## Escrita de arquivo

O mesmo layout que lê um arquivo escreve um. Cada registro é alimentado por uma consulta ou tabela; as colunas casam com os campos pelo nome.

```python
remessa.write(
    "retorno.yml",
    {
        "header": "SELECT current_date AS data",
        "detalhe": "SELECT contrato, status, valor FROM aceitos ORDER BY contrato",
        "trailer": "SELECT count(*) AS qtd, sum(valor) AS total FROM aceitos",
    },
    "retorno.txt",
    con,
)
```

- Texto é preenchido com espaços e cortado no tamanho do campo; número é preenchido com zeros; decimal perde a vírgula (`scale`); data usa o `format` do campo; `NULL` vira brancos ou zeros.
- Número que não cabe no campo, ou negativo, gera erro em vez de ser cortado.
- Campo sem coluna usa o `value` (código do tipo de registro, por exemplo), ou brancos.
- Os registros saem na ordem do layout. Para intercalar (CNAB: cada segmento P seguido do seu Q), inclua uma coluna `_order` nessas consultas.
- As linhas terminam em CRLF por padrão (`newline=` muda). A saída é um arquivo local.

## CNAB 240

Vem um layout pronto de cobrança: header e trailer de arquivo e de lote, segmentos P, Q e R (remessa), T e U (retorno).

```bash
remessa check  cnab240 retorno.ret
remessa export cnab240 retorno.ret saida/
```

As posições seguem o Layout Padrão FEBRABAN, versão 10.1. Foi conferido com remessas de exemplo de oito bancos (fixtures públicas do projeto brcobranca). Bancos fogem do padrão: dos retornos de exemplo, o do Sicredi passa como está e o do Santander precisa de layout próprio no segmento T. Copie `src/remessa/layouts/cnab240.yml` e ajuste.

CNAB 400 não tem padrão único entre bancos e não vem embutido.

## S3

Caminhos `s3://` funcionam para ler, com glob, e para gravar o Parquet. As credenciais ficam na conexão do DuckDB:

```python
con.sql("CREATE SECRET (TYPE s3, PROVIDER credential_chain)")
remessa.load("layout.yml", "s3://bucket/remessas/*.txt", con)
```

No CLI, `--init` roda um SQL na conexão antes do comando:

```bash
remessa --init "CREATE SECRET (TYPE s3, PROVIDER credential_chain)" \
  export layout.yml "s3://bucket/remessas/*.txt" s3://bucket/saida
```

Testado contra um S3 local (S3Mock), com chave fixa e endpoint próprio. Não testado contra a AWS.

## Outros bancos

O DuckDB é só quem executa. Dois caminhos:

**Parquet.** `remessa export` grava Parquet, que Redshift, Snowflake, BigQuery, Athena e Spark leem direto.

**Direto pelo DuckDB.** Ele anexa Postgres e MySQL e grava neles (o Postgres é exercitado em [`examples/workflow`](examples/workflow); MySQL não foi testado):

```python
con.sql("ATTACH 'postgresql://user:senha@host/db' AS pg (TYPE postgres)")
q = remessa.sql("layout.yml", "arquivo.txt")["detalhe"]
con.sql(f"CREATE TABLE pg.public.detalhe AS {q}")
```

## Arquivo grande e memória

Nenhum código Python toca nas linhas. A lib monta SQL; o DuckDB lê o arquivo em streaming, em paralelo, e usa disco quando a memória acaba. Por isso não há código de chunking aqui, e o teto de memória é configuração da conexão:

```bash
remessa --init "SET memory_limit='500MB'; SET threads=2" check layout.yml arquivo.txt
```

O `memory_limit` não cobre os buffers de leitura, que crescem com o número de threads. Para um teto baixo, reduza `threads` junto.

Para consumir as linhas em Python em lotes, em vez de tudo de uma vez:

```python
cur = con.execute(remessa.sql("layout.yml", "arquivo.txt")["detalhe"])
while linhas := cur.fetchmany(50_000):
    ...
```

Medido em arquivos CNAB 240 sintéticos, máquina Windows de 16 threads, DuckDB 1.5.6:

| Arquivo | Exportar um segmento para Parquet | `check` do layout inteiro (216 campos) |
|---|---|---|
| 2 GB, 8,6 milhões de linhas | 6 s, pico de 0,8 GB | 41 s, pico de 0,9 GB |
| 2 GB, com `memory_limit='500MB'` e 2 threads | 28 s, pico de 0,3 GB | 123 s, pico de 0,2 GB |
| 7,3 GB, 30 milhões de linhas | 20 s, pico de 1,3 GB | 146 s, pico de 1,0 GB |

## Limites

- Uma máquina só. Para volume que pede cluster, exporte Parquet e use outro engine.
- O `init` é uma heurística e não há detecção de encoding.
- Os números de linha do `check` seguem a ordem de leitura do DuckDB.
- Testado com DuckDB 1.5 e 2.0, em Windows e Linux.

## Licença

MIT
