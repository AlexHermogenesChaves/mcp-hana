"""Guarda de somente leitura (SAP HANA e SQL Server).

Toda SQL que sai deste conector passa por aqui antes de chegar no banco do cliente.
A regra é por lista de permissão: começa com SELECT ou WITH, e nenhuma palavra-chave
de escrita, DDL, execução, controle de transação/sessão ou trava aparece como código
— fora de literais, identificadores entre aspas/colchetes e comentários.

Primeira de três camadas (ver README): a sessão ainda roda em transação somente
leitura (HANA) ou com rollback garantido (SQL Server), e o usuário do banco deve ter
só SELECT.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# LISTA FIXA. Não existe configuração, variável de ambiente nem parâmetro de
# ferramenta que altere estas listas — de propósito. O teste tests/test_guarda.py
# confere a lista exata e o instalar.ps1 recusa instalar se ela tiver sido mexida.
# Mudança aqui só com aprovação do dono do repositório (CODEOWNERS).
#
# REPLACE fica de fora: como comando só valeria no início (que já tem de ser
# SELECT/WITH); no meio da consulta é a função de texto. "NEXT" também, por causa do
# OFFSET … FETCH NEXT do T-SQL — a sequence é barrada pelo par NEXT VALUE.
# ---------------------------------------------------------------------------
INICIOS_PERMITIDOS: frozenset[str] = frozenset({"SELECT", "WITH"})

PALAVRAS_PROIBIDAS: frozenset[str] = frozenset(
    {
        # DML
        "INSERT", "UPDATE", "DELETE", "UPSERT", "MERGE", "TRUNCATE", "INTO",
        "WRITETEXT", "UPDATETEXT",
        # DDL e privilégios
        "CREATE", "ALTER", "DROP", "RENAME", "GRANT", "REVOKE", "DENY", "COMMENT",
        # execução de código
        "CALL", "EXEC", "EXECUTE", "DO", "DECLARE",
        "OPENROWSET", "OPENQUERY", "OPENDATASOURCE", "OPENXML", "BULK",
        # transação, sessão e travas
        "COMMIT", "ROLLBACK", "SAVEPOINT", "BEGIN", "LOCK", "SET", "USE",
        "CONNECT", "DISCONNECT", "SETUSER", "REVERT",
        "UPDLOCK", "XLOCK", "TABLOCKX", "HOLDLOCK",
        # sequences (avançar sequence é escrita)
        "NEXTVAL",
        # administração, carga e exportação
        "LOAD", "UNLOAD", "IMPORT", "EXPORT", "BACKUP", "RESTORE", "RECOVER", "REFRESH",
        "ANNOTATE", "DBCC", "KILL", "SHUTDOWN", "RECONFIGURE", "CHECKPOINT", "WAITFOR",
    }
)

# Cópias internas usadas na verificação. Reatribuir os nomes públicos acima (por
# exemplo, guarda.PALAVRAS_PROIBIDAS = frozenset()) não muda o que é bloqueado.
_INICIOS = frozenset(INICIOS_PERMITIDOS)
_PROIBIDAS = frozenset(PALAVRAS_PROIBIDAS)

_PALAVRA = re.compile(r"[A-Za-z_][A-Za-z0-9_$#]*")

# delimitador de abertura -> (fechamento, tipo do trecho, mensagem se não fechar)
_DELIMITADORES = {
    "'": ("'", "literal", "Literal entre aspas simples não foi fechado."),
    '"': ('"', "identificador", "Identificador entre aspas duplas não foi fechado."),
    "[": ("]", "identificador", "Identificador entre colchetes não foi fechado."),
}


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
        if c in _DELIMITADORES:
            fecha, tipo, erro = _DELIMITADORES[c]
            despejar()
            j = i + 1
            while True:
                if j >= n:
                    raise ConsultaBloqueada(erro)
                if sql[j] == fecha:
                    if j + 1 < n and sql[j + 1] == fecha:  # '' "" ]] = escape
                        j += 2
                        continue
                    break
                j += 1
            saida.append((tipo, sql[i : j + 1]))
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
    if not isinstance(sql, str) or not sql.strip():
        raise ConsultaBloqueada("Consulta vazia.")

    segmentos = _segmentos(sql)
    codigos = [k for k, (tipo, txt) in enumerate(segmentos) if tipo == "codigo" and txt.strip()]
    if codigos:
        # ';' inicial (";WITH" do T-SQL) e um único ';' final são tolerados.
        primeiro, ultimo = codigos[0], codigos[-1]
        segmentos[primeiro] = ("codigo", re.sub(r"^\s*;", "", segmentos[primeiro][1]))
        segmentos[ultimo] = ("codigo", re.sub(r";\s*$", "", segmentos[ultimo][1]))
    if any(tipo == "codigo" and ";" in txt for tipo, txt in segmentos):
        raise ConsultaBloqueada("Só é permitido um comando por execução (encontrado ';' no meio).")

    codigo = " ".join(txt for tipo, txt in segmentos if tipo == "codigo")
    palavras = [p.upper() for p in _PALAVRA.findall(codigo)]
    if not palavras:
        raise ConsultaBloqueada("Nenhum comando encontrado.")
    if palavras[0] not in _INICIOS:
        raise ConsultaBloqueada(
            f"Só consultas SELECT ou WITH são permitidas; o comando começa com {palavras[0]}."
        )

    proibidas = {p for p in palavras if p in _PROIBIDAS}
    if any(a == "NEXT" and b == "VALUE" for a, b in zip(palavras, palavras[1:])):
        proibidas.add("NEXT VALUE FOR")
    if proibidas:
        raise ConsultaBloqueada(
            "Consulta bloqueada: contém palavra-chave de escrita/controle: " + ", ".join(sorted(proibidas)) + "."
        )

    return "".join(txt for _, txt in segmentos).strip()
