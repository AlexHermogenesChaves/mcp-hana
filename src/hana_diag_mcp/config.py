"""Cadastro de bases, gravado CIFRADO com DPAPI em config/conexoes.dat.

Nada do cadastro (host, porta, tenant, usuário, schema, chave do hdbuserstore) fica
em texto puro no disco. A senha nem passa por aqui: mora no hdbuserstore do HANA
client, também cifrada para o usuário do Windows.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import cofre

RAIZ = Path(os.environ.get("HANA_DIAG_HOME", Path(__file__).resolve().parents[2]))
ARQUIVO_CONEXOES = RAIZ / "config" / "conexoes.dat"
ARQUIVO_LEGADO = RAIZ / "config" / "conexoes.json"  # formato antigo, em texto puro
PASTA_LOGS = RAIZ / "logs"
HDBCLIENT_PADRAO = r"C:\Program Files\sap\hdbclient"

_NOME_VALIDO = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
_USERKEY_VALIDA = re.compile(r"^[A-Za-z0-9_]{1,64}$")
# O schema entra no texto da SQL (CURRENTSCHEMA), então aspas e ';' ficam proibidos.
_SCHEMA_VALIDO = re.compile(r"^[^\"'\x00;]{1,127}$")


@dataclass
class Conexao:
    nome: str
    descricao: str = ""
    userkey: str = ""  # chave do hdbuserstore (guarda host, usuário e senha cifrados)
    host: str = ""
    porta: int = 30015
    banco: str = ""  # tenant (MDC); vazio em instalação single-container
    usuario: str = ""
    schema: str = ""  # schema da empresa B1, ex.: SBO_CLIENTE_PRD
    encrypt: bool = False
    validar_certificado: bool = False
    timeout_ms: int = 60000

    def validar(self) -> None:
        if not _NOME_VALIDO.match(self.nome):
            raise ValueError("Nome da conexão: use só letras, números, '_' e '-' (até 40).")
        if not _USERKEY_VALIDA.match(self.userkey or ""):
            raise ValueError("Conexão sem chave do hdbuserstore válida. Cadastre com hana-diag-config adicionar.")
        if self.schema and not _SCHEMA_VALIDO.match(self.schema):
            raise ValueError("Schema inválido (não pode conter aspas simples/duplas nem ';').")


@dataclass
class Limites:
    linhas_padrao: int = 200
    linhas_maximo: int = 5000


@dataclass
class Configuracao:
    conexoes: list[Conexao] = field(default_factory=list)
    limites: Limites = field(default_factory=Limites)
    hdbclient: str = HDBCLIENT_PADRAO

    def obter(self, nome: str) -> Conexao:
        for c in self.conexoes:
            if c.nome.lower() == nome.lower():
                return c
        nomes = ", ".join(c.nome for c in self.conexoes) or "(nenhuma cadastrada)"
        raise ValueError(f"Conexão '{nome}' não encontrada. Disponíveis: {nomes}.")


def _de_dict(dados: dict) -> Configuracao:
    campos = {f.name for f in fields(Conexao)}
    conexoes = [Conexao(**{k: v for k, v in c.items() if k in campos}) for c in dados.get("conexoes", [])]
    return Configuracao(
        conexoes=conexoes,
        limites=Limites(**dados.get("limites", {})),
        hdbclient=dados.get("hdbclient", HDBCLIENT_PADRAO),
    )


def carregar(arquivo: Path = ARQUIVO_CONEXOES) -> Configuracao:
    if arquivo.exists():
        cfg = _de_dict(json.loads(cofre.decifrar(arquivo.read_bytes()).decode("utf-8")))
    else:
        legado = arquivo.with_name(ARQUIVO_LEGADO.name)
        if not legado.exists():
            return Configuracao()
        # Migração: lê o JSON antigo uma última vez, grava cifrado e apaga o texto puro.
        cfg = _de_dict(json.loads(legado.read_text(encoding="utf-8")))
        cfg.conexoes = [c for c in cfg.conexoes if c.userkey]  # sem hdbuserstore não há como manter
        salvar(cfg, arquivo)
    for c in cfg.conexoes:
        c.validar()
    return cfg


def salvar(cfg: Configuracao, arquivo: Path = ARQUIVO_CONEXOES) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    dados = {
        "hdbclient": cfg.hdbclient,
        "limites": asdict(cfg.limites),
        "conexoes": [asdict(c) for c in cfg.conexoes],
    }
    cifrado = cofre.cifrar(json.dumps(dados, ensure_ascii=False).encode("utf-8"))
    temporario = arquivo.with_suffix(".tmp")
    temporario.write_bytes(cifrado)
    os.replace(temporario, arquivo)  # troca atômica: nunca fica meio gravado
    legado = arquivo.with_name(ARQUIVO_LEGADO.name)
    if legado.exists():
        legado.unlink()
