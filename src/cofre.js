/**
 * Criptografia do cadastro com o DPAPI do Windows (escopo do usuário).
 *
 * A chave é derivada pelo Windows a partir do login do usuário: não existe arquivo
 * de chave nem senha mestra. Só o mesmo usuário do Windows, nesta máquina, decifra.
 *
 * Node não expõe o DPAPI sem módulo nativo; usamos a classe .NET ProtectedData por
 * meio do Windows PowerShell. Os dados vão e voltam pela entrada/saída padrão em
 * base64 — nunca pela linha de comando. Formato e entropia são os mesmos da versão
 * Python, então arquivos antigos continuam abrindo.
 */
import { spawnSync } from "node:child_process";

export const CABECALHO = Buffer.from("HDIAG-DPAPI-1\n", "ascii");
const ENTROPIA = "hana-diag-mcp/conexoes/v1";

const SCRIPT = `
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Security
$modo = [Console]::In.ReadLine()
$dados = [Convert]::FromBase64String([Console]::In.ReadLine())
$entropia = [Text.Encoding]::UTF8.GetBytes('${ENTROPIA}')
$escopo = [Security.Cryptography.DataProtectionScope]::CurrentUser
if ($modo -eq 'cifrar') {
  $saida = [Security.Cryptography.ProtectedData]::Protect($dados, $entropia, $escopo)
} else {
  $saida = [Security.Cryptography.ProtectedData]::Unprotect($dados, $entropia, $escopo)
}
[Console]::Out.Write([Convert]::ToBase64String($saida))
`;

function dpapi(modo, dados) {
  if (process.platform !== "win32") {
    throw new Error("A criptografia do cadastro usa o DPAPI e só funciona no Windows.");
  }
  const r = spawnSync(
    "powershell.exe",
    ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", SCRIPT],
    { input: `${modo}\n${dados.toString("base64")}\n`, encoding: "utf8", windowsHide: true, maxBuffer: 64 * 1024 * 1024 },
  );
  if (r.error) throw r.error;
  if (r.status !== 0) {
    const detalhe = (r.stderr || "").split("\n").find((l) => l.trim()) || `código ${r.status}`;
    throw new Error(detalhe.trim());
  }
  return Buffer.from(r.stdout.trim(), "base64");
}

export function cifrar(dados) {
  return Buffer.concat([CABECALHO, dpapi("cifrar", Buffer.from(dados))]);
}

export function decifrar(conteudo) {
  const buf = Buffer.from(conteudo);
  if (!buf.subarray(0, CABECALHO.length).equals(CABECALHO)) {
    throw new Error("Arquivo de conexões não está no formato cifrado do hana-diag.");
  }
  try {
    return dpapi("decifrar", buf.subarray(CABECALHO.length));
  } catch (e) {
    throw new Error(
      "Não foi possível decifrar o cadastro de conexões. Ele só abre com o mesmo usuário do Windows " +
        `que o criou, nesta máquina (${e.message}). Cadastre as bases de novo com hana-diag-config.`,
    );
  }
}
