import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

// O cadastro dos testes vai para uma pasta temporária, nunca para o config/ real.
process.env.HANA_DIAG_HOME = fs.mkdtempSync(path.join(os.tmpdir(), "hana-diag-home-"));
const { placeholdersMssql } = await import("../src/banco.js");
const catalogo = await import("../src/catalogo.js");
const config = await import("../src/config.js");
const configurar = await import("../src/configurar.js");
const { io } = await import("../src/terminal.js");

const base = config.novaConexao({
  nome: "cliente-prd", tipo: "hana", host: "10.0.0.15", banco: "NDB", usuario: "LEITOR", senha: "x", schema: "SBO_CLIENTE_PRD",
});

test("sugerirNome e interpretarSelecao", () => {
  assert.equal(catalogo.sugerirNome("SBO_CLIENTE_TST", new Set()), "cliente-tst");
  assert.equal(catalogo.sugerirNome("SBO_CLIENTE_TST", new Set(["cliente-tst"])), "cliente-tst-2");
  assert.deepEqual(catalogo.interpretarSelecao("1,3", 3), [0, 2]);
  assert.deepEqual(catalogo.interpretarSelecao("3-2", 3), [1, 2]);
  assert.deepEqual(catalogo.interpretarSelecao("todas", 3), [0, 1, 2]);
  for (const ruim of ["4", "0", "a", "1-9"]) assert.throws(() => catalogo.interpretarSelecao(ruim, 3));
});

test("conexaoParaEmpresa: HANA troca o schema, SQL Server troca o banco, credencial igual", () => {
  const h = catalogo.conexaoParaEmpresa(base, "cliente-tst", "SBO_CLIENTE_TST", "Teste");
  assert.deepEqual([h.schema, h.banco, h.senha, h.usuario], ["SBO_CLIENTE_TST", "NDB", "x", "LEITOR"]);
  const sqlBase = config.novaConexao({ ...base, nome: "sql", tipo: "mssql", porta: 1433, banco: "SBO_A", schema: "" });
  const s = catalogo.conexaoParaEmpresa(sqlBase, "sql-b", "SBO_B", "B");
  assert.deepEqual([s.banco, s.senha], ["SBO_B", "x"]);
});

test("placeholders do SQL Server ignoram '?' dentro de literais", () => {
  assert.equal(placeholdersMssql("SELECT '?' FROM T WHERE A = ? AND B = ?"), "SELECT '?' FROM T WHERE A = @p0 AND B = @p1");
});

test("configurador: cadastra as empresas escolhidas sem pedir senha de novo", async () => {
  config.salvar({ conexoes: [base], limites: config.LIMITES_PADRAO });
  configurar.deps.empresas = async () => ({
    origem: "teste",
    empresas: [
      { empresa: "SBO_CLIENTE_PRD", nome: "Cliente" },
      { empresa: "SBO_CLIENTE_TST", nome: "Cliente Teste" },
      { empresa: "SBO_CLIENTE_SP", nome: "Cliente SP" },
    ],
  });
  const respostas = ["2-3", "", "cliente-filial-sp", "n"]; // seleção, nome sugerido, nome próprio, não testar
  io.perguntar = async (_r, padrao = "") => respostas.shift() || padrao;
  io.sim = async () => respostas.shift() === "s";
  io.lerOculto = async () => assert.fail("não deveria pedir senha");
  const log = console.log;
  const saida = [];
  console.log = (...a) => saida.push(a.join(" "));
  try {
    await configurar.cadastrarEmpresas("cliente-prd");
  } finally {
    console.log = log;
  }
  const cfg = config.carregar();
  assert.deepEqual(cfg.conexoes.map((c) => c.nome), ["cliente-prd", "cliente-tst", "cliente-filial-sp"]);
  assert.ok(cfg.conexoes.every((c) => c.senha === "x"));
  assert.equal(cfg.conexoes[2].schema, "SBO_CLIENTE_SP");
  assert.ok(saida.some((l) => l.includes("já cadastrada (cliente-prd)")));
});
