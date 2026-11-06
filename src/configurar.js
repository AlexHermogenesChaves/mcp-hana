#!/usr/bin/env node
/**
 * Configurador das bases (empresas) que o conector pode consultar.
 *
 *   hana-diag-config                  menu
 *   hana-diag-config listar
 *   hana-diag-config adicionar
 *   hana-diag-config empresas <nome>  lista as empresas B1 do servidor e cadastra as escolhidas
 *   hana-diag-config testar <nome>
 *   hana-diag-config senha <nome>     troca a senha (sem recadastrar)
 *   hana-diag-config remover <nome>
 *
 * Tudo é gravado cifrado com DPAPI em config/conexoes.dat — host, usuário, empresa e
 * senha. A senha é digitada com asteriscos e confirmada; fica só em memória até ser
 * cifrada.
 */
import * as catalogo from "./catalogo.js";
import { ARQUIVO_CONEXOES, TIPOS, carregar, mesmoServidor, novaConexao, obter, salvar } from "./config.js";
import { io, pedirSenha } from "./terminal.js";

const USUARIOS_ADMINISTRATIVOS = new Set(["B1ADMIN", "SYSTEM", "SAPHANADB", "SBOCOMMON", "SA"]);

const empresaDe = (c) => (c.tipo === "hana" ? c.schema : c.banco);

/** Pontos de troca para os testes (simular o banco sem conexão real). */
export const deps = { empresas: (c) => catalogo.empresas(c) };

export function listar() {
  const cfg = carregar();
  if (!cfg.conexoes.length) {
    console.log("Nenhuma base cadastrada. Use: hana-diag-config adicionar");
    return;
  }
  console.log(`${"NOME".padEnd(20)} ${"BANCO".padEnd(11)} ${"EMPRESA".padEnd(30)} ${"SENHA".padEnd(8)} DESCRIÇÃO`);
  for (const c of cfg.conexoes) {
    console.log(
      `${c.nome.padEnd(20)} ${TIPOS[c.tipo].rotulo.padEnd(11)} ${empresaDe(c).padEnd(30)} ${(c.senha ? "ok" : "FALTA").padEnd(8)} ${c.descricao}`,
    );
  }
}

export async function testar(nome) {
  const c = obter(carregar(), nome);
  try {
    const r = await catalogo.teste(c);
    const [usuario, empresa, versao] = r.linhas[0];
    console.log(`OK em ${r.tempoMs} ms — usuário ${usuario}, empresa ${empresa}, ${TIPOS[c.tipo].rotulo} ${versao}`);
    console.log(`  proteção da sessão: ${r.protecaoSessao}.`);
    return true;
  } catch (e) {
    console.log(`FALHOU: ${e.message}`);
    return false;
  }
}

export async function adicionar() {
  const cfg = carregar();
  console.log("Nova base para consultas SELECT (somente leitura)\n");
  let tipo = "";
  while (!TIPOS[tipo]) tipo = (await io.perguntar("Tipo de banco (hana / mssql)", "hana")).toLowerCase();

  const nome = await io.perguntar("Nome curto (ex.: cliente-prd)", "", true);
  if (cfg.conexoes.some((c) => c.nome.toLowerCase() === nome.toLowerCase())) {
    if (!(await io.sim(`'${nome}' já existe. Substituir?`))) return;
    cfg.conexoes = cfg.conexoes.filter((c) => c.nome.toLowerCase() !== nome.toLowerCase());
  }
  const dados = { nome, tipo };
  dados.descricao = await io.perguntar("Descrição (cliente / ambiente)");
  dados.host = await io.perguntar(`Host do ${TIPOS[tipo].rotulo}`, "", true);
  if (tipo === "mssql") dados.instancia = await io.perguntar("Instância nomeada (vazio se padrão; ex.: SQLEXPRESS)");
  if (!dados.instancia) dados.porta = Number(await io.perguntar("Porta", String(TIPOS[tipo].portaPadrao)));
  if (tipo === "hana") {
    dados.banco = await io.perguntar("Tenant/database (vazio se single-container; ex.: NDB)");
    dados.schema = await io.perguntar("Schema da empresa B1 (ex.: SBO_CLIENTE_PRD)", "", true);
  } else {
    dados.banco = await io.perguntar("Banco da empresa B1 (ex.: SBO_CLIENTE_PRD)", "", true);
  }
  dados.usuario = await io.perguntar("Usuário do banco (de preferência um usuário só com SELECT)", "", true);
  if (USUARIOS_ADMINISTRATIVOS.has(dados.usuario.toUpperCase())) {
    console.log(
      `  Atenção: ${dados.usuario} é usuário administrativo com permissão de escrita. O conector continua\n` +
        "  só lendo (guarda + sessão protegida + rollback), mas o ideal é um usuário só com SELECT.",
    );
  }
  dados.encrypt = await io.sim("Conexão com TLS (encrypt)?", false);

  const previa = novaConexao(dados); // valida antes de pedir a senha
  console.log();
  previa.senha = await pedirSenha(previa.usuario);
  cfg.conexoes.push(previa);
  salvar(cfg);
  console.log(`\nBase '${nome}' salva (cifrada) em ${ARQUIVO_CONEXOES}.`);

  if ((await io.sim("Testar a conexão agora?", true)) && (await testar(nome))) {
    if (await io.sim("Cadastrar outras empresas deste mesmo servidor (sem pedir a senha de novo)?", true)) {
      await cadastrarEmpresas(nome);
    }
  }
}

export async function trocarSenha(nome) {
  const cfg = carregar();
  const c = obter(cfg, nome);
  const mesmas = cfg.conexoes.filter((x) => x !== c && mesmoServidor(x, c));
  console.log(`Trocar a senha de ${c.usuario} em ${c.host}${c.instancia ? `\\${c.instancia}` : `:${c.porta}`}`);
  const senha = await pedirSenha(c.usuario);
  c.senha = senha;
  if (mesmas.length && (await io.sim(`Aplicar também a ${mesmas.map((x) => x.nome).join(", ")} (mesmo servidor e usuário)?`, true))) {
    mesmas.forEach((x) => { x.senha = senha; });
  }
  salvar(cfg);
  console.log("  senha gravada (cifrada).");
  if (await io.sim("Testar a conexão agora?", true)) await testar(c.nome);
}

export async function cadastrarEmpresas(nomeBase) {
  const cfg = carregar();
  const base = obter(cfg, nomeBase);
  console.log(`\nBuscando as empresas do servidor de '${base.nome}'...`);
  let r;
  try {
    r = await deps.empresas(base);
  } catch (e) {
    console.log(`Não foi possível listar as empresas: ${e.message}`);
    return;
  }
  if (!r.empresas.length) {
    console.log("Nenhuma empresa B1 visível para este usuário.");
    return;
  }
  console.log(`Origem: ${r.origem}\n`);
  console.log(`  ${"#".padStart(3)}  ${"EMPRESA".padEnd(32)} ${"NOME".padEnd(40)} SITUAÇÃO`);
  r.empresas.forEach((e, i) => {
    const ja = catalogo.jaCadastrada(cfg, base, e.empresa);
    console.log(`  ${String(i + 1).padStart(3)}  ${e.empresa.padEnd(32)} ${String(e.nome).slice(0, 40).padEnd(40)} ${ja ? `já cadastrada (${ja})` : ""}`);
  });

  const texto = await io.perguntar("\nQuais cadastrar? (ex.: 1,3-5 · todas · Enter cancela)");
  if (!texto) return;
  let escolhidas;
  try {
    escolhidas = catalogo.interpretarSelecao(texto, r.empresas.length);
  } catch (e) {
    console.log(e.message);
    return;
  }

  const existentes = new Set(cfg.conexoes.map((c) => c.nome.toLowerCase()));
  const novas = [];
  for (const i of escolhidas) {
    const e = r.empresas[i];
    if (catalogo.jaCadastrada(cfg, base, e.empresa)) {
      console.log(`  ${e.empresa}: já cadastrada, pulando.`);
      continue;
    }
    for (;;) {
      const nome = await io.perguntar(`  Nome para ${e.empresa}`, catalogo.sugerirNome(e.empresa, existentes));
      if (existentes.has(nome.toLowerCase())) {
        console.log("    esse nome já existe.");
        continue;
      }
      try {
        novas.push(catalogo.conexaoParaEmpresa(base, nome, e.empresa, e.nome || e.empresa));
        existentes.add(nome.toLowerCase());
        break;
      } catch (erro) {
        console.log(`    ${erro.message}`);
      }
    }
  }
  if (!novas.length) {
    console.log("Nada novo para cadastrar.");
    return;
  }
  cfg.conexoes.push(...novas);
  salvar(cfg);
  console.log(`\n${novas.length} empresa(s) cadastrada(s): ${novas.map((c) => c.nome).join(", ")}`);
  if (await io.sim("Testar acesso a cada uma agora?", true)) {
    for (const c of novas) {
      process.stdout.write(`  ${c.nome}: `);
      await testar(c.nome);
    }
  }
}

export async function remover(nome) {
  const cfg = carregar();
  const c = obter(cfg, nome);
  if (!(await io.sim(`Remover '${c.nome}'?`))) return;
  cfg.conexoes = cfg.conexoes.filter((x) => x !== c);
  salvar(cfg);
  console.log("Removida.");
}

async function menu() {
  const pedirNome = () => io.perguntar("Nome", "", true);
  const acoes = {
    1: ["Listar bases", listar],
    2: ["Adicionar/alterar base", adicionar],
    3: ["Cadastrar empresas de um servidor já cadastrado", async () => cadastrarEmpresas(await io.perguntar("Nome de uma base já cadastrada nesse servidor", "", true))],
    4: ["Testar base", async () => testar(await pedirNome())],
    5: ["Trocar senha de uma base", async () => trocarSenha(await pedirNome())],
    6: ["Remover base", async () => remover(await pedirNome())],
  };
  for (;;) {
    console.log("\n=== hana-diag — configurador ===");
    for (const [k, [rotulo]] of Object.entries(acoes)) console.log(` ${k}. ${rotulo}`);
    console.log(" 0. Sair");
    const op = await io.perguntar(">");
    if (op === "0") return;
    if (acoes[op]) {
      try {
        await acoes[op][1]();
      } catch (e) {
        console.log(`Erro: ${e.message}`);
      }
    }
  }
}

const AJUDA = `Uso: hana-diag-config [listar | adicionar | empresas <nome> | testar <nome> | senha <nome> | remover <nome>]`;

async function main(argv) {
  const [cmd, nome] = argv;
  const precisaNome = ["empresas", "testar", "senha", "remover"];
  if (precisaNome.includes(cmd) && !nome) throw new Error(AJUDA);
  switch (cmd) {
    case undefined: return menu();
    case "listar": return listar();
    case "adicionar": return adicionar();
    case "empresas": return cadastrarEmpresas(nome);
    case "testar": process.exitCode = (await testar(nome)) ? 0 : 1; return undefined;
    case "senha": return trocarSenha(nome);
    case "remover": return remover(nome);
    case "-h": case "--help": console.log(AJUDA); return undefined;
    default: throw new Error(AJUDA);
  }
}

// Só roda quando chamado como programa (os testes importam as funções).
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/").split("/").pop())) {
  main(process.argv.slice(2)).catch((e) => {
    console.error(e.message);
    process.exitCode = 1;
  });
}
