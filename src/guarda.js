/**
 * Guarda de somente leitura (HANA e SQL Server).
 *
 * Toda SQL que sai deste conector passa por aqui antes de chegar no banco do cliente.
 * A regra é por lista de permissão: começa com SELECT ou WITH, e nenhuma palavra-chave
 * de escrita, DDL, execução, controle de transação/sessão ou trava aparece como código
 * — fora de literais, identificadores entre aspas/colchetes e comentários.
 *
 * Primeira de três camadas (ver README): a sessão ainda roda em transação somente
 * leitura (HANA) ou com rollback garantido (SQL Server), e o usuário do banco deve
 * ter só SELECT.
 */

export class ConsultaBloqueada extends Error {
  constructor(mensagem) {
    super(mensagem);
    this.name = "ConsultaBloqueada";
  }
}

/*
 * LISTA FIXA. Não existe configuração, variável de ambiente nem parâmetro de
 * ferramenta que altere estas listas — de propósito. O teste
 * test/guarda.test.js confere a lista exata e o instalar.ps1 recusa instalar se
 * ela tiver sido mexida. Mudança aqui só com aprovação do dono do repositório
 * (CODEOWNERS).
 *
 * REPLACE fica de fora: como comando só valeria no início (que já tem de ser
 * SELECT/WITH); no meio da consulta é a função de texto. "NEXT" também, por causa
 * do OFFSET … FETCH NEXT do T-SQL — a sequence é barrada pelo par NEXT VALUE.
 */
export const INICIOS_PERMITIDOS = Object.freeze(["SELECT", "WITH"]);

export const PALAVRAS_PROIBIDAS = Object.freeze([
  // DML
  "INSERT", "UPDATE", "DELETE", "UPSERT", "MERGE", "TRUNCATE", "INTO",
  "WRITETEXT", "UPDATETEXT",
  // DDL e privilégios
  "CREATE", "ALTER", "DROP", "RENAME", "GRANT", "REVOKE", "DENY", "COMMENT",
  // execução de código
  "CALL", "EXEC", "EXECUTE", "DO", "DECLARE",
  "OPENROWSET", "OPENQUERY", "OPENDATASOURCE", "OPENXML", "BULK",
  // transação, sessão e travas
  "COMMIT", "ROLLBACK", "SAVEPOINT", "BEGIN", "LOCK", "SET", "USE",
  "CONNECT", "DISCONNECT", "SETUSER", "REVERT",
  "UPDLOCK", "XLOCK", "TABLOCKX", "HOLDLOCK",
  // sequences (avançar sequence é escrita)
  "NEXTVAL",
  // administração, carga e exportação
  "LOAD", "UNLOAD", "IMPORT", "EXPORT", "BACKUP", "RESTORE", "RECOVER", "REFRESH",
  "ANNOTATE", "DBCC", "KILL", "SHUTDOWN", "RECONFIGURE", "CHECKPOINT", "WAITFOR",
]);

// Conjuntos internos usados na verificação: não são exportados, ninguém de fora os altera.
const INICIOS = new Set(INICIOS_PERMITIDOS);
const PROIBIDAS = new Set(PALAVRAS_PROIBIDAS);

const PALAVRA = /[A-Za-z_][A-Za-z0-9_$#]*/g;

/** Quebra a SQL em trechos {tipo: codigo|literal|identificador|comentario, texto}. */
export function segmentos(sql) {
  const saida = [];
  let buffer = "";
  let i = 0;
  const n = sql.length;
  const despejar = () => {
    if (buffer) saida.push({ tipo: "codigo", texto: buffer });
    buffer = "";
  };
  const fechar = (abre, fecha, tipo, erro) => {
    let j = i + 1;
    for (;;) {
      if (j >= n) throw new ConsultaBloqueada(erro);
      if (sql[j] === fecha) {
        if (sql[j + 1] === fecha) { j += 2; continue; } // '' "" ]] = escape
        break;
      }
      j += 1;
    }
    saida.push({ tipo, texto: sql.slice(i, j + 1) });
    i = j + 1;
  };

  while (i < n) {
    const c = sql[i];
    if (c === "'") {
      despejar();
      fechar("'", "'", "literal", "Literal entre aspas simples não foi fechado.");
    } else if (c === '"') {
      despejar();
      fechar('"', '"', "identificador", "Identificador entre aspas duplas não foi fechado.");
    } else if (c === "[") {
      despejar();
      fechar("[", "]", "identificador", "Identificador entre colchetes não foi fechado.");
    } else if (sql.startsWith("--", i)) {
      despejar();
      let fim = sql.indexOf("\n", i);
      if (fim === -1) fim = n;
      saida.push({ tipo: "comentario", texto: sql.slice(i, fim) });
      i = fim;
    } else if (sql.startsWith("/*", i)) {
      despejar();
      const fim = sql.indexOf("*/", i + 2);
      if (fim === -1) throw new ConsultaBloqueada("Comentário /* */ não foi fechado.");
      saida.push({ tipo: "comentario", texto: sql.slice(i, fim + 2) });
      i = fim + 2;
    } else {
      buffer += c;
      i += 1;
    }
  }
  despejar();
  return saida;
}

/** Devolve a SQL pronta para executar ou lança ConsultaBloqueada. */
export function validarSomenteLeitura(sql) {
  if (typeof sql !== "string" || !sql.trim()) throw new ConsultaBloqueada("Consulta vazia.");

  const partes = segmentos(sql);
  const indicesCodigo = partes
    .map((p, k) => (p.tipo === "codigo" && p.texto.trim() ? k : -1))
    .filter((k) => k !== -1);

  if (indicesCodigo.length) {
    // ';' inicial (";WITH" do T-SQL) e um único ';' final são tolerados.
    const primeiro = indicesCodigo[0];
    partes[primeiro] = { tipo: "codigo", texto: partes[primeiro].texto.replace(/^\s*;/, "") };
    const ultimo = indicesCodigo[indicesCodigo.length - 1];
    partes[ultimo] = { tipo: "codigo", texto: partes[ultimo].texto.replace(/;\s*$/, "") };
  }
  if (partes.some((p) => p.tipo === "codigo" && p.texto.includes(";"))) {
    throw new ConsultaBloqueada("Só é permitido um comando por execução (encontrado ';' no meio).");
  }

  const codigo = partes.filter((p) => p.tipo === "codigo").map((p) => p.texto).join(" ");
  const palavras = (codigo.match(PALAVRA) || []).map((p) => p.toUpperCase());
  if (!palavras.length) throw new ConsultaBloqueada("Nenhum comando encontrado.");
  if (!INICIOS.has(palavras[0])) {
    throw new ConsultaBloqueada(`Só consultas SELECT ou WITH são permitidas; o comando começa com ${palavras[0]}.`);
  }

  const proibidas = new Set(palavras.filter((p) => PROIBIDAS.has(p)));
  for (let k = 0; k + 1 < palavras.length; k += 1) {
    if (palavras[k] === "NEXT" && palavras[k + 1] === "VALUE") proibidas.add("NEXT VALUE FOR");
  }
  if (proibidas.size) {
    throw new ConsultaBloqueada(
      `Consulta bloqueada: contém palavra-chave de escrita/controle: ${[...proibidas].sort().join(", ")}.`,
    );
  }
  return partes.map((p) => p.texto).join("").trim();
}
