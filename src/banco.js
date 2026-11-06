/**
 * Ponto único de execução: guarda → adaptador do banco → normalização → auditoria.
 * Nenhum outro módulo fala com o banco.
 */
import fs from "node:fs";
import path from "node:path";
import { PASTA_LOGS } from "./config.js";
import { segmentos, validarSomenteLeitura } from "./guarda.js";
import * as hana from "./bancos/hana.js";
import * as mssql from "./bancos/mssql.js";

const ADAPTADORES = { hana, mssql };

/** Troca os '?' (fora de literais/comentários) por @p0, @p1… — parâmetros do SQL Server. */
export function placeholdersMssql(sql) {
  let n = 0;
  return segmentos(sql)
    .map((s) => (s.tipo === "codigo" ? s.texto.replace(/\?/g, () => `@p${n++}`) : s.texto))
    .join("");
}

export function valor(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === "bigint") return Number.isSafeInteger(Number(v)) ? Number(v) : v.toString();
  if (Buffer.isBuffer(v)) return `0x${v.subarray(0, 64).toString("hex")}${v.length > 64 ? "…" : ""}`;
  if (v instanceof Date) return v.toISOString();
  if (typeof v === "string" && v.length > 4000) return `${v.slice(0, 4000)}…`;
  if (typeof v === "object") return String(v);
  return v;
}

function auditar(registro) {
  try {
    fs.mkdirSync(PASTA_LOGS, { recursive: true });
    fs.appendFileSync(path.join(PASTA_LOGS, "auditoria.jsonl"), `${JSON.stringify(registro)}\n`, "utf8");
  } catch (e) {
    process.stderr.write(`Falha ao gravar auditoria: ${e.message}\n`);
  }
}

/** Executa uma consulta de leitura. `sql` usa '?' como parâmetro nos dois bancos. */
export async function executar(c, sql, valores = [], maxLinhas = 200) {
  const sqlOk = validarSomenteLeitura(sql);
  if (!c.senha) {
    throw new Error(`A conexão '${c.nome}' está sem senha cadastrada. Rode: hana-diag-config senha ${c.nome}`);
  }
  const inicio = Date.now();
  const registro = {
    quando: new Date().toISOString(),
    conexao: c.nome,
    tipo: c.tipo,
    sql: sqlOk,
    parametros: valores.map(String),
  };
  try {
    const textoBanco = c.tipo === "mssql" && valores.length ? placeholdersMssql(sqlOk) : sqlOk;
    const r = await ADAPTADORES[c.tipo].executar(c, textoBanco, valores, maxLinhas);
    const linhas = r.linhas.map((l) => l.map(valor));
    const tempoMs = Date.now() - inicio;
    Object.assign(registro, { status: "ok", linhas: linhas.length, truncado: r.truncado, ms: tempoMs, protecao: r.protecao });
    return {
      conexao: c.nome,
      sql: sqlOk,
      colunas: r.colunas,
      linhas,
      totalLinhas: linhas.length,
      truncado: r.truncado,
      tempoMs,
      protecaoSessao: r.protecao,
    };
  } catch (e) {
    Object.assign(registro, { status: "erro", erro: e.message, ms: Date.now() - inicio });
    throw e;
  } finally {
    auditar(registro);
  }
}
