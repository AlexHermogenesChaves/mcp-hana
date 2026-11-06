"""Descoberta das empresas (company databases) do SAP B1 num servidor HANA.

Fonte principal: "SBO-COMMON"."SRGC", o cadastro de empresas do B1.
Se o usuário não enxerga o SBO-COMMON, cai para os schemas que têm a tabela OADM
(toda empresa B1 tem), sem o nome da empresa.
"""

from __future__ import annotations

import re
from dataclasses import replace

from . import banco
from .config import Configuracao, Conexao

SQL_SRGC = (
    'SELECT "dbName", "cmpName", "versStr" FROM "SBO-COMMON"."SRGC" ORDER BY "dbName"'
)
SQL_OADM = (
    'SELECT "SCHEMA_NAME", CAST(NULL AS NVARCHAR(100)), CAST(NULL AS NVARCHAR(30)) '
    "FROM \"SYS\".\"TABLES\" WHERE \"TABLE_NAME\" = 'OADM' ORDER BY \"SCHEMA_NAME\""
)


def listar(c: Conexao) -> tuple[list[dict[str, str]], str]:
    """Devolve (empresas, origem). Cada empresa: schema, nome, versao."""
    try:
        r = banco.executar(c, SQL_SRGC, max_linhas=1000)
        origem = "SBO-COMMON.SRGC"
    except Exception:  # noqa: BLE001 - sem SBO-COMMON (ex.: tenant sem ele, ou sem permissão): pelo catálogo
        r = banco.executar(c, SQL_OADM, max_linhas=1000)
        origem = "schemas com OADM (SBO-COMMON indisponível)"
        r = _com_nome_da_oadm(c, r)
    empresas = [
        {"schema": str(s), "nome": str(n or ""), "versao": str(v or "")}
        for s, n, v in r["linhas"]
    ]
    return empresas, origem


def _com_nome_da_oadm(c: Conexao, r: dict) -> dict:
    """Completa o nome da empresa lendo a OADM de cada schema (uma consulta só, UNION ALL)."""
    schemas = [str(linha[0]) for linha in r["linhas"] if '"' not in str(linha[0])]
    if not schemas:
        return r
    partes = [
        f'SELECT CAST(\'{s.replace(chr(39), chr(39) * 2)}\' AS NVARCHAR(256)), '
        f'CAST(MAX("CompnyName") AS NVARCHAR(100)), CAST(NULL AS NVARCHAR(30)) FROM "{s}"."OADM"'
        for s in schemas
    ]
    try:
        return banco.executar(c, " UNION ALL ".join(partes) + " ORDER BY 1", max_linhas=1000)
    except Exception:  # noqa: BLE001 - sem SELECT em algum schema: fica a lista sem nome
        return r


def mesmo_servidor(a: Conexao, b: Conexao) -> bool:
    """Mesma chave do hdbuserstore = mesmo servidor, tenant e usuário."""
    return a.userkey.upper() == b.userkey.upper()


def ja_cadastrada(cfg: Configuracao, base: Conexao, schema: str) -> str | None:
    """Nome da conexão que já aponta para esse schema no mesmo servidor, se houver."""
    for c in cfg.conexoes:
        if mesmo_servidor(c, base) and c.schema.upper() == schema.upper():
            return c.nome
    return None


def sugerir_nome(schema: str, existentes: set[str]) -> str:
    """SBO_CLIENTE_PRD -> cliente-prd (sem colidir com nomes existentes)."""
    base = re.sub(r"^SBO[_\-]?", "", schema, flags=re.IGNORECASE)
    base = re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-").lower() or "empresa"
    base = base[:36]
    nome, n = base, 2
    while nome.lower() in existentes:
        nome = f"{base}-{n}"
        n += 1
    return nome


def nova_conexao(base: Conexao, nome: str, schema: str, descricao: str) -> Conexao:
    """Mesma credencial e servidor da conexão base, apontando para outro schema."""
    c = replace(base, nome=nome, schema=schema, descricao=descricao)
    c.validar()
    return c


def interpretar_selecao(texto: str, total: int) -> list[int]:
    """'1,3-5' / 'todas' -> índices 0-based. Levanta ValueError se inválido."""
    texto = texto.strip().lower()
    if texto in ("todas", "todos", "*"):
        return list(range(total))
    escolhidos: set[int] = set()
    for parte in filter(None, (p.strip() for p in texto.split(","))):
        if "-" in parte:
            ini, _, fim = parte.partition("-")
            a, b = int(ini), int(fim)
            if a > b:
                a, b = b, a
            faixa = range(a, b + 1)
        else:
            faixa = range(int(parte), int(parte) + 1)
        for i in faixa:
            if not 1 <= i <= total:
                raise ValueError(f"Número fora da lista: {i} (1 a {total}).")
            escolhidos.add(i - 1)
    return sorted(escolhidos)
