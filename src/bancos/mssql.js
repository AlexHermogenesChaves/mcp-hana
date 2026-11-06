/**
 * Adaptador SQL Server (mssql/tedious — JavaScript puro, sem driver nativo).
 *
 * O SQL Server não tem transação "somente leitura": a segunda camada aqui é abrir
 * uma transação explícita e SEMPRE terminar com ROLLBACK. Qualquer efeito que
 * escapasse da guarda é desfeito.
 */
import sql from "mssql";

function configuracao(c) {
  const cfg = {
    server: c.host,
    database: c.banco || undefined,
    user: c.usuario,
    password: c.senha,
    connectionTimeout: Math.min(c.timeoutMs, 30000),
    requestTimeout: c.timeoutMs,
    pool: { max: 1, min: 0 },
    options: {
      encrypt: c.encrypt,
      trustServerCertificate: !c.validarCertificado,
      readOnlyIntent: true, // em Always On, vai para a réplica de leitura quando houver
      appName: "hana-diag-mcp (somente leitura)",
    },
  };
  if (c.instancia) cfg.options.instanceName = c.instancia; // porta resolvida pelo SQL Browser
  else cfg.port = c.porta;
  return cfg;
}

/** Executa uma consulta já validada (parâmetros @p0, @p1…). Para no maxLinhas+1. */
export async function executar(c, texto, valores, maxLinhas) {
  const pool = new sql.ConnectionPool(configuracao(c));
  await pool.connect();
  const tx = new sql.Transaction(pool);
  try {
    await tx.begin();
    const req = new sql.Request(tx);
    req.stream = true;
    req.arrayRowMode = true;
    valores.forEach((v, i) => req.input(`p${i}`, v));

    const resultado = await new Promise((resolve, reject) => {
      let colunas = null;
      let conjunto = -1;
      const linhas = [];
      let truncado = false;
      let cancelado = false;
      let erro = null;
      req.on("recordset", (cols) => {
        conjunto += 1;
        if (conjunto === 0) colunas = cols.map((col) => col.name);
      });
      req.on("row", (linha) => {
        if (conjunto !== 0 || cancelado) return; // só o primeiro resultado
        if (linhas.length >= maxLinhas) {
          truncado = true;
          cancelado = true;
          req.cancel();
          return;
        }
        linhas.push(linha);
      });
      req.on("error", (e) => {
        if (cancelado && /cancel/i.test(`${e.code} ${e.message}`)) return;
        erro = erro || e;
      });
      req.on("done", () => (erro ? reject(erro) : resolve({ colunas: colunas || [], linhas, truncado })));
      req.query(texto);
    });
    return { ...resultado, protecao: "transação com rollback" };
  } finally {
    await tx.rollback().catch(() => {}); // nada é confirmado, nunca
    await pool.close().catch(() => {});
  }
}
