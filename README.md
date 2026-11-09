# hana-diag — SELECTs nas bases SAP B1 dos clientes, sem acessar o ambiente local

Conector MCP para o Claude executar **consultas SELECT** nas bases SAP Business One dos
clientes — **SAP HANA** ou **SQL Server** — direto da sua máquina, sem abrir área de
trabalho remota, HANA Studio ou SSMS no ambiente do cliente. Você pergunta em linguagem
natural; o Claude monta o `SELECT`, executa e mostra **a SQL e o resultado**.

É **somente leitura**: nenhum INSERT, UPDATE, DELETE, DDL, EXEC/CALL ou controle de
transação chega ao banco.

> Ainda é preciso alcançar o servidor de banco pela rede (normalmente pela VPN do
> cliente). O que deixa de ser necessário é entrar no ambiente dele.

## Pré-requisitos

| Ferramenta | Para quê | Versão |
|---|---|---|
| Windows 10/11 | o cadastro é cifrado com o DPAPI do Windows | — |
| **Git** | baixar e atualizar o conector | qualquer recente |
| **Python** | roda o conector | **3.10 ou superior** (recomendado 3.13) |
| **Claude Code** | o cliente que usa o MCP | atual |
| **SAP HANA Client** | driver ODBC `HDBODBC` + `hdbuserstore` — **só para bases SAP HANA** | 2.x |
| Driver ODBC do SQL Server | **só para bases SQL Server**; o "SQL Server" já vem com o Windows | ODBC Driver 18 recomendado |
| VPN / acesso de rede ao servidor do cliente | alcançar o banco | — |

Não é preciso conta no GitHub — o repositório é público.

### 1. Instalar Git, Python e Claude Code

Abra o **PowerShell** (não precisa ser administrador) e rode:

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.13 -e
irm https://claude.ai/install.ps1 | iex
```

Opcional, para bases SQL Server com TLS (o driver antigo do Windows não faz TLS confiável):

```powershell
winget install --id Microsoft.msodbcsql.18 -e
```

Sem `winget` (Windows antigo ou bloqueado pela TI), baixe e instale manualmente, aceitando
as opções padrão:

- Git: <https://git-scm.com/download/win>
- Python: <https://www.python.org/downloads/windows/> — **marque "Add python.exe to PATH"**
- Claude Code: <https://docs.claude.com/en/docs/claude-code/setup>
- ODBC Driver 18 for SQL Server: <https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server>

**SAP HANA Client** (só se você vai consultar bases HANA): baixe
`hanaclient-latest-windows-x64.zip` em <https://tools.hana.ondemand.com/#hanatools>
(exige aceitar a SAP Developer License) e passe o zip para o instalador no passo 2
(`-InstaladorHanaClient "<caminho do zip>"`). Se já estiver instalado, nada a fazer.

**Feche e abra o PowerShell de novo** (para o `PATH` novo valer) e confira:

```powershell
git --version
python --version   # precisa mostrar 3.10 ou maior
claude --version
```

Na primeira vez, rode `claude` e faça login com a sua conta.

### 2. Baixar o conector e instalar

```powershell
git clone https://github.com/AlexHermogenesChaves/mcp-hana.git C:\Projetos_SPS\hana-diag-mcp
cd C:\Projetos_SPS\hana-diag-mcp
powershell -ExecutionPolicy Bypass -File .\instalar.ps1 -RegistrarClaude
```

(O `-ExecutionPolicy Bypass` vale só para essa execução; evita o erro "a execução de
scripts foi desabilitada neste sistema" sem mudar a configuração do Windows.)

O instalador confere o Python, o SAP HANA Client e o driver do SQL Server, cria o ambiente
`.venv` com as dependências, roda os testes (inclusive a conferência da lista de palavras
proibidas — se ela foi alterada, **a instalação é recusada**) e, com `-RegistrarClaude`,
registra o servidor `hana-diag` no Claude Code.

Confira o registro (deve aparecer `Connected`):

```powershell
claude mcp get hana-diag
```

Se o instalador avisar que a CLI `claude` não foi encontrada, registre manualmente:

```powershell
claude mcp add hana-diag --scope user -- C:\Projetos_SPS\hana-diag-mcp\.venv\Scripts\python.exe -m hana_diag_mcp.server
```

Outro cliente MCP: comando `<pasta do clone>\.venv\Scripts\python.exe`, argumentos
`-m hana_diag_mcp.server` (stdio).

### Atualizar

```powershell
cd C:\Projetos_SPS\hana-diag-mcp
git pull
powershell -ExecutionPolicy Bypass -File .\instalar.ps1 -RegistrarClaude
```

O seu cadastro (`config\`) não é afetado. Depois de atualizar, abra uma sessão nova do
Claude Code.

### Problemas comuns na instalação

| Sintoma | O que fazer |
|---|---|
| `git`, `python` ou `claude` "não é reconhecido" | feche e abra o PowerShell; se persistir, reinstale marcando "Add to PATH" |
| `python` abre a Microsoft Store | o Python não está instalado de verdade: `winget install --id Python.Python.3.13 -e` |
| "execução de scripts foi desabilitada" | rode com `powershell -ExecutionPolicy Bypass -File .\instalar.ps1 ...` |
| `pip` falha com erro de rede/certificado | proxy da empresa: defina `HTTPS_PROXY=http://proxy:porta` antes de instalar, ou rode fora da VPN só a instalação |
| aviso "SAP HANA Client não está instalado" | só importa para bases HANA; veja o passo 1 |
| erro `-10757 Secure store error` em base HANA | o hdbuserstore não achou o cadastro da senha: rode `hana-diag-config senha <nome>` com o mesmo usuário do Windows |
| testes reprovados / instalação recusada | o código local foi alterado; descarte com `git checkout -- .` e `git pull` |
| antivírus apaga arquivos do conector | peça à TI a liberação da pasta `C:\Projetos_SPS\hana-diag-mcp` (falso positivo) |

## Cadastrar empresas

Cada **empresa do B1** é uma conexão: no HANA, um schema; no SQL Server, um banco.

```powershell
.\hana-diag-config.cmd adicionar
```

1. Escolha o tipo (`hana` ou `mssql`) e informe host, porta (ou instância nomeada, no
   SQL Server), tenant (HANA), empresa e usuário.
2. Digite a senha **do banco** (aparece `*` a cada tecla) e confirme. O configurador
   mostra "senha recebida (N caracteres)". No HANA ela vai para o `hdbuserstore` do HANA
   client; no SQL Server, para o cadastro cifrado.
3. Ele testa a conexão e oferece **cadastrar as outras empresas do mesmo servidor** (lidas
   do `SBO-COMMON.SRGC` ou, sem ele, dos bancos/schemas que têm `OADM`). Não pede a senha
   de novo.

Depois, a qualquer momento:

```powershell
.\hana-diag-config.cmd listar
.\hana-diag-config.cmd empresas cliente-prd   # empresas do servidor dessa base
.\hana-diag-config.cmd testar cliente-prd
.\hana-diag-config.cmd senha cliente-prd      # troca a senha (oferece aplicar às empresas do mesmo servidor)
.\hana-diag-config.cmd remover cliente-tst
.\hana-diag-config.cmd                        # menu
```

Sugestão de nome: `cliente-ambiente` (`cliente-prd`, `cliente-tst`). O usuário do banco
precisa de `SELECT` em **cada** empresa cadastrada.

**"authentication failed" / "Login failed"** = rede e porta estão certas, mas usuário ou
senha não. Confira se é a senha **do banco** (não a do login do B1) e troque com
`senha <nome>`. Não repita várias vezes: o banco bloqueia o usuário após algumas senhas
erradas seguidas.

## Usar no Claude

Numa sessão nova do Claude Code, peça citando a empresa:

- *"Na cliente-prd, quais campos de usuário existem na ORDR?"*
- *"Faça um count da OCRD na cliente-prd por tipo de parceiro."*
- *"Quais empresas existem no servidor da cliente-prd?"*

| Ferramenta | Para quê |
|---|---|
| `listar_conexoes` | empresas cadastradas (nome, descrição, tipo de banco, empresa) |
| `listar_empresas` | empresas B1 do mesmo servidor e quais já estão cadastradas |
| `testar_conexao` | usuário, empresa, versão do banco |
| `listar_schemas` / `listar_tabelas` / `descrever_tabela` | conferir nomes antes de montar a SQL |
| `consultar` | executa o `SELECT`/`WITH` montado pelo Claude; devolve a SQL junto com as linhas |

## Dados de conexão: nada exposto

Todo o cadastro — host, porta, banco, usuário e empresa — fica em `config\conexoes.dat`,
cifrado com o **DPAPI** do Windows (chamado direto pelo Python, dentro do próprio
processo). A senha também fica cifrada:

- **SAP HANA**: no `hdbuserstore` do HANA client, cifrado para o seu usuário do Windows;
- **SQL Server**: dentro do próprio `conexoes.dat`.

A chave vem do seu login no Windows: não existe senha mestra nem arquivo de chave, e o
arquivo copiado para outro usuário ou outro PC **não abre**. Por isso cada pessoa cadastra
as próprias bases; nada disso vai para o git.

A senha é digitada com asteriscos, confirmada e fica só em memória até ser cifrada. As
ferramentas MCP nunca devolvem host, usuário ou senha ao modelo. O `logs\auditoria.jsonl`
registra SQL, empresa, linhas e tempo — nunca credenciais —, mas pode conter **valores**
de filtros: não compartilhe esse arquivo.

## Como a leitura é garantida (defesa em camadas)

1. **Guarda de SQL** (`src/hana_diag_mcp/guarda.py`), antes de abrir conexão: começa com `SELECT`/`WITH`,
   um único comando, e nenhuma palavra proibida fora de literais, identificadores entre
   aspas/colchetes e comentários. Cobre os dois dialetos — inclusive T-SQL sem `;`
   (`SELECT 1 DELETE …`), `EXEC`, `OPENROWSET`, `DBCC`, hints de trava e sequences.
2. **Sessão**:
   - HANA: `SET TRANSACTION READ ONLY` no servidor + rollback;
   - SQL Server: transação implícita (autocommit desligado) que termina **sempre** em
     rollback (o SQL Server não tem transação somente leitura).
   Cada resultado informa a proteção aplicada (`protecao_sessao`).
3. **Usuário do banco** — a camada que realmente fecha a porta. Peça ao cliente um
   usuário só com leitura:

   ```sql
   -- SAP HANA
   CREATE USER LEITOR_SELECT PASSWORD "..." NO FORCE_FIRST_PASSWORD_CHANGE;
   GRANT SELECT ON SCHEMA "SBO_CLIENTE_PRD" TO LEITOR_SELECT;
   GRANT SELECT ON SCHEMA "SBO-COMMON" TO LEITOR_SELECT;   -- para listar as empresas
   GRANT CATALOG READ TO LEITOR_SELECT;

   -- SQL Server (em cada banco de empresa e no SBO-COMMON)
   CREATE LOGIN LEITOR_SELECT WITH PASSWORD = '...';
   USE [SBO_CLIENTE_PRD]; CREATE USER LEITOR_SELECT FOR LOGIN LEITOR_SELECT;
   ALTER ROLE db_datareader ADD MEMBER LEITOR_SELECT;
   ```

### A lista de palavras proibidas não pode ser alterada

- Fica fixa em `src/hana_diag_mcp/guarda.py`, como `frozenset` (imutável); a verificação
  usa uma cópia interna, então nem reatribuir a lista em tempo de execução libera algo.
  Não há configuração, variável de ambiente nem parâmetro de ferramenta que a altere.
- `tests/test_guarda.py` confere a lista exata. Se alguém mexer, os testes falham e o
  `instalar.ps1` **recusa instalar**.
- No GitHub, `.github/CODEOWNERS` exige aprovação do dono do repositório para mudanças na
  guarda (com a proteção da branch `main` ativada).
- Quem altera o código na própria máquina sempre consegue contornar o que é local — por
  isso a camada 3 (usuário só com SELECT) é a que vale na produção do cliente.

## Por que Python (e não Node.js)

O conector chegou a ser reescrito em Node.js, mas voltou para Python:

- **Antivírus**: na versão Node, decifrar o cadastro exigia abrir um PowerShell para chamar
  o DPAPI. O Kaspersky classificou esse comportamento ("script abre PowerShell para
  decifrar credenciais") como cavalo de Troia e apagou os arquivos do conector. Em Python o
  DPAPI é chamado direto, dentro do próprio processo, como qualquer programa do Windows.
- **Funcionou de primeira** com dados reais na versão Python.
- Drivers: HANA pelo ODBC do SAP HANA Client (`HDBODBC`) — o driver Python da SAP
  (`hdbcli`) derrubava o processo nesta estação —, e SQL Server pelo ODBC da Microsoft.
  Os dois via `pyodbc`.
