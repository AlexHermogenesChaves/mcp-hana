# hana-diag — SELECTs nas bases SAP B1 dos clientes, sem acessar o ambiente do cliente

Conector MCP para o Claude executar **consultas SELECT** nas bases SAP Business One dos
clientes — **SAP HANA** ou **SQL Server** — direto da sua máquina, sem abrir área de
trabalho remota, HANA Studio ou SSMS no ambiente do cliente. Você pergunta em linguagem
natural; o Claude monta o `SELECT`, executa e mostra **a SQL e o resultado**.

É **somente leitura**: nenhum INSERT, UPDATE, DELETE, DDL, EXEC/CALL ou controle de
transação chega ao banco.

> Ainda é preciso alcançar o servidor de banco pela rede (normalmente pela VPN do
> cliente). O que deixa de ser necessário é entrar no ambiente dele.

## Instalação (cada pessoa, no próprio PC)

Pré-requisitos: Windows, **Node.js 20+** (`winget install OpenJS.NodeJS.LTS`), Claude
Code e `git`. Não é preciso instalar SAP HANA Client nem driver de SQL Server: os drivers
vêm pelo npm.

```powershell
git clone https://github.com/AlexHermogenesChaves/mcp-hana.git C:\Projetos_SPS\hana-diag-mcp
cd C:\Projetos_SPS\hana-diag-mcp
.\instalar.ps1 -RegistrarClaude
```

O instalador confere o Node, roda `npm ci`, roda os testes (inclusive a conferência da
lista de palavras proibidas — se ela foi alterada, **a instalação é recusada**) e, com
`-RegistrarClaude`, registra o servidor `hana-diag` no Claude Code.

**Atualizar:** `git pull` e `.\instalar.ps1 -RegistrarClaude` de novo. O seu cadastro não
é afetado.

Outro cliente MCP: comando `node`, argumento `<pasta do clone>\src\server.js` (stdio).

## Cadastrar empresas

Cada **empresa do B1** é uma conexão: no HANA, um schema; no SQL Server, um banco.

```powershell
.\hana-diag-config.cmd adicionar
```

1. Escolha o tipo (`hana` ou `mssql`) e informe host, porta (ou instância nomeada, no
   SQL Server), tenant (HANA), empresa e usuário.
2. Digite a senha **do banco** (aparece `*` a cada tecla) e confirme. O configurador
   mostra "senha recebida (N caracteres)".
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

Todo o cadastro — host, porta, banco, usuário, empresa **e senha** — fica em
`config\conexoes.dat`, cifrado com o **DPAPI** do Windows. A chave vem do seu login no
Windows: não existe senha mestra nem arquivo de chave, e o arquivo copiado para outro
usuário ou outro PC **não abre**. Por isso cada pessoa cadastra as próprias bases; nada
disso vai para o git.

A senha é digitada com asteriscos, confirmada e fica só em memória até ser cifrada. As
ferramentas MCP nunca devolvem host, usuário ou senha ao modelo. O `logs\auditoria.jsonl`
registra SQL, empresa, linhas e tempo — nunca credenciais —, mas pode conter **valores**
de filtros: não compartilhe esse arquivo.

## Como a leitura é garantida (defesa em camadas)

1. **Guarda de SQL** (`src/guarda.js`), antes de abrir conexão: começa com `SELECT`/`WITH`,
   um único comando, e nenhuma palavra proibida fora de literais, identificadores entre
   aspas/colchetes e comentários. Cobre os dois dialetos — inclusive T-SQL sem `;`
   (`SELECT 1 DELETE …`), `EXEC`, `OPENROWSET`, `DBCC`, hints de trava e sequences.
2. **Sessão**:
   - HANA: `SET TRANSACTION READ ONLY` no servidor + rollback;
   - SQL Server: transação explícita que termina **sempre** em rollback (o SQL Server não
     tem transação somente leitura).
   Cada resultado informa a proteção aplicada (`protecaoSessao`).
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

- Fica fixa em `src/guarda.js`, congelada em tempo de execução; não há configuração,
  variável de ambiente nem parâmetro de ferramenta que a altere.
- `test/guarda.test.js` confere a lista exata. Se alguém mexer, os testes falham e o
  `instalar.ps1` **recusa instalar**.
- No GitHub, `.github/CODEOWNERS` exige aprovação do dono do repositório para mudanças na
  guarda (com a proteção da branch `main` ativada).
- Quem altera o código na própria máquina sempre consegue contornar o que é local — por
  isso a camada 3 (usuário só com SELECT) é a que vale na produção do cliente.

## Por que Node.js

Drivers sem instalação externa (`@sap/hana-client` traz a biblioteca do HANA; `mssql` é
JavaScript puro) e um só runtime para a equipe. A versão anterior em Python dependia do
SAP HANA Client instalado e do ODBC, porque o driver Python da SAP (`hdbcli`) derrubava o
processo nesta estação.
