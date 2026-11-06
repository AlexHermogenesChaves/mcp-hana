"""Guarda de somente leitura.

Toda SQL que sai deste serviço passa por aqui antes de chegar no banco do cliente.
A regra é por lista de permissão: um único comando, que começa com SELECT ou WITH,
sem nenhuma palavra-chave de escrita, DDL, controle de transação ou chamada de
procedure fora de literais, identificadores entre aspas e comentários.

Esta é a primeira de três camadas (ver README): a conexão ainda roda em
transação READ ONLY com rollback no fim, e o usuário do banco deve ter só SELECT.
"""

from __future__ import annotations

import re

INICIOS_PERMITIDOS = frozenset({"SELECT", "WITH"})

# Palavras que nunca podem aparecer como código numa consulta de diagnóstico.
# REPLACE fica de fora de propósito: como comando ele só vale no início (e o
# início já tem de ser SELECT/WITH); no meio da consulta é a função de texto.
PALAVRAS_PROIBIDAS = frozenset(
    {
        # DML
        "INSERT", "UPDATE", "DELETE", "UPSERT", "MERGE", "TRUNCATE", "INTO",
        # DDL e privilégios
        "CREATE", "ALTER", "DROP", "RENAME", "GRANT", "REVOKE", "COMMENT",
        # execução de código
        "CALL", "EXEC", "EXECUTE", "DO",
        # transação e sessão
        "COMMIT", "ROLLBACK", "SAVEPOINT", "LOCK", "SET", "CONNECT", "DISCONNECT",
        # carga, exportação e administração
        "LOAD", "UNLOAD", "IMPORT", "EXPORT", "BACKUP", "RECOVER", "REFRESH",
        "ANNOTATE",
    }
)

_PALAVRA = re.compile(r"[A-Za-z_][A-Za-z0-9_$#]*")


class ConsultaBloqueada(ValueError):
    """A SQL não é uma consulta de leitura e não será enviada ao banco."""


def _segmentos(sql: str) -> list[tuple[str, str]]:
    """Quebra a SQL em trechos ('codigo' | 'literal' | 'identificador' | 'comentario')."""
    saida: list[tuple[str, str]] = []
    buffer: list[str] = []
    i, n = 0, len(sql)

    def despejar() -> None:
        if buffer:
            saida.append(("codigo", "".join(buffer)))
            buffer.clear()

    while i < n:
        c = sql[i]
        if c in ("'", '"'):
            despejar()
            j = i + 1
            while True:
                if j >= n:
                    raise ConsultaBloqueada("Literal ou identificador entre aspas não foi fechado.")
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:  # aspas duplicadas = escape
                        j += 2
                        continue
                    break
                j += 1
            saida.append(("literal" if c == "'" else "identificador", sql[i : j + 1]))
            i = j + 1
        elif sql.startswith("--", i):
            despejar()
            fim = sql.find("\n", i)
            fim = n if fim == -1 else fim
            saida.append(("comentario", sql[i:fim]))
            i = fim
        elif sql.startswith("/*", i):
            despejar()
            fim = sql.find("*/", i + 2)
            if fim == -1:
                raise ConsultaBloqueada("Comentário /* */ não foi fechado.")
            saida.append(("comentario", sql[i : fim + 2]))
            i = fim + 2
        else:
            buffer.append(c)
            i += 1
    despejar()
    return saida


def validar_somente_leitura(sql: str) -> str:
    """Devolve a SQL pronta para executar ou levanta ConsultaBloqueada."""
    if not sql or not sql.strip():
        raise ConsultaBloqueada("Consulta vazia.")

    segmentos = _segmentos(sql)

    # Aceita um único ';' no fim (comentários depois dele são tolerados).
    ultimo_codigo = max(
        (k for k, (tipo, txt) in enumerate(segmentos) if tipo == "codigo" and txt.strip()),
        default=None,
    )
    if ultimo_codigo is not None:
        texto = segmentos[ultimo_codigo][1].rstrip()
        if texto.endswith(";"):
            segmentos[ultimo_codigo] = ("codigo", texto[:-1])
    if any(tipo == "codigo" and ";" in txt for tipo, txt in segmentos):
        raise ConsultaBloqueada("Só é permitido um comando por execução (encontrado ';' no meio).")

    codigo = " ".join(txt for tipo, txt in segmentos if tipo == "codigo")
    palavras = [p.upper() for p in _PALAVRA.findall(codigo)]
    if not palavras:
        raise ConsultaBloqueada("Nenhum comando encontrado.")
    if palavras[0] not in INICIOS_PERMITIDOS:
        raise ConsultaBloqueada(
            f"Só consultas SELECT ou WITH são permitidas; o comando começa com {palavras[0]}."
        )
    proibidas = sorted({p for p in palavras if p in PALAVRAS_PROIBIDAS})
    if proibidas:
        raise ConsultaBloqueada(
            "Consulta bloqueada: contém palavra-chave de escrita/controle: " + ", ".join(proibidas) + "."
        )

    return "".join(txt for _, txt in segmentos).strip()

