/**
 * Consultas de estrutura e de empresas, por dialeto. Todas passam pela mesma guarda
 * de leitura em banco.executar.
 */
import { executar } from "./banco.js";
import { mesmoServidor, novaConexao } from "./config.js";

const SQL = {
  hana: {
    teste:
      'SELECT CURRENT_USER AS "Usuario", CURRENT_SCHEMA AS "Empresa", ' +
      '(SELECT "VERSION" FROM "SYS"."M_DATABASE") AS "Versao", ' +
      '(SELECT "DATABASE_NAME" FROM "SYS"."M_DATABASE") AS "Banco" FROM DUMMY',
    schemas:
      'SELECT "SCHEMA_NAME", "SCHEMA_OWNER" FROM "SYS"."SCHEMAS" ' +
      "WHERE UPPER(\"SCHEMA_NAME\") LIKE '%' || UPPER(?) || '%' ORDER BY \"SCHEMA_NAME\"",
    tabelas:
      'SELECT "TABLE_NAME" AS "Objeto", CAST(\'TABELA\' AS NVARCHAR(10)) AS "Tipo" FROM "SYS"."TABLES" ' +
      "WHERE \"SCHEMA_NAME\" = ? AND UPPER(\"TABLE_NAME\") LIKE '%' || UPPER(?) || '%' " +
      'UNION ALL SELECT "VIEW_NAME", CAST(\'VIEW\' AS NVARCHAR(10)) FROM "SYS"."VIEWS" ' +
      "WHERE \"SCHEMA_NAME\" = ? AND UPPER(\"VIEW_NAME\") LIKE '%' || UPPER(?) || '%' ORDER BY 1",
    colunas:
      'SELECT "COLUMN_NAME", "DATA_TYPE_NAME", "LENGTH", "SCALE", "IS_NULLABLE", "DEFAULT_VALUE", "POSITION" ' +
      'FROM "SYS"."TABLE_COLUMNS" WHERE "SCHEMA_NAME" = ? AND "TABLE_NAME" = ? UNION ALL ' +
      'SELECT "COLUMN_NAME", "DATA_TYPE_NAME", "LENGTH", "SCALE", "IS_NULLABLE", "DEFAULT_VALUE", "POSITION" ' +
      'FROM "SYS"."VIEW_COLUMNS" WHERE "SCHEMA_NAME" = ? AND "VIEW_NAME" = ? ORDER BY "POSITION"',
    srgc: 'SELECT "dbName", "cmpName" FROM "SBO-COMMON"."SRGC" ORDER BY "dbName"',
    candidatas: "SELECT \"SCHEMA_NAME\" FROM \"SYS\".\"TABLES\" WHERE \"TABLE_NAME\" = 'OADM' ORDER BY 1",
    nomeEmpresa: (e) => `SELECT MAX("CompnyName") FROM "${e}"."OADM"`,
  },
  mssql: {
    teste:
      "SELECT SUSER_SNAME() AS Usuario, DB_NAME() AS Empresa, " +
      "CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(50)) AS Versao, @@SERVERNAME AS Servidor",
    schemas:
      "SELECT name AS Banco FROM sys.databases WHERE HAS_DBACCESS(name) = 1 " +
      "AND UPPER(name) LIKE '%' + UPPER(?) + '%' ORDER BY name",
    tabelas:
      "SELECT TABLE_SCHEMA AS Esquema, TABLE_NAME AS Objeto, " +
      "CASE TABLE_TYPE WHEN 'VIEW' THEN 'VIEW' ELSE 'TABELA' END AS Tipo " +
      "FROM INFORMATION_SCHEMA.TABLES WHERE (? = '' OR TABLE_SCHEMA = ?) " +
      "AND UPPER(TABLE_NAME) LIKE '%' + UPPER(?) + '%' ORDER BY TABLE_NAME",
    colunas:
      "SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_SCALE, IS_NULLABLE, COLUMN_DEFAULT, " +
      "ORDINAL_POSITION FROM INFORMATION_SCHEMA.COLUMNS WHERE (? = '' OR TABLE_SCHEMA = ?) AND TABLE_NAME = ? " +
      "ORDER BY ORDINAL_POSITION",
    srgc: "SELECT dbName, cmpName FROM [SBO-COMMON].dbo.SRGC ORDER BY dbName",
    candidatas: "SELECT name FROM sys.databases WHERE database_id > 4 AND state = 0 AND HAS_DBACCESS(name) = 1 ORDER BY name",
    nomeEmpresa: (e) => `SELECT MAX(CompnyName) FROM [${e}].dbo.OADM`,
  },
};

export const teste = (c) => executar(c, SQL[c.tipo].teste);

export const schemas = (c, filtro = "", max = 1000) => executar(c, SQL[c.tipo].schemas, [filtro], max);

export function tabelas(c, filtro = "", esquema = "", max = 300) {
  if (c.tipo === "hana") {
    const alvo = esquema || c.schema;
    if (!alvo) throw new Error("Informe o schema (a conexão não tem schema padrão).");
    return executar(c, SQL.hana.tabelas, [alvo, filtro, alvo, filtro], max);
  }
  return executar(c, SQL.mssql.tabelas, [esquema, esquema, filtro], max);
}

export function colunas(c, tabela, esquema = "") {
  if (c.tipo === "hana") {
    const alvo = esquema || c.schema;
    if (!alvo) throw new Error("Informe o schema (a conexão não tem schema padrão).");
    return executar(c, SQL.hana.colunas, [alvo, tabela, alvo, tabela], 2000);
  }
  return executar(c, SQL.mssql.colunas, [esquema, esquema, tabela], 2000);
}

const SEGURO = /^[^"'\[\];\x00]{1,128}$/;

/**
 * Empresas B1 do servidor da conexão: [{empresa, nome}] e a origem da lista.
 * HANA: empresa = schema; SQL Server: empresa = banco.
 * Fonte: SBO-COMMON.SRGC; sem ele, procura bancos/schemas que tenham a OADM.
 */
export async function empresas(c) {
  const sqls = SQL[c.tipo];
  try {
    const r = await executar(c, sqls.srgc, [], 1000);
    return { origem: "SBO-COMMON.SRGC", empresas: r.linhas.map(([empresa, nome]) => ({ empresa: String(empresa), nome: nome ?? "" })) };
  } catch {
    // tenant/servidor sem SBO-COMMON, ou sem permissão nele
  }
  const candidatas = (await executar(c, sqls.candidatas, [], 1000)).linhas.map(([e]) => String(e)).filter((e) => SEGURO.test(e));
  const lista = [];
  for (const e of candidatas) {
    try {
      const r = await executar(c, sqls.nomeEmpresa(e), [], 1);
      lista.push({ empresa: e, nome: r.linhas[0]?.[0] ?? "" });
    } catch {
      if (c.tipo === "hana") lista.push({ empresa: e, nome: "" }); // schema tem OADM, só sem SELECT nela
      // SQL Server: banco sem OADM não é empresa B1 — fica de fora
    }
  }
  return { origem: `${c.tipo === "hana" ? "schemas" : "bancos"} com OADM (SBO-COMMON indisponível)`, empresas: lista };
}

/** Nome da conexão já cadastrada para essa empresa no mesmo servidor. */
export function jaCadastrada(cfg, base, empresa) {
  const alvo = empresa.toUpperCase();
  const achou = cfg.conexoes.find(
    (x) => mesmoServidor(x, base) && (x.tipo === "hana" ? x.schema : x.banco).toUpperCase() === alvo,
  );
  return achou?.nome ?? null;
}

/** Mesma credencial e servidor da base, apontando para outra empresa. */
export function conexaoParaEmpresa(base, nome, empresa, descricao) {
  return novaConexao({
    ...base,
    nome,
    descricao,
    ...(base.tipo === "hana" ? { schema: empresa } : { banco: empresa }),
  });
}

export function sugerirNome(empresa, existentes) {
  let base = empresa.replace(/^SBO[_-]?/i, "").replace(/[^A-Za-z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "empresa";
  base = base.slice(0, 36);
  let nome = base;
  for (let n = 2; existentes.has(nome); n += 1) nome = `${base}-${n}`;
  return nome;
}

export function interpretarSelecao(texto, total) {
  const t = texto.trim().toLowerCase();
  if (["todas", "todos", "*"].includes(t)) return [...Array(total).keys()];
  const escolhidos = new Set();
  for (const parte of t.split(",").map((x) => x.trim()).filter(Boolean)) {
    const m = parte.match(/^(\d+)(?:-(\d+))?$/);
    if (!m) throw new Error(`Seleção inválida: ${parte}`);
    let [a, b] = [Number(m[1]), Number(m[2] ?? m[1])];
    if (a > b) [a, b] = [b, a];
    for (let i = a; i <= b; i += 1) {
      if (i < 1 || i > total) throw new Error(`Número fora da lista: ${i} (1 a ${total}).`);
      escolhidos.add(i - 1);
    }
  }
  return [...escolhidos].sort((x, y) => x - y);
}
