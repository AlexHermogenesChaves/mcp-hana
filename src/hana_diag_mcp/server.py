"""Servidor MCP (stdio) para executar SELECTs nas bases SAP B1 dos clientes (SAP HANA e SQL Server).

Nunca escrever em stdout aqui: no transporte stdio ele é o canal do protocolo.
"""

from __future__ import annotations

import functools
import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import banco, empresas
from .config import TIPOS, Conexao, carregar
from .guarda import ConsultaBloqueada

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

INSTRUCOES = """\
Conector para executar SELECTs nas bases SAP Business One dos clientes (SAP HANA ou SQL Server)
sem precisar acessar o ambiente do cliente. SOMENTE LEITURA.
- Só executa SELECT/WITH. Qualquer INSERT, UPDATE, DELETE, DDL, EXEC/CALL ou controle de transação
  é recusado antes de chegar ao banco. Não tente contornar: peça ao usuário para executar.
- Comece por listar_conexoes: cada conexão é uma empresa B1 e informa o tipo de banco.
  listar_empresas mostra as demais empresas do mesmo servidor.
- Não há consultas prontas: monte o SELECT para a pergunta do usuário e execute com `consultar`.
  Antes, confira nomes com listar_tabelas / descrever_tabela em vez de supor colunas.
- SEMPRE mostre ao usuário a SQL executada (bloco ```sql, campo `sql` do resultado) junto com o
  resultado, para ele poder conferir e reaproveitar.
- Dialeto conforme o tipo da conexão:
  * SAP HANA: nomes de coluna case-sensitive entre aspas ("ItemCode"), LIMIT n, FROM DUMMY, || para concatenar.
  * SQL Server: colchetes ([ItemCode], [@MINHATAB]), TOP n, + para concatenar, sem FROM DUMMY.
- Campos de usuário ficam em CUFD (TableID, AliasID sem o U_), valores válidos em UFD1, tabelas de
  usuário em OUTB, UDOs em OUDO.
- Traga poucas linhas (max_linhas, TOP/LIMIT) e filtre no WHERE; a base é do cliente, muitas vezes produção.
"""

mcp = MCPServer("hana-diag", instructions=INSTRUCOES)

# Todas as ferramentas se declaram somente leitura para o cliente MCP.
_LEITURA = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

def ferramenta():
    """Registra a ferramenta como somente leitura e devolve ao modelo o motivo das falhas.

    No SDK 2.x só `ToolError` chega ao cliente com mensagem; qualquer outra exceção
    vira um genérico "Error executing tool" e o modelo não saberia por que a
    consulta foi recusada ou qual foi o erro do banco.
    """

    def registrar(fn):
        @functools.wraps(fn)
        def envolvida(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ConsultaBloqueada as e:
                raise ToolError(f"RECUSADA pela guarda de somente leitura: {e}") from e
            except (ValueError, RuntimeError) as e:
                raise ToolError(str(e)) from e

        return mcp.tool(annotations=_LEITURA)(envolvida)

    return registrar


def _limite(max_linhas: int | None) -> int:
    lim = carregar().limites
    if not max_linhas or max_linhas <= 0:
        return lim.linhas_padrao
    return min(max_linhas, lim.linhas_maximo)


def _executar(c: Conexao, sql: str, parametros: list[Any] | None = None, max_linhas: int | None = None) -> dict[str, Any]:
    try:
        return banco.executar(c, sql, parametros or [], _limite(max_linhas))
    except (ConsultaBloqueada, ValueError):
        raise
    except Exception as e:  # erros do banco/conexão voltam como texto legível para o modelo
        raise RuntimeError(f"Erro do {TIPOS[c.tipo]['rotulo']} em '{c.nome}': {e}") from e


@ferramenta()
def listar_conexoes() -> list[dict[str, Any]]:
    """Lista as bases (empresas) cadastradas: nome, descrição, tipo de banco e empresa. Host, usuário e senha nunca são expostos."""
    return [c.resumo() for c in carregar().conexoes]


@ferramenta()
def testar_conexao(conexao: str) -> dict[str, Any]:
    """Conecta na base e devolve usuário, empresa corrente e versão do banco."""
    c = carregar().obter(conexao)
    return _executar(c, banco.SQL_TESTE[c.tipo])


@ferramenta()
def listar_empresas(conexao: str) -> dict[str, Any]:
    """Empresas SAP B1 do mesmo servidor da conexão (SBO-COMMON.SRGC), marcando quais já estão cadastradas e com que nome.

    Para consultar uma empresa não cadastrada, peça ao usuário para rodar `hana-diag-config empresas <conexao>`.
    """
    cfg = carregar()
    base = cfg.obter(conexao)
    try:
        lista, origem = empresas.listar(base)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Erro do {TIPOS[base.tipo]['rotulo']} em '{base.nome}': {e}") from e
    saida = [
        {"empresa": e["schema"], "nome": e["nome"], "conexao_cadastrada": empresas.ja_cadastrada(cfg, base, e["schema"]) or ""}
        for e in lista
    ]
    return {"origem": origem, "empresas": saida}


@ferramenta()
def listar_schemas(conexao: str, filtro: str = "") -> dict[str, Any]:
    """HANA: schemas visíveis ao usuário. SQL Server: bancos acessíveis. `filtro` é um trecho do nome (ex.: SBO)."""
    c = carregar().obter(conexao)
    if c.tipo == "mssql":
        sql = (
            "SELECT name AS Banco FROM sys.databases WHERE HAS_DBACCESS(name) = 1 "
            "AND UPPER(name) LIKE '%' + UPPER(?) + '%' ORDER BY name"
        )
    else:
        sql = (
            'SELECT "SCHEMA_NAME", "SCHEMA_OWNER" FROM "SYS"."SCHEMAS" '
            "WHERE UPPER(\"SCHEMA_NAME\") LIKE '%' || UPPER(?) || '%' ORDER BY \"SCHEMA_NAME\""
        )
    return _executar(c, sql, [filtro], 1000)


@ferramenta()
def listar_tabelas(conexao: str, filtro: str = "", schema: str = "", max_linhas: int = 300) -> dict[str, Any]:
    """Lista tabelas e views. HANA: do schema (padrão: o da conexão). SQL Server: do banco da conexão (schema opcional, ex.: dbo).

    `filtro` é trecho do nome, ex.: '@' para tabelas de usuário.
    """
    c = carregar().obter(conexao)
    if c.tipo == "mssql":
        return _executar(
            c,
            "SELECT TABLE_SCHEMA AS Esquema, TABLE_NAME AS Objeto, "
            "CASE TABLE_TYPE WHEN 'VIEW' THEN 'VIEW' ELSE 'TABELA' END AS Tipo "
            "FROM INFORMATION_SCHEMA.TABLES WHERE (? = '' OR TABLE_SCHEMA = ?) "
            "AND UPPER(TABLE_NAME) LIKE '%' + UPPER(?) + '%' ORDER BY TABLE_NAME",
            [schema, schema, filtro],
            max_linhas,
        )
    alvo = schema or c.schema
    if not alvo:
        raise ValueError("Informe o schema (a conexão não tem schema padrão).")
    return _executar(
        c,
        'SELECT "TABLE_NAME" AS "Objeto", CAST(\'TABELA\' AS NVARCHAR(10)) AS "Tipo" FROM "SYS"."TABLES" '
        "WHERE \"SCHEMA_NAME\" = ? AND UPPER(\"TABLE_NAME\") LIKE '%' || UPPER(?) || '%' "
        "UNION ALL "
        'SELECT "VIEW_NAME", CAST(\'VIEW\' AS NVARCHAR(10)) FROM "SYS"."VIEWS" '
        "WHERE \"SCHEMA_NAME\" = ? AND UPPER(\"VIEW_NAME\") LIKE '%' || UPPER(?) || '%' "
        "ORDER BY 1",
        [alvo, filtro, alvo, filtro],
        max_linhas,
    )


@ferramenta()
def descrever_tabela(conexao: str, tabela: str, schema: str = "") -> dict[str, Any]:
    """Colunas de uma tabela ou view: nome, tipo, tamanho, nulo e default. `tabela` exatamente como no banco (ex.: OITM, @MINHATAB)."""
    c = carregar().obter(conexao)
    if c.tipo == "mssql":
        return _executar(
            c,
            "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_SCALE, IS_NULLABLE, "
            "COLUMN_DEFAULT, ORDINAL_POSITION FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE (? = '' OR TABLE_SCHEMA = ?) AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
            [schema, schema, tabela],
            2000,
        )
    alvo = schema or c.schema
    if not alvo:
        raise ValueError("Informe o schema (a conexão não tem schema padrão).")
    return _executar(
        c,
        'SELECT "COLUMN_NAME", "DATA_TYPE_NAME", "LENGTH", "SCALE", "IS_NULLABLE", "DEFAULT_VALUE", "POSITION" '
        'FROM "SYS"."TABLE_COLUMNS" WHERE "SCHEMA_NAME" = ? AND "TABLE_NAME" = ? '
        "UNION ALL "
        'SELECT "COLUMN_NAME", "DATA_TYPE_NAME", "LENGTH", "SCALE", "IS_NULLABLE", "DEFAULT_VALUE", "POSITION" '
        'FROM "SYS"."VIEW_COLUMNS" WHERE "SCHEMA_NAME" = ? AND "VIEW_NAME" = ? '
        'ORDER BY "POSITION"',
        [alvo, tabela, alvo, tabela],
        2000,
    )


@ferramenta()
def consultar(conexao: str, sql: str, max_linhas: int = 200) -> dict[str, Any]:
    """Executa um SELECT/WITH montado por você para a pergunta do usuário. Um comando só; escrita é recusada.

    Use o dialeto do tipo da conexão (SAP HANA ou SQL Server, ver listar_conexoes). Tabelas sem
    schema usam a empresa da conexão. Filtre (WHERE / TOP / LIMIT) para não varrer a base do
    cliente. O resultado traz `sql`: mostre-a ao usuário junto com as linhas.
    """
    return _executar(carregar().obter(conexao), sql, None, max_linhas)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
