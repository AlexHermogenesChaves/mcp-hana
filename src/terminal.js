/**
 * Entrada no terminal. Exportado como objeto `io` para os testes poderem simular
 * as respostas sem teclado.
 */
import readline from "node:readline/promises";

async function linha(rotulo) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: process.stdin.isTTY });
  try {
    return await rl.question(rotulo);
  } finally {
    rl.close();
  }
}

/** Lê a senha mostrando '*' a cada tecla (Backspace apaga, Enter termina, Ctrl+C cancela). */
function lerOculto(rotulo) {
  if (!process.stdin.isTTY) return linha(rotulo);
  return new Promise((resolve, reject) => {
    const entrada = process.stdin;
    process.stdout.write(rotulo);
    let senha = "";
    entrada.setRawMode(true);
    entrada.resume();
    entrada.setEncoding("utf8");
    const terminar = () => {
      entrada.setRawMode(false);
      entrada.pause();
      entrada.removeListener("data", aoDigitar);
      process.stdout.write("\n");
    };
    function aoDigitar(pedaco) {
      for (const ch of pedaco) {
        if (ch === "\r" || ch === "\n") {
          terminar();
          resolve(senha);
          return;
        }
        if (ch === "\u0003") {
          terminar();
          reject(new Error("Cancelado."));
          return;
        }
        if (ch === "\b" || ch === "\u007f") {
          if (senha) {
            senha = senha.slice(0, -1);
            process.stdout.write("\b \b");
          }
          continue;
        }
        if (ch < " ") continue; // setas e outras teclas de controle
        senha += ch;
        process.stdout.write("*");
      }
    }
    entrada.on("data", aoDigitar);
  });
}

export const io = {
  async perguntar(rotulo, padrao = "", obrigatorio = false) {
    for (;;) {
      const sufixo = padrao ? ` [${padrao}]` : "";
      const valor = (await linha(`${rotulo}${sufixo}: `)).trim() || padrao;
      if (valor || !obrigatorio) return valor;
      console.log("  campo obrigatório.");
    }
  },
  async sim(rotulo, padrao = false) {
    const resp = (await linha(`${rotulo} [${padrao ? "S/n" : "s/N"}]: `)).trim().toLowerCase();
    return resp ? resp.startsWith("s") : padrao;
  },
  lerOculto,
};

/** Pede a senha duas vezes, confere e informa o tamanho recebido. */
export async function pedirSenha(usuario, tentativas = 3) {
  for (let t = 0; t < tentativas; t += 1) {
    const senha = await io.lerOculto(`Senha do banco para ${usuario}: `);
    if (!senha) {
      console.log("  senha vazia, digite de novo.");
      continue;
    }
    if ((await io.lerOculto("Confirme a senha: ")) !== senha) {
      console.log("  as senhas não conferem, digite de novo.");
      continue;
    }
    console.log(`  senha recebida (${senha.length} caracteres).`);
    return senha;
  }
  throw new Error("Senha não confirmada; nada foi salvo.");
}
