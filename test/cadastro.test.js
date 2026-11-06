import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { CABECALHO, cifrar, decifrar } from "../src/cofre.js";
import { carregar, mesmoServidor, novaConexao, resumo, salvar } from "../src/config.js";

const SEGREDOS = ["hana-cliente.interno", "LEITOR_SELECT", "SBO_CLIENTE_PRD", "S3nh@Secreta"];

const conexao = () =>
  novaConexao({
    nome: "cliente-prd", tipo: "hana", host: "hana-cliente.interno", porta: 30015, banco: "NDB",
    usuario: "LEITOR_SELECT", senha: "S3nh@Secreta", schema: "SBO_CLIENTE_PRD", descricao: "Cliente – produção",
  });

const temporario = () => path.join(fs.mkdtempSync(path.join(os.tmpdir(), "hana-diag-")), "conexoes.dat");

test("cofre: ida e volta com acentos", () => {
  assert.equal(decifrar(cifrar(Buffer.from("acentuação e ç"))).toString("utf8"), "acentuação e ç");
});

test("cofre: arquivo adulterado não abre", () => {
  const dados = cifrar(Buffer.from("segredo"));
  dados[dados.length - 5] ^= 0xff;
  assert.throws(() => decifrar(dados), /decifrar/);
});

test("cofre: texto puro é recusado", () => {
  assert.throws(() => decifrar(Buffer.from('{"conexoes": []}')), /formato cifrado/);
});

test("cadastro: nada legível no disco, nem a senha", () => {
  const arq = temporario();
  salvar({ conexoes: [conexao()], limites: { linhasPadrao: 200, linhasMaximo: 5000 } }, arq);
  const bruto = fs.readFileSync(arq);
  assert.ok(bruto.subarray(0, CABECALHO.length).equals(CABECALHO));
  for (const s of SEGREDOS) {
    assert.equal(bruto.includes(Buffer.from(s, "utf8")), false, s);
    assert.equal(bruto.includes(Buffer.from(s, "utf16le")), false, s);
  }
  const lida = carregar(arq).conexoes[0];
  assert.equal(lida.senha, "S3nh@Secreta");
  assert.equal(fs.existsSync(`${arq}.tmp`), false);
});

test("cadastro: registro da versão Python é aceito, sem senha", () => {
  const c = novaConexao({ nome: "antiga", userkey: "DIAG_X", host: "h", porta: 30015, usuario: "u", schema: "SBO_X", validar_certificado: true, timeout_ms: 5000 });
  assert.equal(c.tipo, "hana");
  assert.equal(c.senha, "");
  assert.equal(c.validarCertificado, true);
  assert.equal(c.timeoutMs, 5000);
});

test("cadastro: SQL Server usa 1433 e banco como empresa", () => {
  const c = novaConexao({ nome: "sql", tipo: "mssql", host: "h", usuario: "u", banco: "SBO_CLIENTE" });
  assert.equal(c.porta, 1433);
  assert.equal(resumo(c).empresa, "SBO_CLIENTE");
  assert.equal(resumo(c).tipo, "SQL Server");
});

test("cadastro: resumo nunca expõe host, usuário ou senha", () => {
  const texto = JSON.stringify(resumo(conexao()));
  for (const s of ["hana-cliente.interno", "LEITOR_SELECT", "S3nh@Secreta"]) assert.equal(texto.includes(s), false, s);
});

test("cadastro: rejeita identificador com aspas/colchetes/; e tipo desconhecido", () => {
  assert.throws(() => novaConexao({ nome: "x", host: "h", usuario: "u", schema: 'A"B' }));
  assert.throws(() => novaConexao({ nome: "x", tipo: "mssql", host: "h", usuario: "u", banco: "A];DROP" }));
  assert.throws(() => novaConexao({ nome: "x", tipo: "oracle", host: "h", usuario: "u" }));
});

test("cadastro: mesmo servidor considera tipo, host, porta, instância e usuário", () => {
  const a = conexao();
  assert.ok(mesmoServidor(a, { ...a, schema: "OUTRA" }));
  assert.equal(mesmoServidor(a, { ...a, usuario: "OUTRO" }), false);
  assert.equal(mesmoServidor(a, { ...a, tipo: "mssql" }), false);
});
