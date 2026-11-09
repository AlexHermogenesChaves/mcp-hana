"""Cadastro de bases, gravado CIFRADO com DPAPI em config/conexoes.dat.

Nada do cadastro (host, porta, banco, usuário, empresa, chave do hdbuserstore, senha)
fica em texto puro no disco.

Senha:
- SAP HANA: no hdbuserstore do HANA client (chave `userkey`), cifrada para o usuário do
  Windows — o caminho que já funcionava;
- SQL Server: não tem hdbuserstore; a senha vai no próprio cadastro, cifrado com DPAPI.
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

TIPOS = {
    "hana": {"rotulo": "SAP HANA", "porta": 30015},
    "mssql": {"rotulo": "SQL Server", "porta": 1433},
}

_NOME_VALIDO = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
_USERKEY_VALIDA = re.compile(r"^[A-Za-z0-9_]{1,64}$")
# Schema, banco e instância entram no texto da SQL ou da connection string: aspas,
# colchetes e ';' ficam proibidos.
_IDENTIFICADOR_VALIDO = re.compile(r"^[^\"'\[\]\x00;]{1,127}$")

# Chaves gravadas pela versão Node (camelCase) -> nomes daqui.
_SINONIMOS = {"validarCertificado": "validar_certificado", "timeoutMs": "timeout_ms"}


@dataclass
class Conexao:
    nome: str
    descricao: str = ""
    tipo: str = "hana"  # hana | mssql
    userkey: str = ""  # HANA: chave do hdbuserstore (host, usuário e senha cifrados)
    host: str = ""
    porta: int = 30015
    banco: str = ""  # HANA: tenant (vazio em single-container); SQL Server: banco da empresa B1
    instancia: str = ""  # SQL Server: instância nomeada (vazio = padrão, usa a porta)
    usuario: str = ""
    senha: str = ""  # SQL Server (e HANA sem hdbuserstore): só existe dentro do arquivo cifrado
    schema: str = ""  # HANA: schema da empresa B1, ex.: SBO_CLIENTE_PRD
    encrypt: bool = False
    validar_certificado: bool = False
    timeout_ms: int = 60000

    @property
    def empresa(self) -> str:
        """Empresa B1 da conexão: schema no HANA, banco no SQL Server."""
        return self.banco if self.tipo == "mssql" else self.schema

    def validar(self) -> None:
        if not _NOME_VALIDO.match(self.nome):
            raise ValueError("Nome da conexão: use só letras, números, '_' e '-' (até 40).")
        if self.tipo not in TIPOS:
            raise ValueError(f"Tipo de banco inválido: {self.tipo!r} (use hana ou mssql).")
        if not 0 < int(self.porta) < 65536:
            raise ValueError("Porta inválida.")
        for rotulo, valor in (("Schema", self.schema), ("Banco", self.banco), ("Instância", self.instancia)):
            if valor and not _IDENTIFICADOR_VALIDO.match(valor):
                raise ValueError(f"{rotulo} inválido (não pode conter aspas, colchetes nem ';').")
        if self.tipo == "hana":
            if self.userkey:
                if not _USERKEY_VALIDA.match(self.userkey):
                    raise ValueError("Chave do hdbuserstore inválida.")
            elif not (self.host and self.usuario):
                raise ValueError("Conexão HANA sem chave do hdbuserstore nem host/usuário. Cadastre com hana-diag-config adicionar.")
        else:
            if not (self.host and self.usuario and self.banco):
                raise ValueError("Conexão SQL Server precisa de host, usuário e banco.")

    def resumo(self) -> dict:
        """O que pode ser mostrado ao modelo: nunca host, usuário ou senha."""
        return {
            "nome": self.nome,
            "descricao": self.descricao,
            "tipo": TIPOS[self.tipo]["rotulo"],
            "empresa": self.empresa,
        }


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


def mesmo_servidor(a: Conexao, b: Conexao) -> bool:
    """Mesmo servidor e mesma credencial (as empresas podem dividir a senha)."""
    if a.tipo != b.tipo:
        return False
    if a.userkey and b.userkey:
        return a.userkey.upper() == b.userkey.upper()
    mesmo = (
        a.host.lower() == b.host.lower()
        and a.usuario.upper() == b.usuario.upper()
        and a.instancia.upper() == b.instancia.upper()
        and (bool(a.instancia) or int(a.porta) == int(b.porta))
    )
    if a.tipo == "hana":  # no HANA o "banco" é o tenant: faz parte do servidor
        mesmo = mesmo and a.banco.upper() == b.banco.upper()
    return mesmo


def _conexao_de(dados: dict) -> Conexao:
    campos = {f.name for f in fields(Conexao)}
    normalizado = {_SINONIMOS.get(k, k): v for k, v in dados.items()}
    return Conexao(**{k: v for k, v in normalizado.items() if k in campos})


def _de_dict(dados: dict) -> Configuracao:
    limites = {k: v for k, v in dados.get("limites", {}).items() if k in {f.name for f in fields(Limites)}}
    return Configuracao(
        conexoes=[_conexao_de(c) for c in dados.get("conexoes", [])],
        limites=Limites(**limites),
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
