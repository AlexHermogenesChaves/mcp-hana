"""Execução no HANA. Só este módulo fala com o banco, e só depois da guarda."""

from __future__ import annotations

import datetime as dt
import decimal
import json
import logging
import time
from typing import Any, Sequence

from .config import PASTA_LOGS, Conexao
from .guarda import validar_somente_leitura

log = logging.getLogger("hana_diag_mcp")


# Driver: ODBC do HANA client (HDBODBC), via pyodbc.
# O hdbcli (extensão Python da SAP) foi descartado: em 2026-11 todas as versões
# 2.22–2.30 derrubavam o processo com access violation em qualquer tentativa de
# abrir socket nesta estação, enquanto hdbsql e o ODBC, que usam as DLLs do
# HANA client instalado, funcionavam. O ODBC também aceita KEY= do hdbuserstore.
DRIVER_ODBC = "HDBODBC"


def _pyodbc():
    try:
        import pyodbc
    except ImportError as e:  # pragma: no cover - depende do ambiente
        raise RuntimeError("Pacote pyodbc não instalado. Rode o instalar.ps1.") from e
    return pyodbc


def _valor_odbc(v: Any) -> str:
    """Protege valores na connection string ODBC: {valor} com '}' duplicado."""
    return "{" + str(v).replace("}", "}}") + "}"


def string_conexao(c: Conexao) -> str:
    """Connection string sem credencial: host, usuário e senha vêm da chave do hdbuserstore."""
    partes: dict[str, Any] = {
        "DRIVER": DRIVER_ODBC,
        "KEY": _valor_odbc(c.userkey),
        "communicationTimeout": c.timeout_ms,
        "ENCRYPT": "TRUE" if c.encrypt else "FALSE",
        "sslValidateCertificate": "TRUE" if c.validar_certificado else "FALSE",
        "CHAR_AS_UTF8": "TRUE",
    }
    if c.schema:
        partes["CURRENTSCHEMA"] = _valor_odbc(c.schema)
    return ";".join(f"{k}={v}" for k, v in partes.items())


def abrir(c: Conexao):
    pyodbc = _pyodbc()
    if not c.userkey:
        raise RuntimeError(f"A conexão {c.nome} não tem chave do hdbuserstore. Cadastre de novo com hana-diag-config.")
    # autocommit=False: nada é confirmado sem commit (e este módulo nunca faz commit).
    # Sem readonly=True: o HDBODBC não implementa SQL_ATTR_ACCESS_MODE e recusa a conexão
    # com HYC00 "Optional feature not implemented". O modo só leitura vem do
    # SET TRANSACTION READ ONLY, executado no servidor em executar().
    return pyodbc.connect(string_conexao(c), autocommit=False, timeout=max(1, c.timeout_ms // 1000))


def _valor(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 2**53 else str(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
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
        try:
            # Segunda camada: a transação nasce somente leitura no servidor.
            cur.execute("SET TRANSACTION READ ONLY")
            read_only = True
        except Exception as e:  # noqa: BLE001 - se o servidor recusar, a guarda e o rollback seguem valendo
            read_only = False
            log.warning("SET TRANSACTION READ ONLY recusado em %s: %s", c.nome, e)
        registro["transacao_read_only"] = read_only
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
