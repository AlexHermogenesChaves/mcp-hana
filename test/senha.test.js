import assert from "node:assert/strict";
import { test } from "node:test";
import { io, pedirSenha } from "../src/terminal.js";

async function comSaida(fn) {
  const log = console.log;
  const linhas = [];
  console.log = (...a) => linhas.push(a.join(" "));
  try {
    return { valor: await fn(), texto: linhas.join("\n") };
  } finally {
    console.log = log;
  }
}

test("pede de novo quando diverge ou vem vazia, e informa o tamanho", async () => {
  const respostas = ["abc", "abd", "", "s3nha!", "s3nha!"];
  io.lerOculto = async () => respostas.shift();
  const { valor, texto } = await comSaida(() => pedirSenha("LEITOR"));
  assert.equal(valor, "s3nha!");
  assert.match(texto, /não conferem/);
  assert.match(texto, /senha vazia/);
  assert.match(texto, /senha recebida \(6 caracteres\)/);
});

test("desiste depois de três tentativas", async () => {
  const respostas = ["a", "b", "a", "b", "a", "b"];
  io.lerOculto = async () => respostas.shift();
  await assert.rejects(comSaida(() => pedirSenha("LEITOR")), /não confirmada/);
});
