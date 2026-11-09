"""Configurador das bases (empresas) que o conector pode consultar — SAP HANA ou SQL Server.

    hana-diag-config                 menu interativo
    hana-diag-config listar
    hana-diag-config adicionar
    hana-diag-config testar <nome>
    hana-diag-config empresas <nome> lista as empresas B1 do servidor e cadastra as escolhidas
    hana-diag-config senha <nome>    troca a senha da base (sem recadastrar)
    hana-diag-config remover <nome>

Nada fica exposto em disco:
- a senha é digitada com asteriscos e confirmada e fica só em memória até ser guardada:
  * SAP HANA: entregue ao hdbuserstore do HANA client pela entrada padrão (nunca na linha
    de comando); ele guarda host, usuário e senha cifrados para o seu usuário do Windows;
  * SQL Server: gravada no próprio cadastro, cifrado com DPAPI;
- o cadastro (nomes, empresas, chaves, senhas do SQL Server) é gravado cifrado com DPAPI
  em config/conexoes.dat.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import empresas
from .config import ARQUIVO_CONEXOES, TIPOS, Conexao, carregar, mesmo_servidor, salvar

USUARIOS_ADMINISTRATIVOS = frozenset({"B1ADMIN", "SYSTEM", "SAPHANADB", "SBOCOMMON", "SA"})


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


def _situacao_senha(c: Conexao) -> str:
    if c.senha:
        return "ok"
    return "hdbuserstore" if c.userkey else "FALTA"


def listar() -> None:
    cfg = carregar()
    if not cfg.conexoes:
        print("Nenhuma base cadastrada. Use: hana-diag-config adicionar")
        return
    print(f"{'NOME':<20} {'BANCO':<11} {'EMPRESA':<30} {'SENHA':<13} DESCRIÇÃO")
    for c in cfg.conexoes:
        print(f"{c.nome:<20} {TIPOS[c.tipo]['rotulo']:<11} {c.empresa:<30} {_situacao_senha(c):<13} {c.descricao}")


def _avisar_se_administrativo(usuario: str) -> None:
    if usuario.upper() in USUARIOS_ADMINISTRATIVOS:
        print(
            f"  Atenção: {usuario} é usuário administrativo com permissão de escrita. O conector continua\n"
            "  só lendo (guarda de SQL + rollback), mas o ideal é um usuário só com SELECT."
        )


def adicionar() -> None:
    cfg = carregar()
    print("Nova base (SELECTs somente leitura)\n")
    tipo = ""
    while tipo not in TIPOS:
        tipo = _perguntar("Tipo de banco (hana / mssql)", "hana").lower()
    nome = _perguntar("Nome curto (ex.: cliente-prd)", obrigatorio=True)
    if any(c.nome.lower() == nome.lower() for c in cfg.conexoes):
        if not _sim(f"'{nome}' já existe. Substituir?"):
            return
        cfg.conexoes = [c for c in cfg.conexoes if c.nome.lower() != nome.lower()]
    descricao = _perguntar("Descrição (cliente / ambiente)")
    host = _perguntar(f"Host do {TIPOS[tipo]['rotulo']}", obrigatorio=True)

    if tipo == "hana":
        porta = int(_perguntar("Porta SQL", str(TIPOS[tipo]["porta"])))
        banco = _perguntar("Tenant/database (vazio se single-container; ex.: NDB)")
        usuario = _perguntar("Usuário do banco (de preferência um usuário só com SELECT)", obrigatorio=True)
        _avisar_se_administrativo(usuario)
        schema = _perguntar("Schema da empresa B1 (ex.: SBO_CLIENTE_PRD)")
        encrypt = _sim("Conexão com TLS (encrypt)?", False)
        c = Conexao(
            nome=nome, descricao=descricao, tipo=tipo, host=host, porta=porta, banco=banco, usuario=usuario,
            schema=schema, encrypt=encrypt, userkey="DIAG_" + nome.upper().replace("-", "_"),
        )
        c.validar()
        print()
        _gravar_no_hdbuserstore(cfg.hdbclient, c)
    else:
        print("  Instância nomeada é a parte depois da barra em SERVIDOR\\INSTANCIA (ex.: SQLEXPRESS).")
        print("  NÃO é o nome do servidor. Na dúvida, deixe vazio (instância padrão, porta 1433).")
        instancia = _perguntar("Instância nomeada (vazio = padrão)")
        if instancia:
            # Com a porta, a conexão vai direto e dispensa o SQL Browser (UDP 1434), que
            # costuma estar parado ou bloqueado na VPN.
            porta_texto = _perguntar("Porta TCP da instância (vazio = descobrir pelo SQL Browser)")
            if porta_texto:
                instancia, porta = "", int(porta_texto)
            else:
                porta = TIPOS[tipo]["porta"]
        else:
            porta = int(_perguntar("Porta", str(TIPOS[tipo]["porta"])))
        banco = _perguntar("Banco da empresa B1 (ex.: SBO_CLIENTE_PRD)", obrigatorio=True)
        usuario = _perguntar("Usuário do banco (de preferência um usuário só com SELECT)", obrigatorio=True)
        _avisar_se_administrativo(usuario)
        encrypt = _sim("Conexão com TLS (encrypt)?", False)
        c = Conexao(
            nome=nome, descricao=descricao, tipo=tipo, host=host, porta=porta, instancia=instancia,
            banco=banco, usuario=usuario, encrypt=encrypt,
        )
        c.validar()
        print()
        c.senha = _pedir_senha(usuario)

    cfg.conexoes.append(c)
    salvar(cfg)
    print(f"\nBase '{nome}' salva (cifrada) em {ARQUIVO_CONEXOES}.")
    if _sim("Testar a conexão agora?", True) and testar(nome):
        if _sim("Cadastrar outras empresas deste mesmo servidor (sem pedir a senha de novo)?", True):
            cadastrar_empresas(nome)


def trocar_senha(nome: str) -> None:
    """Troca a senha da base; oferece aplicar às outras empresas do mesmo servidor e usuário."""
    cfg = carregar()
    c = cfg.obter(nome)
    if not (c.host and c.usuario):
        raise SystemExit(f"A base '{c.nome}' não tem host/usuário no cadastro; recadastre com 'adicionar'.")
    destino = f"{c.host}\\{c.instancia}" if c.instancia else f"{c.host}:{c.porta}"
    print(f"Trocar a senha de {c.usuario} em {destino}" + (f"@{c.banco}" if c.tipo == "hana" and c.banco else ""))

    if c.tipo == "hana" and c.userkey and not c.senha:
        # HANA: a senha mora na chave do hdbuserstore, compartilhada pelas empresas do servidor.
        mesmas = [x.nome for x in cfg.conexoes if x.userkey == c.userkey]
        if len(mesmas) > 1:
            print(f"  (vale para: {', '.join(mesmas)})")
        _gravar_no_hdbuserstore(cfg.hdbclient, c)
    else:
        senha = _pedir_senha(c.usuario)
        alvos = [c]
        mesmas = [x for x in cfg.conexoes if x is not c and mesmo_servidor(x, c) and not x.userkey]
        if mesmas and _sim(f"Aplicar também a {', '.join(x.nome for x in mesmas)} (mesmo servidor e usuário)?", True):
            alvos += mesmas
        for x in alvos:
            x.senha = senha
        del senha
        salvar(cfg)
        print(f"  senha gravada (cifrada) para: {', '.join(x.nome for x in alvos)}.")
    if _sim("Testar a conexão agora?", True):
        testar(c.nome)


def remover(nome: str) -> None:
    cfg = carregar()
    c = cfg.obter(nome)
    if not _sim(f"Remover '{c.nome}'?"):
        return
    cfg.conexoes = [x for x in cfg.conexoes if x is not c]
    salvar(cfg)
    if c.userkey:
        # A chave é compartilhada pelas empresas do mesmo servidor: só apaga quando ninguém mais usa.
        outras = [x.nome for x in cfg.conexoes if x.userkey == c.userkey]
        if outras:
            print(f"Chave {c.userkey} mantida no hdbuserstore: ainda usada por {', '.join(outras)}.")
        elif _sim(f"Apagar também a chave {c.userkey} do hdbuserstore?", True):
            subprocess.run([str(_hdbuserstore(cfg.hdbclient)), "DELETE", c.userkey])
    print("Removida.")


def _dica_de_erro(c: Conexao, erro: str) -> str:
    """Traduz os erros de conexão mais comuns em o que fazer."""
    texto = erro.lower()
    if c.tipo == "mssql" and c.instancia and (
        "xffffffff" in texto or "localizar servidor/inst" in texto or "locating server/instance" in texto
    ):
        return (
            f"o SQL Browser (UDP 1434) não respondeu para achar a instância '{c.instancia}'. Se ela é a "
            "instância padrão (o nome é do servidor, não da instância), recadastre com 'Instância' vazia e "
            "porta 1433; se é mesmo nomeada, informe a porta TCP dela no cadastro."
        )
    if "login failed" in texto or "falha de logon" in texto or "authentication failed" in texto:
        return "rede e porta estão certas; usuário ou senha não. Troque com: hana-diag-config senha " + c.nome
    if "tempo limite" in texto or "timeout" in texto or "10060" in texto:
        return "o servidor não respondeu: confira a VPN, o host e a porta."
    return ""


def testar(nome: str) -> bool:
    from . import banco

    c = carregar().obter(nome)
    try:
        r = banco.executar(c, banco.SQL_TESTE[c.tipo])
    except Exception as e:  # noqa: BLE001 - mostrar ao usuário qualquer falha de conexão
        print(f"FALHOU: {e}")
        dica = _dica_de_erro(c, str(e))
        if dica:
            print(f"  DICA: {dica}")
        return False
    usuario, empresa, versao = r["linhas"][0][:3]
    print(f"OK em {r['tempo_ms']} ms — usuário {usuario}, empresa {empresa}, {TIPOS[c.tipo]['rotulo']} {versao}")
    if c.tipo == "hana" and not r.get("transacao_read_only"):
        print("  ATENÇÃO: o servidor recusou SET TRANSACTION READ ONLY; seguem valendo a guarda de SQL e o rollback.")
    else:
        print(f"  proteção da sessão: {r['protecao_sessao']}.")
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
    print(f"  {'#':>3}  {'EMPRESA (BANCO/SCHEMA)':<32} {'NOME':<40} SITUAÇÃO")
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
