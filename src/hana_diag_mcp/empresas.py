"""Descoberta das empresas (company databases) do SAP B1 num servidor.

HANA: empresa = schema. SQL Server: empresa = banco.
Fonte principal: SBO-COMMON.SRGC, o cadastro de empresas do B1.
Se o usuário não enxerga o SBO-COMMON, cai para os schemas/bancos que têm a tabela
OADM (toda empresa B1 tem), lendo o nome da empresa nela.
"""

from __future__ import annotations

import re
from dataclasses import replace

from . import banco
from .config import Configuracao, Conexao, mesmo_servidor

SQL_SRGC = {
    "hana": 'SELECT "dbName", "cmpName", "versStr" FROM "SBO-COMMON"."SRGC" ORDER BY "dbName"',
    "mssql": "SELECT dbName, cmpName, versStr FROM [SBO-COMMON].dbo.SRGC ORDER BY dbName",
}
SQL_OADM = (
    'SELECT "SCHEMA_NAME", CAST(NULL AS NVARCHAR(100)), CAST(NULL AS NVARCHAR(30)) '
    "FROM \"SYS\".\"TABLES\" WHERE \"TABLE_NAME\" = 'OADM' ORDER BY \"SCHEMA_NAME\""
)
SQL_BANCOS_MSSQL = (
    "SELECT name FROM sys.databases WHERE database_id > 4 AND state = 0 "
    "AND HAS_DBACCESS(name) = 1 ORDER BY name"
)
_SEGURO = re.compile(r"^[^\"'\[\];\x00]{1,128}$")


def listar(c: Conexao) -> tuple[list[dict[str, str]], str]:
    """Devolve (empresas, origem). Cada empresa: schema (= empresa: schema ou banco), nome, versao."""
    try:
        r = banco.executar(c, SQL_SRGC[c.tipo], max_linhas=1000)
        origem = "SBO-COMMON.SRGC"
    except Exception:  # noqa: BLE001 - sem SBO-COMMON (ou sem permissão nele): pelo catálogo
        if c.tipo == "mssql":
            return _bancos_com_oadm(c), "bancos com OADM (SBO-COMMON indisponível)"
        r = banco.executar(c, SQL_OADM, max_linhas=1000)
        origem = "schemas com OADM (SBO-COMMON indisponível)"
        r = _com_nome_da_oadm(c, r)
    empresas = [
        {"schema": str(s), "nome": str(n or ""), "versao": str(v or "")}
        for s, n, v in r["linhas"]
    ]
    return empresas, origem


def _com_nome_da_oadm(c: Conexao, r: dict) -> dict:
    """HANA: completa o nome da empresa lendo a OADM de cada schema (uma consulta só, UNION ALL)."""
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


def _bancos_com_oadm(c: Conexao) -> list[dict[str, str]]:
    """SQL Server: bancos acessíveis que têm OADM. Banco sem OADM não é empresa B1 e fica de fora."""
    nomes = [str(l[0]) for l in banco.executar(c, SQL_BANCOS_MSSQL, max_linhas=1000)["linhas"]]
    lista = []
    for nome in filter(_SEGURO.match, nomes):
        try:
            r = banco.executar(c, f"SELECT MAX(CompnyName) FROM [{nome}].dbo.OADM", max_linhas=1)
        except Exception:  # noqa: BLE001 - sem OADM ou sem permissão
            continue
        lista.append({"schema": nome, "nome": str(r["linhas"][0][0] or ""), "versao": ""})
    return lista


def ja_cadastrada(cfg: Configuracao, base: Conexao, empresa: str) -> str | None:
    """Nome da conexão que já aponta para essa empresa no mesmo servidor, se houver."""
    for c in cfg.conexoes:
        if mesmo_servidor(c, base) and c.empresa.upper() == empresa.upper():
            return c.nome
    return None


def sugerir_nome(empresa: str, existentes: set[str]) -> str:
    """SBO_CLIENTE_PRD -> cliente-prd (sem colidir com nomes existentes)."""
    base = re.sub(r"^SBO[_\-]?", "", empresa, flags=re.IGNORECASE)
    base = re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-").lower() or "empresa"
    base = base[:36]
    nome, n = base, 2
    while nome.lower() in existentes:
        nome = f"{base}-{n}"
        n += 1
    return nome


def nova_conexao(base: Conexao, nome: str, empresa: str, descricao: str) -> Conexao:
    """Mesma credencial e servidor da conexão base, apontando para outra empresa."""
    if base.tipo == "mssql":
        c = replace(base, nome=nome, banco=empresa, descricao=descricao)
    else:
        c = replace(base, nome=nome, schema=empresa, descricao=descricao)
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
