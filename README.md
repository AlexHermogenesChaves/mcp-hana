# hana-diag — conector MCP de diagnóstico SAP HANA (somente leitura)

Servidor MCP para o Claude consultar bases SAP HANA / SAP Business One dos clientes
durante o levantamento para criar rotinas no ASTER. Você pergunta em linguagem
natural; o Claude monta o `SELECT`, executa e mostra **a SQL e o resultado**.
Ele **não grava nada**: nenhum INSERT, UPDATE, DELETE, DDL, CALL ou controle de
transação chega ao banco.

## Instalação (cada pessoa, no próprio PC)

Pré-requisitos: Windows, Python 3.10+, Claude Code, `git`, acesso de rede ao HANA do
cliente (VPN) e, só se o HANA client ainda não estiver instalado, permissão de
administrador uma vez.

```powershell
git clone https://github.com/AlexHermogenesChaves/mcp-hana.git C:\Projetos_SPS\hana-diag-mcp
cd C:\Projetos_SPS\hana-diag-mcp
.\instalar.ps1 -RegistrarClaude
# com o instalador do HANA client numa pasta da rede:
# .\instalar.ps1 -RegistrarClaude -InstaladorHanaClient "\\servidor\instaladores\hanaclient-latest-windows-x64.zip"
```

Depois, cadastre as bases que **você** vai usar (seção abaixo). O repositório só tem
código: nenhum cadastro, senha ou log vai para o git, e o cadastro de uma pessoa não
abre no PC de outra (é cifrado para o usuário do Windows de quem cadastrou).

**Atualizar:** `git pull` e rodar `.\instalar.ps1` de novo. O seu cadastro não é afetado.

O instalador:

1. confere o Python 3.10+;
2. confere o **SAP HANA Client** (driver ODBC `HDBODBC` + `hdbuserstore.exe`) e, se
   faltar, instala com `hdbinst --batch`:
   - `-InstaladorHanaClient <zip ou pasta>` — usa um pacote já baixado (ex.: compartilhamento da SPS);
   - `-AceitoLicencaSAP` — baixa `hanaclient-latest-windows-x64.zip` de
     <https://tools.hana.ondemand.com/#hanatools> e confere o SHA1. Use só se você leu
     e aceita a [SAP Developer License 3.2](https://tools.hana.ondemand.com/developer-license-3_2.txt);
   - sem nenhum dos dois, abre a página de download e explica o que fazer;
3. cria `.venv` e instala o pacote;
4. roda os testes (guarda de leitura, criptografia, cadastro de empresas);
5. com `-RegistrarClaude`, registra o servidor `hana-diag` no Claude Code (escopo do usuário).

Para outro cliente MCP: comando `<pasta do clone>\.venv\Scripts\python.exe`,
argumentos `-m hana_diag_mcp.server` (stdio).

## Cadastrar empresas

Cada **empresa do B1** (um schema) é uma conexão. O fluxo é:

```powershell
.\.venv\Scripts\hana-diag-config.exe adicionar
```

1. Informe nome curto, host, porta, tenant (se houver), usuário e o schema de uma empresa.
2. Digite a senha **do banco** (aparece `*` a cada tecla) e confirme. O configurador avisa
   "senha recebida (N caracteres)" e a entrega ao `hdbuserstore`.
3. Ele testa a conexão e oferece **cadastrar as outras empresas do mesmo servidor**: lista
   as empresas do `SBO-COMMON.SRGC`, você escolhe (`1,3-5` ou `todas`) e confirma o nome
   de cada uma. Não pede a senha de novo.

Depois, a qualquer momento:

```powershell
.\.venv\Scripts\hana-diag-config.exe empresas cliente-prd   # empresas do servidor dessa base
.\.venv\Scripts\hana-diag-config.exe listar
.\.venv\Scripts\hana-diag-config.exe testar cliente-prd
.\.venv\Scripts\hana-diag-config.exe senha cliente-prd      # troca a senha sem recadastrar
.\.venv\Scripts\hana-diag-config.exe remover cliente-tst
.\.venv\Scripts\hana-diag-config.exe                         # menu
```

Sugestão de nome: `cliente-ambiente` (`cliente-prd`, `cliente-tst`, `outro-prd`).
O usuário do banco precisa de `SELECT` em **cada** schema cadastrado.

**"authentication failed"** no teste = rede, porta e TLS estão certos, mas usuário/senha
não. Confira se é a senha **do banco HANA** (não a do login do B1) e troque com
`senha <nome>`. Não repita o teste várias vezes: o HANA bloqueia o usuário após algumas
senhas erradas seguidas.

## Usar no Claude

Numa sessão nova do Claude Code, peça citando a empresa:

- *"Na cliente-prd, quais campos de usuário existem na ORDR?"*
- *"Quantos pedidos abertos da filial 3 estão sem U_Rota na cliente-prd?"*
- *"Quais empresas existem no servidor da cliente-prd?"*

| Ferramenta | Para quê |
|---|---|
| `listar_conexoes` | empresas cadastradas (só nome, descrição e schema) |
| `listar_empresas` | empresas B1 do mesmo servidor e quais já estão cadastradas |
| `testar_conexao` | usuário, schema, versão do HANA |
| `listar_schemas` / `listar_tabelas` / `descrever_tabela` | conferir nomes antes de montar a SQL |
| `consultar` | executa o `SELECT`/`WITH` montado pelo Claude; devolve a SQL junto com as linhas |

## Dados de conexão: nada exposto em disco

| Dado | Onde fica | Proteção |
|---|---|---|
| Senha | `hdbuserstore` do HANA client | cifrada pelo HANA client para o seu usuário do Windows; o configurador só a mantém em memória e entrega pela entrada padrão (nunca na linha de comando nem em arquivo) |
| Host, porta, tenant, usuário | `hdbuserstore` (usado na conexão) e `config\conexoes.dat` | ambos cifrados |
| Nomes, schemas, descrições, chaves | `config\conexoes.dat` | cifrado com **DPAPI** do Windows |

O DPAPI amarra a chave ao seu login do Windows: não existe senha mestra nem arquivo de
chave. Outro usuário, ou o arquivo copiado para outro computador, **não abre**. Por isso
cada pessoa do time cadastra as próprias bases. O `conexoes.dat` não deve ir para o git
(já está no `.gitignore`). Um `conexoes.json` de versão anterior é convertido e apagado
automaticamente na primeira execução.

As ferramentas MCP nunca devolvem host, usuário ou senha ao modelo. O
`logs\auditoria.jsonl` registra o SQL executado, a empresa, as linhas e o tempo, nunca
credenciais. Ele pode conter **valores** que estavam no `WHERE`, então não compartilhe
esse arquivo.

## Como a leitura é garantida (defesa em camadas)

1. **Guarda de SQL** (`guarda.py`), antes de abrir conexão: um único comando, que começa
   com `SELECT`/`WITH`, sem palavra-chave de escrita, DDL, `CALL`, `DO`, `INTO`,
   `FOR UPDATE`, `LOCK`, `SET`, `COMMIT` etc. fora de literais, identificadores entre
   aspas e comentários.
2. **Sessão**: conexão ODBC com `autocommit=False`, `SET TRANSACTION READ ONLY` no servidor
   (o teste e cada resultado informam se foi aceito) e **rollback** no fim de toda
   execução, com ou sem erro. O modo só leitura do próprio driver não é usado: o
   `HDBODBC` não o implementa (`HYC00`).
3. **Usuário do banco**: a camada que realmente fecha a porta. Peça ao DBA/parceiro do
   cliente um usuário só com leitura, por exemplo:

   ```sql
   CREATE USER ASTER_DIAG PASSWORD "..." NO FORCE_FIRST_PASSWORD_CHANGE;
   GRANT SELECT ON SCHEMA "SBO_CLIENTE_PRD" TO ASTER_DIAG;
   GRANT SELECT ON SCHEMA "SBO-COMMON" TO ASTER_DIAG;   -- para listar as empresas
   GRANT CATALOG READ TO ASTER_DIAG;
   ```

## Por que ODBC e não `hdbcli`

O driver Python da SAP (`hdbcli`, versões 2.22 a 2.30, em Python 3.12 e 3.13) derrubava o
processo com *access violation* em qualquer tentativa de conexão nesta estação, enquanto
`hdbsql` e o ODBC, que usam as DLLs do HANA client instalado, funcionavam. Por isso o
conector usa `pyodbc` + `HDBODBC`, e o HANA client é pré-requisito do instalador.
