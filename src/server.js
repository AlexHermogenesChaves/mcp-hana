#!/usr/bin/env node
/**
 * Servidor MCP (stdio): executa SELECTs nas bases SAP B1 dos clientes (SAP HANA e
 * SQL Server) sem precisar acessar o ambiente do cliente — sem área de trabalho
 * remota, sem abrir Studio/SSMS lá. Somente leitura.
 *
 * Nunca escrever em stdout aqui: no transporte stdio ele é o canal do protocolo.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { executar } from "./banco.js";
import * as catalogo from "./catalogo.js";
import { carregar, obter, resumo } from "./config.js";
import { ConsultaBloqueada } from "./guarda.js";

const INSTRUCOES = `\
Conector SOMENTE LEITURA para executar SELECTs nas bases SAP Business One dos clientes
(SAP HANA ou SQL Server), sem precisar acessar o ambiente do cliente.
- Só executa SELECT/WITH. INSERT, UPDATE, DELETE, DDL, EXEC/CALL, controle de transação e
  demais palavras proibidas são recusados antes de chegar ao banco. Não tente contornar:
  se o usuário precisar alterar dados, diga que isso é feito no ambiente do cliente, não aqui.
- Comece por listar_conexoes. Cada conexão é uma empresa B1 e informa o tipo do banco.
- Monte o SELECT para a pergunta do usuário e execute com \`consultar\`. Antes, confira nomes
  com listar_tabelas / descrever_tabela em vez de supor colunas.
- SEMPRE mostre ao usuário a SQL executada (bloco \`\`\`sql, campo \`sql\` do resultado) junto
  com o resultado.
- Dialeto: HANA usa aspas nos nomes ("ItemCode", case-sensitive) e LIMIT/TOP; SQL Server usa
  nomes sem aspas ou [colchetes], TOP n, e não aceita LIMIT. Se uma coluna tiver nome de
  palavra reservada, coloque entre aspas/colchetes.
- B1: campos de usuário em CUFD (TableID, AliasID sem o U_), valores válidos em UFD1,
  tabelas de usuário em OUTB, UDOs em OUDO.
- Traga poucas linhas (max_linhas, TOP) e filtre no WHERE: é a base real do cliente.`;

const servidor = new McpServer({ name: "hana-diag", version: "0.2.0" }, { instructions: INSTRUCOES });

// Todas as ferramentas se declaram somente leitura para o cliente MCP.
const LEITURA = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };

function limite(maxLinhas) {
  const { linhasPadrao, linhasMaximo } = carregar().limites;
  if (!maxLinhas || maxLinhas <= 0) return linhasPadrao;
  return Math.min(maxLinhas, linhasMaximo);
}

/** Registra a ferramenta; erros (recusa da guarda, erro do banco) voltam legíveis ao modelo. */
function ferramenta(nome, descricao, esquema, fn) {
  servidor.registerTool(nome, { description: descricao, inputSchema: esquema, annotations: LEITURA }, async (args) => {
    try {
      const r = await fn(args ?? {});
      return { content: [{ type: "text", text: JSON.stringify(r, null, 2) }] };
    } catch (e) {
      const texto = e instanceof ConsultaBloqueada ? `RECUSADA pela guarda de somente leitura: ${e.message}` : e.message;
      return { isError: true, content: [{ type: "text", text: texto }] };
    }
  });
}

const conexao = z.string().describe("Nome da conexão (ver listar_conexoes)");

ferramenta("listar_conexoes", "Lista as empresas cadastradas: nome, descrição, tipo de banco (SAP HANA/SQL Server) e empresa. Host, usuário e senha nunca são expostos.", {}, () =>
  carregar().conexoes.map(resumo),
);

ferramenta("testar_conexao", "Conecta e devolve usuário, empresa corrente, versão do banco.", { conexao }, ({ conexao: nome }) =>
  catalogo.teste(obter(carregar(), nome)),
);

ferramenta(
  "listar_empresas",
  "Empresas SAP B1 do mesmo servidor da conexão (SBO-COMMON.SRGC ou, sem ele, bancos/schemas com OADM), marcando quais já estão cadastradas. Para usar uma não cadastrada, peça ao usuário: hana-diag-config empresas <conexao>.",
  { conexao },
  async ({ conexao: nome }) => {
    const cfg = carregar();
    const base = obter(cfg, nome);
    const r = await catalogo.empresas(base);
    return { ...r, empresas: r.empresas.map((e) => ({ ...e, conexaoCadastrada: catalogo.jaCadastrada(cfg, base, e.empresa) ?? "" })) };
  },
);

ferramenta(
  "listar_schemas",
  "HANA: schemas visíveis. SQL Server: bancos acessíveis. `filtro` é trecho do nome.",
  { conexao, filtro: z.string().default("") },
  ({ conexao: nome, filtro }) => catalogo.schemas(obter(carregar(), nome), filtro),
);

ferramenta(
  "listar_tabelas",
  "Tabelas e views da empresa. `filtro` é trecho do nome (ex.: '@' para tabelas de usuário). `esquema`: HANA = schema (padrão: o da empresa); SQL Server = schema do banco (padrão: todos, normalmente dbo).",
  { conexao, filtro: z.string().default(""), esquema: z.string().default(""), max_linhas: z.number().int().default(300) },
  ({ conexao: nome, filtro, esquema, max_linhas }) => catalogo.tabelas(obter(carregar(), nome), filtro, esquema, limite(max_linhas)),
);

ferramenta(
  "descrever_tabela",
  "Colunas de uma tabela ou view: nome, tipo, tamanho, nulo e default. `tabela` exatamente como no banco (ex.: OITM, @MINHATAB).",
  { conexao, tabela: z.string(), esquema: z.string().default("") },
  ({ conexao: nome, tabela, esquema }) => catalogo.colunas(obter(carregar(), nome), tabela, esquema),
);

ferramenta(
  "consultar",
  "Executa um SELECT/WITH montado por você para a pergunta do usuário, no dialeto do banco da conexão. Escrita é recusada. Tabelas sem qualificação usam a empresa da conexão. Filtre (WHERE/TOP). O resultado traz `sql`: mostre-a ao usuário junto com as linhas.",
  { conexao, sql: z.string(), max_linhas: z.number().int().default(200) },
  ({ conexao: nome, sql, max_linhas }) => executar(obter(carregar(), nome), sql, [], limite(max_linhas)),
);

await servidor.connect(new StdioServerTransport());
