"""Servidor MCP (stdio) de diagnóstico somente leitura em bases SAP HANA.

Nunca escrever em stdout aqui: no transporte stdio ele é o canal do protocolo.
"""

from __future__ import annotations

import logging
import sys
import functools
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import banco, empresas
from .config import carregar
from .guarda import ConsultaBloqueada

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

INSTRUCOES = """\
Conector de DIAGNÓSTICO SOMENTE LEITURA para bases SAP HANA / SAP Business One de clientes,
usado para levantar informações antes de criar rotinas no ASTER.
- Só executa SELECT/WITH. Qualquer INSERT, UPDATE, DELETE, DDL, CALL ou controle de transação
  é recusado antes de chegar ao banco. Não tente contornar: peça ao usuário para executar.
- Comece por listar_conexoes. Cada conexão é uma empresa B1 (um schema); listar_empresas mostra as
  demais empresas do mesmo servidor.
- Não há consultas prontas: monte o SELECT para a pergunta do usuário e execute com `consultar`.
  Antes, confira nomes com listar_tabelas / descrever_tabela em vez de supor colunas.
- SEMPRE mostre ao usuário a SQL executada (bloco ```sql, campo `sql` do resultado) junto com o
  resultado, para ele poder conferir e reaproveitar.
- Nas tabelas do B1 os nomes de coluna são case-sensitive: use aspas ("ItemCode"). Campos de
  usuário ficam em CUFD (TableID, AliasID sem o U_), valores válidos em UFD1, tabelas de
  usuário em OUTB, UDOs em OUDO.
- Traga poucas linhas (max_linhas, TOP) e filtre no WHERE; a base é de produção do cliente.
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
    consulta foi recusada ou qual foi o erro do HANA.
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


def _rodar(conexao: str, sql: str, parametros: list[Any] | None = None, max_linhas: int | None = None) -> dict[str, Any]:
    c = carregar().obter(conexao)
    try:
        return banco.executar(c, sql, parametros or [], _limite(max_linhas))
    except (ConsultaBloqueada, ValueError):
        raise
    except Exception as e:  # erros do HANA/conexão voltam como texto legível para o modelo
        raise RuntimeError(f"Erro do HANA em '{c.nome}': {e}") from e


@ferramenta()
def listar_conexoes() -> list[dict[str, Any]]:
    """Lista as bases (empresas) cadastradas: nome, descrição e schema. Host, usuário e senha nunca são expostos."""
    return [{"nome": c.nome, "descricao": c.descricao, "schema": c.schema} for c in carregar().conexoes]


@ferramenta()
def testar_conexao(conexao: str) -> dict[str, Any]:
    """Conecta na base e devolve usuário, schema corrente, versão do HANA e nome do banco."""
    return _rodar(
        conexao,
        'SELECT CURRENT_USER AS "Usuario", CURRENT_SCHEMA AS "SchemaCorrente", '
        '(SELECT "VERSION" FROM "SYS"."M_DATABASE") AS "VersaoHANA", '
        '(SELECT "DATABASE_NAME" FROM "SYS"."M_DATABASE") AS "Banco" FROM DUMMY',
    )


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
        raise RuntimeError(f"Erro do HANA em '{base.nome}': {e}") from e
    for e in lista:
        e["conexao_cadastrada"] = empresas.ja_cadastrada(cfg, base, e["schema"]) or ""
    return {"origem": origem, "empresas": lista}


@ferramenta()
def listar_schemas(conexao: str, filtro: str = "") -> dict[str, Any]:
    """Lista schemas visíveis ao usuário. `filtro` é um trecho do nome (ex.: SBO)."""
    return _rodar(
        conexao,
        'SELECT "SCHEMA_NAME", "SCHEMA_OWNER" FROM "SYS"."SCHEMAS" '
        "WHERE UPPER(\"SCHEMA_NAME\") LIKE '%' || UPPER(?) || '%' ORDER BY \"SCHEMA_NAME\"",
        [filtro],
        1000,
    )


@ferramenta()
def listar_tabelas(conexao: str, filtro: str = "", schema: str = "", max_linhas: int = 300) -> dict[str, Any]:
    """Lista tabelas e views de um schema (padrão: o da conexão). `filtro` é trecho do nome, ex.: '@' para tabelas de usuário."""
    c = carregar().obter(conexao)
    alvo = schema or c.schema
    if not alvo:
        raise ValueError("Informe o schema (a conexão não tem schema padrão).")
    return _rodar(
        conexao,
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
    alvo = schema or c.schema
    if not alvo:
        raise ValueError("Informe o schema (a conexão não tem schema padrão).")
    return _rodar(
        conexao,
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

    Tabelas sem schema usam o schema da empresa da conexão. Filtre (WHERE / TOP) para não varrer
    a base do cliente. O resultado traz `sql`: mostre-a ao usuário junto com as linhas.
    """
    return _rodar(conexao, sql, None, max_linhas)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
