/**
 * Cadastro de bases, gravado CIFRADO (DPAPI) em config/conexoes.dat.
 *
 * Nada fica em texto puro no disco: host, porta, banco, usuário, schema e a senha.
 * Cada conexão é uma empresa do SAP B1:
 *   - HANA: servidor + tenant (banco) + schema da empresa;
 *   - SQL Server: servidor (+ instância) + banco da empresa.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { cifrar, decifrar } from "./cofre.js";

export const RAIZ = process.env.HANA_DIAG_HOME || path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const ARQUIVO_CONEXOES = path.join(RAIZ, "config", "conexoes.dat");
export const PASTA_LOGS = path.join(RAIZ, "logs");

export const TIPOS = {
  hana: { rotulo: "SAP HANA", portaPadrao: 30015 },
  mssql: { rotulo: "SQL Server", portaPadrao: 1433 },
};

const NOME_VALIDO = /^[A-Za-z0-9_-]{1,40}$/;
// Schema e banco entram em texto de SQL/conexão: aspas, colchetes e ';' ficam proibidos.
const IDENT_VALIDO = /^[^"'\[\];\x00]{1,128}$/;

export const LIMITES_PADRAO = { linhasPadrao: 200, linhasMaximo: 5000 };

/** Normaliza um registro (inclusive do formato da versão Python). */
export function novaConexao(dados) {
  const c = {
    nome: dados.nome,
    descricao: dados.descricao ?? "",
    tipo: dados.tipo ?? "hana",
    host: dados.host ?? "",
    porta: Number(dados.porta ?? TIPOS[dados.tipo ?? "hana"]?.portaPadrao ?? 0),
    banco: dados.banco ?? "",
    instancia: dados.instancia ?? "",
    usuario: dados.usuario ?? "",
    senha: dados.senha ?? "",
    schema: dados.schema ?? "",
    encrypt: Boolean(dados.encrypt),
    validarCertificado: Boolean(dados.validarCertificado ?? dados.validar_certificado ?? false),
    timeoutMs: Number(dados.timeoutMs ?? dados.timeout_ms ?? 60000),
  };
  validar(c);
  return c;
}

export function validar(c) {
  if (!NOME_VALIDO.test(c.nome || "")) throw new Error("Nome da conexão: use só letras, números, '_' e '-' (até 40).");
  if (!TIPOS[c.tipo]) throw new Error(`Tipo de banco inválido: ${c.tipo} (use hana ou mssql).`);
  if (!c.host || !c.usuario) throw new Error(`Conexão ${c.nome}: host e usuário são obrigatórios.`);
  if (!Number.isInteger(c.porta) || c.porta <= 0 || c.porta > 65535) throw new Error(`Conexão ${c.nome}: porta inválida.`);
  for (const campo of ["schema", "banco", "instancia"]) {
    if (c[campo] && !IDENT_VALIDO.test(c[campo])) {
      throw new Error(`Conexão ${c.nome}: ${campo} inválido (não pode conter aspas, colchetes nem ';').`);
    }
  }
}

/** Mesmo servidor e mesma credencial: as empresas de um servidor compartilham login. */
export function mesmoServidor(a, b) {
  return (
    a.tipo === b.tipo &&
    a.host.toLowerCase() === b.host.toLowerCase() &&
    a.porta === b.porta &&
    a.instancia.toLowerCase() === b.instancia.toLowerCase() &&
    a.usuario.toLowerCase() === b.usuario.toLowerCase() &&
    (a.tipo === "mssql" || a.banco.toLowerCase() === b.banco.toLowerCase())
  );
}

export function obter(cfg, nome) {
  const c = cfg.conexoes.find((x) => x.nome.toLowerCase() === String(nome).toLowerCase());
  if (!c) {
    const nomes = cfg.conexoes.map((x) => x.nome).join(", ") || "(nenhuma cadastrada)";
    throw new Error(`Conexão '${nome}' não encontrada. Disponíveis: ${nomes}.`);
  }
  return c;
}

let cache = null; // { chave, cfg } — decifrar custa ~0,5 s (PowerShell)

export function carregar(arquivo = ARQUIVO_CONEXOES) {
  if (!fs.existsSync(arquivo)) return { conexoes: [], limites: { ...LIMITES_PADRAO } };
  const st = fs.statSync(arquivo);
  const chave = `${arquivo}|${st.mtimeMs}|${st.size}`;
  if (cache?.chave === chave) return structuredClone(cache.cfg);

  const dados = JSON.parse(decifrar(fs.readFileSync(arquivo)).toString("utf8"));
  const limites = dados.limites ?? {};
  const cfg = {
    conexoes: (dados.conexoes ?? []).map(novaConexao),
    limites: {
      linhasPadrao: limites.linhasPadrao ?? limites.linhas_padrao ?? LIMITES_PADRAO.linhasPadrao,
      linhasMaximo: limites.linhasMaximo ?? limites.linhas_maximo ?? LIMITES_PADRAO.linhasMaximo,
    },
  };
  cache = { chave, cfg };
  return structuredClone(cfg);
}

export function salvar(cfg, arquivo = ARQUIVO_CONEXOES) {
  cfg.conexoes.forEach(validar);
  fs.mkdirSync(path.dirname(arquivo), { recursive: true });
  const conteudo = cifrar(Buffer.from(JSON.stringify({ limites: cfg.limites, conexoes: cfg.conexoes }), "utf8"));
  const temporario = `${arquivo}.tmp`;
  fs.writeFileSync(temporario, conteudo);
  fs.renameSync(temporario, arquivo); // troca atômica: nunca fica meio gravado
  cache = null;
}

/** Visão segura para o modelo/listagens: nunca host, usuário ou senha. */
export function resumo(c) {
  return {
    nome: c.nome,
    descricao: c.descricao,
    tipo: TIPOS[c.tipo].rotulo,
    empresa: c.tipo === "hana" ? c.schema : c.banco,
    senhaCadastrada: Boolean(c.senha),
  };
}
