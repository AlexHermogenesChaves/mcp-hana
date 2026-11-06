"""Configurador das bases de diagnóstico.

    hana-diag-config                 menu interativo
    hana-diag-config listar
    hana-diag-config adicionar
    hana-diag-config testar <nome>
    hana-diag-config empresas <nome> lista as empresas B1 do servidor e cadastra as escolhidas
    hana-diag-config senha <nome>    troca a senha da base (sem recadastrar)
    hana-diag-config remover <nome>

Nada fica exposto em disco:
- a senha é digitada com asteriscos e confirmada, fica só em memória e é entregue ao
  hdbuserstore do HANA client pela entrada padrão (nunca na linha de comando); ele
  guarda host, usuário e senha cifrados para o seu usuário do Windows;
- o cadastro (nomes, schemas, chaves) é gravado cifrado com DPAPI em config/conexoes.dat.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import empresas
from .config import ARQUIVO_CONEXOES, Conexao, carregar, salvar

USUARIOS_ADMINISTRATIVOS = frozenset({"B1ADMIN", "SYSTEM", "SAPHANADB", "SBOCOMMON"})


def _perguntar(rotulo: str, padrao: str = "", obrigatorio: bool = False) -> str:
    while True:
        sufixo = f" [{padrao}]" if padrao else ""
        valor = input(f"{rotulo}{sufixo}: ").strip() or padrao
        if valor or not obrigatorio:
            return valor
        print("  campo obrigatório.")


def _sim(rotulo: str, padrao: bool = False) -> bool:
    resp = input(f"{rotulo} [{'S/n' if padrao else 's/N'}]: ").strip().lower()
    return padrao if not resp else resp.startswith("s")


def _hdbuserstore(hdbclient: str) -> Path:
    exe = Path(hdbclient) / "hdbuserstore.exe"
    if not exe.exists():
        raise SystemExit(f"hdbuserstore.exe não encontrado em {hdbclient}. Ajuste 'hdbclient' em {ARQUIVO_CONEXOES}.")
    return exe


def _ler_oculto(rotulo: str) -> str:
    """Lê a senha mostrando '*' a cada tecla (Backspace apaga, Enter termina)."""
    if not sys.stdin.isatty():
        import getpass

        return getpass.getpass(rotulo)
    import msvcrt

    print(rotulo, end="", flush=True)
    caracteres: list[str] = []
    while True:
        tecla = msvcrt.getwch()
        if tecla in ("\r", "\n"):
            print()
            return "".join(caracteres)
        if tecla == "\x03":
            print()
            raise KeyboardInterrupt
        if tecla in ("\x00", "\xe0"):  # setas/F1…: vêm em dois códigos, ignorar
            msvcrt.getwch()
            continue
        if tecla == "\b":
            if caracteres:
                caracteres.pop()
                print("\b \b", end="", flush=True)
            continue
        caracteres.append(tecla)
        print("*", end="", flush=True)


def _pedir_senha(usuario: str, tentativas: int = 3) -> str:
    for _ in range(tentativas):
        senha = _ler_oculto(f"Senha do banco para {usuario}: ")
        if not senha:
            print("  senha vazia, digite de novo.")
            continue
        if _ler_oculto("Confirme a senha: ") != senha:
            print("  as senhas não conferem, digite de novo.")
            continue
        print(f"  senha recebida ({len(senha)} caracteres).")
        return senha
    raise SystemExit("Senha não confirmada; nada foi salvo.")


def _gravar_no_hdbuserstore(hdbclient: str, c: Conexao) -> None:
    """Grava host/porta/tenant/usuário/senha na chave do hdbuserstore.

    A senha vai pela entrada padrão do hdbuserstore em modo -i: nunca na linha de
    comando (visível na lista de processos) e nunca em disco por este programa.
    """
    senha = _pedir_senha(c.usuario)
    destino = f"{c.host}:{c.porta}" + (f"@{c.banco}" if c.banco else "")
    r = subprocess.run(
        [str(_hdbuserstore(hdbclient)), "-i", "SET", c.userkey, destino, c.usuario],
        input=senha + "\n", text=True, capture_output=True,
    )
    del senha
    if r.returncode != 0 or "succeed" not in (r.stdout + r.stderr).lower():
        detalhe = (r.stdout + r.stderr).replace("Password:", "").strip()
        raise SystemExit(f"hdbuserstore falhou; nada foi salvo. {detalhe}")
    print(f"  senha gravada (cifrada) na chave {c.userkey} do hdbuserstore.")


def listar() -> None:
    cfg = carregar()
    if not cfg.conexoes:
        print("Nenhuma base cadastrada. Use: hana-diag-config adicionar")
        return
    print(f"{'NOME':<20} {'SCHEMA':<28} {'CHAVE HDBUSERSTORE':<24} DESCRIÇÃO")
    for c in cfg.conexoes:
        print(f"{c.nome:<20} {c.schema:<28} {c.userkey:<24} {c.descricao}")


def adicionar() -> None:
    cfg = carregar()
    print("Nova base de diagnóstico (somente leitura)\n")
    nome = _perguntar("Nome curto (ex.: cliente-prd)", obrigatorio=True)
    if any(c.nome.lower() == nome.lower() for c in cfg.conexoes):
        if not _sim(f"'{nome}' já existe. Substituir?"):
            return
        cfg.conexoes = [c for c in cfg.conexoes if c.nome.lower() != nome.lower()]
    descricao = _perguntar("Descrição (cliente / ambiente)")
    host = _perguntar("Host do HANA", obrigatorio=True)
    porta = int(_perguntar("Porta SQL", "30015"))
    banco = _perguntar("Tenant/database (vazio se single-container; ex.: NDB)")
    usuario = _perguntar("Usuário do banco (de preferência um usuário só com SELECT)", obrigatorio=True)
    if usuario.upper() in USUARIOS_ADMINISTRATIVOS:
        print(
            f"  Atenção: {usuario} é usuário administrativo com permissão de escrita. O conector continua\n"
            "  só lendo (guarda + transação read only + rollback), mas o ideal é um usuário só com SELECT."
        )
    schema = _perguntar("Schema da empresa B1 (ex.: SBO_CLIENTE_PRD)")
    encrypt = _sim("Conexão com TLS (encrypt)?", False)

    c = Conexao(
        nome=nome, descricao=descricao, host=host, porta=porta, banco=banco, usuario=usuario,
        schema=schema, encrypt=encrypt, userkey="DIAG_" + nome.upper().replace("-", "_"),
    )
    c.validar()

    print()
    _gravar_no_hdbuserstore(cfg.hdbclient, c)

    cfg.conexoes.append(c)
    salvar(cfg)
    print(f"\nBase '{nome}' salva (cifrada) em {ARQUIVO_CONEXOES}.")
    if _sim("Testar a conexão agora?", True) and testar(nome):
        if _sim("Cadastrar outras empresas deste mesmo servidor (sem pedir a senha de novo)?", True):
            cadastrar_empresas(nome)


def trocar_senha(nome: str) -> None:
    """Regrava a senha da chave da base (vale para todas as empresas que usam a mesma chave)."""
    cfg = carregar()
    c = cfg.obter(nome)
    if not (c.host and c.usuario):
        raise SystemExit(f"A base '{c.nome}' não tem host/usuário no cadastro; recadastre com 'adicionar'.")
    mesmas = [x.nome for x in cfg.conexoes if x.userkey == c.userkey]
    print(f"Trocar a senha de {c.usuario} em {c.host}:{c.porta}" + (f"@{c.banco}" if c.banco else ""))
    if len(mesmas) > 1:
        print(f"  (vale para: {', '.join(mesmas)})")
    _gravar_no_hdbuserstore(cfg.hdbclient, c)
    if _sim("Testar a conexão agora?", True):
        testar(c.nome)


def remover(nome: str) -> None:
    cfg = carregar()
    c = cfg.obter(nome)
    if not _sim(f"Remover '{c.nome}'?"):
        return
    cfg.conexoes = [x for x in cfg.conexoes if x is not c]
    salvar(cfg)
    # A chave é compartilhada pelas empresas do mesmo servidor: só apaga quando ninguém mais usa.
    outras = [x.nome for x in cfg.conexoes if x.userkey == c.userkey]
    if outras:
        print(f"Chave {c.userkey} mantida no hdbuserstore: ainda usada por {', '.join(outras)}.")
    elif _sim(f"Apagar também a chave {c.userkey} do hdbuserstore?", True):
        subprocess.run([str(_hdbuserstore(cfg.hdbclient)), "DELETE", c.userkey])
    print("Removida.")


def testar(nome: str) -> bool:
    from . import banco

    c = carregar().obter(nome)
    try:
        r = banco.executar(
            c,
            'SELECT CURRENT_USER AS "Usuario", CURRENT_SCHEMA AS "Schema", '
            '(SELECT "VERSION" FROM "SYS"."M_DATABASE") AS "Versao" FROM DUMMY',
        )
    except Exception as e:  # noqa: BLE001 - mostrar ao usuário qualquer falha de conexão
        print(f"FALHOU: {e}")
        return False
    usuario, schema, versao = r["linhas"][0]
    print(f"OK em {r['tempo_ms']} ms — usuário {usuario}, schema {schema}, HANA {versao}")
    if r.get("transacao_read_only"):
        print("  transação somente leitura aceita pelo servidor.")
    else:
        print("  ATENÇÃO: o servidor recusou SET TRANSACTION READ ONLY; seguem valendo a guarda de SQL e o rollback.")
    return True


def cadastrar_empresas(nome_base: str) -> None:
    """Lista as empresas B1 do servidor da conexão base e cadastra as escolhidas.

    As novas conexões reaproveitam a chave do hdbuserstore da base: a senha não é
    pedida de novo.
    """
    cfg = carregar()
    base = cfg.obter(nome_base)
    print(f"\nBuscando as empresas do servidor de '{base.nome}'...")
    try:
        lista, origem = empresas.listar(base)
    except Exception as e:  # noqa: BLE001 - mostrar ao usuário qualquer falha de conexão
        print(f"Não foi possível listar as empresas: {e}")
        return
    if not lista:
        print("Nenhuma empresa B1 visível para este usuário.")
        return

    print(f"Origem: {origem}\n")
    print(f"  {'#':>3}  {'SCHEMA':<32} {'EMPRESA':<40} SITUAÇÃO")
    for i, e in enumerate(lista, 1):
        cadastrada = empresas.ja_cadastrada(cfg, base, e["schema"])
        situacao = f"já cadastrada ({cadastrada})" if cadastrada else ""
        print(f"  {i:>3}  {e['schema']:<32} {e['nome'][:40]:<40} {situacao}")

    texto = input("\nQuais cadastrar? (ex.: 1,3-5 · todas · Enter cancela): ").strip()
    if not texto:
        return
    try:
        escolhidas = empresas.interpretar_selecao(texto, len(lista))
    except ValueError as e:
        print(f"Seleção inválida: {e}")
        return

    existentes = {c.nome.lower() for c in cfg.conexoes}
    novas: list[Conexao] = []
    for i in escolhidas:
        e = lista[i]
        if empresas.ja_cadastrada(cfg, base, e["schema"]):
            print(f"  {e['schema']}: já cadastrada, pulando.")
            continue
        while True:
            nome = _perguntar(f"  Nome para {e['schema']}", empresas.sugerir_nome(e["schema"], existentes))
            if nome.lower() in existentes:
                print("    esse nome já existe.")
                continue
            try:
                c = empresas.nova_conexao(base, nome, e["schema"], e["nome"] or e["schema"])
            except ValueError as erro:
                print(f"    {erro}")
                continue
            break
        existentes.add(nome.lower())
        novas.append(c)

    if not novas:
        print("Nada novo para cadastrar.")
        return
    cfg.conexoes.extend(novas)
    salvar(cfg)
    print(f"\n{len(novas)} empresa(s) cadastrada(s): " + ", ".join(c.nome for c in novas))
    if _sim("Testar acesso a cada uma agora?", True):
        for c in novas:
            print(f"  {c.nome}: ", end="")
            testar(c.nome)


def menu() -> None:
    acoes = {
        "1": ("Listar bases", listar),
        "2": ("Adicionar/alterar base", adicionar),
        "3": ("Cadastrar empresas de um servidor já cadastrado",
              lambda: cadastrar_empresas(_perguntar("Nome de uma base já cadastrada nesse servidor", obrigatorio=True))),
        "4": ("Testar base", lambda: testar(_perguntar("Nome", obrigatorio=True))),
        "5": ("Trocar senha de uma base", lambda: trocar_senha(_perguntar("Nome", obrigatorio=True))),
        "6": ("Remover base", lambda: remover(_perguntar("Nome", obrigatorio=True))),
    }
    while True:
        print("\n=== hana-diag — configurador ===")
        for k, (rotulo, _) in acoes.items():
            print(f" {k}. {rotulo}")
        print(" 0. Sair")
        op = input("> ").strip()
        if op == "0":
            return
        if op in acoes:
            try:
                acoes[op][1]()
            except (ValueError, SystemExit) as e:
                print(f"Erro: {e}")


def main() -> None:
    p = argparse.ArgumentParser(prog="hana-diag-config", description="Configura as bases do conector MCP hana-diag.")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("listar")
    sub.add_parser("adicionar")
    for cmd in ("testar", "remover"):
        sub.add_parser(cmd).add_argument("nome")
    sub.add_parser("senha", help="troca a senha de uma base sem recadastrar").add_argument("nome")
    sub.add_parser("empresas", help="lista as empresas B1 do servidor de uma base e cadastra as escolhidas").add_argument(
        "nome", help="uma base já cadastrada nesse servidor"
    )
    a = p.parse_args()
    if a.cmd is None:
        menu()
    elif a.cmd == "listar":
        listar()
    elif a.cmd == "adicionar":
        adicionar()
    elif a.cmd == "testar":
        sys.exit(0 if testar(a.nome) else 1)
    elif a.cmd == "empresas":
        cadastrar_empresas(a.nome)
    elif a.cmd == "senha":
        trocar_senha(a.nome)
    elif a.cmd == "remover":
        remover(a.nome)


if __name__ == "__main__":
    main()
