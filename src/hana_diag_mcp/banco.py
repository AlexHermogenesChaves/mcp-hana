"""Execução no banco (SAP HANA ou SQL Server). Só este módulo fala com o banco, e só depois da guarda."""

from __future__ import annotations

import datetime as dt
import decimal
import json
import logging
import os
import time
import uuid
from typing import Any, Sequence

from .config import PASTA_LOGS, Conexao
from .guarda import validar_somente_leitura

log = logging.getLogger("hana_diag_mcp")


# HANA: ODBC do HANA client (HDBODBC), via pyodbc.
# O hdbcli (extensão Python da SAP) foi descartado: em 2026-11 todas as versões
# 2.22–2.30 derrubavam o processo com access violation em qualquer tentativa de
# abrir socket nesta estação, enquanto hdbsql e o ODBC, que usam as DLLs do
# HANA client instalado, funcionavam. O ODBC também aceita KEY= do hdbuserstore.
DRIVER_HANA = "HDBODBC"

# SQL Server: o melhor driver ODBC instalado. "SQL Server" (sqlsrv32) vem com o
# Windows; os "ODBC Driver 17/18" são melhores (TLS moderno, ApplicationIntent).
DRIVERS_MSSQL = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
)
_DRIVERS_MSSQL_MODERNOS = set(DRIVERS_MSSQL[:3])

# Consulta de teste por dialeto: usuário, empresa corrente, versão e banco/servidor.
SQL_TESTE = {
    "hana": 'SELECT CURRENT_USER AS "Usuario", CURRENT_SCHEMA AS "Empresa", '
    '(SELECT "VERSION" FROM "SYS"."M_DATABASE") AS "Versao", '
    '(SELECT "DATABASE_NAME" FROM "SYS"."M_DATABASE") AS "Banco" FROM DUMMY',
    "mssql": "SELECT SUSER_SNAME() AS Usuario, DB_NAME() AS Empresa, "
    "CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(50)) AS Versao, @@SERVERNAME AS Servidor",
}


def _pyodbc():
    try:
        import pyodbc
    except ImportError as e:  # pragma: no cover - depende do ambiente
        raise RuntimeError("Pacote pyodbc não instalado. Rode o instalar.ps1.") from e
    return pyodbc


def _valor_odbc(v: Any) -> str:
    """Protege valores na connection string ODBC: {valor} com '}' duplicado."""
    return "{" + str(v).replace("}", "}}") + "}"


def driver_mssql(instalados: Sequence[str] | None = None) -> str:
    instalados = list(_pyodbc().drivers() if instalados is None else instalados)
    for d in DRIVERS_MSSQL:
        if d in instalados:
            return d
    raise RuntimeError(
        "Nenhum driver ODBC do SQL Server encontrado. Instale com: winget install --id Microsoft.msodbcsql.18 -e"
    )


def _partes_hana(c: Conexao) -> dict[str, Any]:
    partes: dict[str, Any] = {"DRIVER": DRIVER_HANA}
    if c.userkey and not c.senha:
        # host, usuário e senha vêm da chave do hdbuserstore
        partes["KEY"] = _valor_odbc(c.userkey)
    else:
        partes["SERVERNODE"] = f"{c.host}:{c.porta}"
        if c.banco:
            partes["DATABASENAME"] = _valor_odbc(c.banco)
        partes["UID"] = _valor_odbc(c.usuario)
        partes["PWD"] = _valor_odbc(c.senha)
    partes.update(
        communicationTimeout=c.timeout_ms,
        ENCRYPT="TRUE" if c.encrypt else "FALSE",
        sslValidateCertificate="TRUE" if c.validar_certificado else "FALSE",
        CHAR_AS_UTF8="TRUE",
    )
    if c.schema:
        partes["CURRENTSCHEMA"] = _valor_odbc(c.schema)
    return partes


def _partes_mssql(c: Conexao, driver: str) -> dict[str, Any]:
    servidor = f"{c.host}\\{c.instancia}" if c.instancia else f"{c.host},{c.porta}"
    partes: dict[str, Any] = {
        "DRIVER": _valor_odbc(driver),
        "SERVER": _valor_odbc(servidor),
        "DATABASE": _valor_odbc(c.banco),
        "UID": _valor_odbc(c.usuario),
        "PWD": _valor_odbc(c.senha),
        "APP": "hana-diag (somente leitura)",
    }
    if driver in _DRIVERS_MSSQL_MODERNOS:
        partes["Encrypt"] = "yes" if c.encrypt else "no"
        partes["TrustServerCertificate"] = "no" if c.validar_certificado else "yes"
        partes["ApplicationIntent"] = "ReadOnly"  # réplica de leitura, quando houver
    elif c.encrypt:
        raise RuntimeError(
            f"O driver '{driver}' (antigo, do Windows) não faz TLS confiável. Instale o ODBC Driver 18: "
            "winget install --id Microsoft.msodbcsql.18 -e"
        )
    return partes


def string_conexao(c: Conexao, driver: str | None = None) -> str:
    """Connection string ODBC. Nunca vai para log nem para o modelo (pode conter a senha)."""
    if c.tipo == "mssql":
        partes = _partes_mssql(c, driver or driver_mssql())
    else:
        partes = _partes_hana(c)
    return ";".join(f"{k}={v}" for k, v in partes.items())


def abrir(c: Conexao):
    pyodbc = _pyodbc()
    if c.tipo == "mssql" and not c.senha:
        raise RuntimeError(f"A conexão {c.nome} está sem senha. Rode: hana-diag-config senha {c.nome}")
    if c.tipo == "hana" and not (c.userkey or c.senha):
        raise RuntimeError(f"A conexão {c.nome} está sem senha. Rode: hana-diag-config senha {c.nome}")
    # autocommit=False: nada é confirmado sem commit (e este módulo nunca faz commit).
    # Sem readonly=True: o HDBODBC não implementa SQL_ATTR_ACCESS_MODE e recusa a conexão
    # com HYC00 "Optional feature not implemented". O modo só leitura vem do
    # SET TRANSACTION READ ONLY (HANA) e do rollback (os dois), em executar().
    if c.tipo == "hana":
        # O secure store do HANA client (hdbuserstore) lê ALLUSERSPROFILE; quem sobe o
        # servidor MCP com ambiente mínimo não a passa, e a conexão falha com -10757.
        os.environ.setdefault("ALLUSERSPROFILE", os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    con = pyodbc.connect(string_conexao(c), autocommit=False, timeout=max(1, c.timeout_ms // 1000))
    if c.tipo == "mssql":
        # varchar chega na página de código ANSI do Windows; nvarchar segue UTF-16.
        con.setdecoding(pyodbc.SQL_CHAR, encoding="mbcs")
        con.timeout = max(1, c.timeout_ms // 1000)
    return con


def _valor(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 2**53 else str(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        return "0x" + b[:64].hex() + ("…" if len(b) > 64 else "")
    if hasattr(v, "read"):  # LOB
        conteudo = v.read()
        if isinstance(conteudo, (bytes, bytearray)):
            return _valor(conteudo)
        texto = str(conteudo)
        return texto if len(texto) <= 4000 else texto[:4000] + "…"
    return str(v)


def _auditar(registro: dict[str, Any]) -> None:
    try:
        PASTA_LOGS.mkdir(parents=True, exist_ok=True)
        with open(PASTA_LOGS / "auditoria.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except OSError:
        log.exception("Falha ao gravar auditoria")


def executar(c: Conexao, sql: str, parametros: Sequence[Any] = (), max_linhas: int = 200) -> dict[str, Any]:
    """Executa uma consulta de leitura e devolve colunas + linhas (limitadas)."""
    sql_ok = validar_somente_leitura(sql)
    inicio = time.perf_counter()
    registro: dict[str, Any] = {
        "quando": dt.datetime.now().isoformat(timespec="seconds"),
        "conexao": c.nome,
        "sql": sql_ok,
        "parametros": [str(p) for p in parametros],
    }
    con = None
    try:
        con = abrir(c)
        cur = con.cursor()
        read_only = False
        if c.tipo == "hana":
            try:
                # Segunda camada: a transação nasce somente leitura no servidor.
                cur.execute("SET TRANSACTION READ ONLY")
                read_only = True
            except Exception as e:  # noqa: BLE001 - se o servidor recusar, a guarda e o rollback seguem valendo
                log.warning("SET TRANSACTION READ ONLY recusado em %s: %s", c.nome, e)
            protecao = "transação READ ONLY + rollback" if read_only else "rollback (READ ONLY recusado)"
        else:
            # SQL Server não tem transação somente leitura: autocommit desligado abre uma
            # transação implícita que termina SEMPRE em rollback.
            protecao = "transação com rollback"
        registro["protecao_sessao"] = protecao
        if parametros:
            cur.execute(sql_ok, list(parametros))
        else:
            cur.execute(sql_ok)
        colunas = [d[0] for d in (cur.description or [])]
        brutas = cur.fetchmany(max_linhas + 1) if colunas else []
        truncado = len(brutas) > max_linhas
        linhas = [[_valor(v) for v in linha] for linha in brutas[:max_linhas]]
        ms = round((time.perf_counter() - inicio) * 1000)
        registro.update(status="ok", linhas=len(linhas), truncado=truncado, ms=ms)
        return {
            "conexao": c.nome,
            "sql": sql_ok,
            "colunas": colunas,
            "linhas": linhas,
            "total_linhas": len(linhas),
            "truncado": truncado,
            "tempo_ms": ms,
            "transacao_read_only": read_only,
            "protecao_sessao": protecao,
        }
    except Exception as e:
        registro.update(status="erro", erro=str(e), ms=round((time.perf_counter() - inicio) * 1000))
        raise
    finally:
        if con is not None:
            try:
                con.rollback()  # terceira camada: nada é confirmado, nunca
            finally:
                con.close()
        _auditar(registro)
