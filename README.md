# remessa

Um YAML descreve o arquivo que o cliente manda. O DuckDB devolve tabelas tipadas.

Serve para arquivo posicional (largura fixa, com vários tipos de registro), CSV e Excel. Lê do disco ou do S3, sem importar nada para um banco antes.

```bash
git clone <este repo> && pip install ./remessa
```

## Exemplo

`layout.yml`:

```yaml
source: fixed
encoding: latin-1
records:
  header:
    match: '^0'
    fields:
      data_geracao: {pos: [2, 8], type: date, format: '%d%m%Y'}
  detalhe:
    match: '^1'
    length: 50
    fields:
      nome:       {pos: [2, 20]}
      valor:      {pos: [22, 13], type: decimal, scale: 2}
      vencimento: {pos: [35, 8], type: date, format: '%Y%m%d'}
  trailer:
    match: '^9'
    fields:
      qtd: {pos: [2, 6], type: int}
expect:
  - count: detalhe
    equals: trailer.qtd
```

```python
import duckdb, remessa

con = duckdb.connect()
tabelas = remessa.load("layout.yml", "arquivos/*.txt", con)
detalhe = tabelas["detalhe"]
con.sql("SELECT d.nome, d.valor, c.id FROM detalhe d JOIN 'clientes.csv' c USING (nome)")
```

## Linha de comando

```bash
remessa check  layout.yml arquivo.txt        # o arquivo bate com o layout?
remessa sql    layout.yml arquivo.txt        # imprime o SQL gerado
remessa export layout.yml arquivo.txt saida/ # um Parquet por registro
```

`check` sai com código 1 e lista o que achou:

```
1 linha(s) não casam com nenhum registro (linhas 5)
detalhe: 1 linha(s) com tamanho diferente de 50 (linhas 3)
detalhe.valor: 1 valor(es) não convertem para decimal
expect: trailer.qtd diz 2, mas o arquivo tem 3 detalhe
```

Em CSV e Excel ele também acusa coluna faltando e coluna a mais.

## Formato do layout

| Chave | Significado |
|---|---|
| `source` | `fixed` (padrão), `csv` ou `xlsx` |
| `encoding` | Padrão `utf-8`. Arquivo legado costuma ser `latin-1` |
| `records.<nome>.match` | Regex que identifica a linha do registro. Obrigatório em `fixed` |
| `records.<nome>.length` | Tamanho esperado da linha. Opcional, usado pelo `check` |
| `fields.<nome>.pos` | `[início, tamanho]`, início a partir de 1. Só em `fixed` |
| `fields.<nome>.column` | Nome da coluna no arquivo. Só em `csv` e `xlsx` |
| `fields.<nome>.type` | `str` (padrão), `int`, `decimal`, `date` |
| `expect` | `count: <registro ou '*'>` deve ser igual a `equals: <registro>.<campo>` |
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

## CNAB 240

Vem um layout pronto de cobrança: header e trailer de arquivo e de lote, segmentos P, Q e R (remessa), T e U (retorno).

```bash
remessa check cnab240 retorno.ret
remessa export cnab240 retorno.ret saida/
```

As posições seguem o Layout Padrão FEBRABAN 240 posições, versão 10.1. Bancos mudam detalhes como convênio e nosso número: copie `src/remessa/layouts/cnab240.yml` e ajuste. Conferido com arquivos de remessa de exemplo de oito bancos (fixtures públicas do projeto brcobranca). Retorno (T e U) só foi testado com arquivo sintético.

CNAB 400 não tem padrão único entre bancos e não vem embutido.

## Levar para outro banco

O DuckDB é só quem executa. Dois caminhos:

**Parquet.** `remessa export` grava Parquet, que Redshift, Snowflake, BigQuery, Athena e Spark leem direto.

**Direto pelo DuckDB.** Ele anexa Postgres e MySQL e grava neles:

```python
con.sql("ATTACH 'postgresql://user:senha@host/db' AS pg (TYPE postgres)")
q = remessa.sql("layout.yml", "arquivo.txt")["detalhe"]
con.sql(f"CREATE TABLE pg.public.detalhe AS {q}")
```

Para S3, configure as credenciais na conexão (`CREATE SECRET`) e use caminhos `s3://`.

## Limites

- Uma máquina só. Para arquivos de dezenas de GB funciona; acima disso, exporte Parquet e use outro engine.
- Sem inferência de layout e sem detecção de encoding.
- Os números de linha do `check` seguem a ordem de leitura do DuckDB.

## Licença

MIT
