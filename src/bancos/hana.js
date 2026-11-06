/**
 * Adaptador SAP HANA (@sap/hana-client — o pacote npm já traz a biblioteca nativa;
 * não depende do HANA client instalado na máquina).
 */
import hana from "@sap/hana-client";

const p = (fn) => new Promise((resolve, reject) => fn((err, valor) => (err ? reject(err) : resolve(valor))));

function parametros(c) {
  const params = {
    serverNode: `${c.host}:${c.porta}`,
    uid: c.usuario,
    pwd: c.senha,
    encrypt: c.encrypt ? "true" : "false",
    sslValidateCertificate: c.validarCertificado ? "true" : "false",
    communicationTimeout: String(c.timeoutMs),
    connectTimeout: String(Math.min(c.timeoutMs, 30000)),
  };
  if (c.banco) params.databaseName = c.banco;
  if (c.schema) params.currentSchema = c.schema;
  return params;
}

/** Executa uma consulta já validada. Lê no máximo maxLinhas+1 linhas do cursor. */
export async function executar(c, sql, valores, maxLinhas) {
  const con = hana.createConnection();
  await p((cb) => con.connect(parametros(c), cb));
  let readOnly = false;
  try {
    con.setAutoCommit(false);
    try {
      // Segunda camada: a transação nasce somente leitura no servidor.
      await p((cb) => con.exec("SET TRANSACTION READ ONLY", cb));
      readOnly = true;
    } catch {
      readOnly = false; // a guarda e o rollback seguem valendo
    }
    const stmt = await p((cb) => con.prepare(sql, cb));
    try {
      const rs = await p((cb) => stmt.execQuery(valores, cb));
      try {
        const colunas = rs.getColumnInfo().map((col) => col.columnName);
        const linhas = [];
        let truncado = false;
        while (await p((cb) => rs.next(cb))) {
          if (linhas.length >= maxLinhas) {
            truncado = true;
            break;
          }
          linhas.push(colunas.map((_, i) => rs.getValue(i)));
        }
        return { colunas, linhas, truncado, protecao: readOnly ? "transação READ ONLY + rollback" : "rollback (READ ONLY recusado pelo servidor)" };
      } finally {
        await p((cb) => rs.close(cb)).catch(() => {});
      }
    } finally {
      await p((cb) => stmt.drop(cb)).catch(() => {});
    }
  } finally {
    await p((cb) => con.rollback(cb)).catch(() => {}); // terceira camada: nada é confirmado
    await p((cb) => con.disconnect(cb)).catch(() => {});
  }
}
