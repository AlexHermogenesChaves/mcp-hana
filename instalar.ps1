<#
.SYNOPSIS
  Instala o conector MCP hana-diag: SELECTs somente leitura nas bases SAP B1 dos
  clientes (SAP HANA e SQL Server), sem precisar acessar o ambiente do cliente.

.DESCRIPTION
  1. Confere o Node.js (>= 20).
  2. Instala as dependências (npm ci). Os drivers vêm pelo npm: @sap/hana-client já
     traz a biblioteca do HANA, e mssql é JavaScript puro — não precisa instalar o
     SAP HANA Client nem drivers de SQL Server.
  3. Roda os testes. Inclui a conferência da lista de palavras proibidas: se ela foi
     alterada, a instalação é recusada.
  4. Opcional (-RegistrarClaude): registra o servidor MCP no Claude Code do usuário.

.EXAMPLE
  .\instalar.ps1 -RegistrarClaude
#>
[CmdletBinding()]
param(
    [switch]$RegistrarClaude
)

# "Continue" de propósito: no PowerShell 5.1, com "Stop", qualquer linha que npm ou
# node escrevam no stderr vira exceção. Executáveis são conferidos por $LASTEXITCODE.
$ErrorActionPreference = "Continue"
$Raiz = $PSScriptRoot

function Passo($texto) { Write-Host "`n==> $texto" -ForegroundColor Cyan }
function Ok($texto) { Write-Host "    OK  $texto" -ForegroundColor Green }
function Falha($texto) { Write-Host "    ERRO  $texto" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- 1. Node.js
Passo "Node.js"
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Falha "Node.js não encontrado. Instale a versão LTS com: winget install OpenJS.NodeJS.LTS  — e rode de novo."
}
$versao = (& node -v).TrimStart("v")
if ([int]($versao.Split(".")[0]) -lt 20) { Falha "Node.js $versao é antigo; é preciso 20 ou superior." }
Ok "Node.js $versao"

# ---------------------------------------------------------------- 2. dependências
Passo "Dependências (npm)"
Push-Location $Raiz
try {
    $saida = & npm ci --no-fund --no-audit 2>&1
    if ($LASTEXITCODE -ne 0) {
        $saida | Select-Object -Last 10 | ForEach-Object { Write-Host "    $_" }
        Falha "npm ci falhou."
    }
} finally { Pop-Location }
Ok "dependências instaladas (drivers SAP HANA e SQL Server inclusos)"

# ---------------------------------------------------------------- 3. testes
Passo "Testes (guarda de leitura, lista de palavras proibidas, cadastro cifrado)"
Push-Location $Raiz
try {
    $saida = & node --test 2>&1
    $codigo = $LASTEXITCODE
} finally { Pop-Location }
$resumo = $saida | ForEach-Object { "$_" } | Where-Object { $_ -match '^# (tests|pass|fail)' }
$resumo | ForEach-Object { Write-Host "    $_" }
if ($codigo -ne 0) {
    $saida | ForEach-Object { "$_" } | Where-Object { $_ -match 'not ok' } | Select-Object -First 5 | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    Falha "Testes falharam — se a lista de palavras proibidas foi alterada, a instalação é recusada. Não use o conector até corrigir."
}
Ok "testes ok"

# ---------------------------------------------------------------- 4. Claude Code
$servidor = Join-Path $Raiz "src\server.js"
if ($RegistrarClaude) {
    Passo "Registrando no Claude Code (escopo do usuário)"
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Host "    CLI 'claude' não encontrada; registre manualmente (README)." -ForegroundColor Yellow
    } else {
        & claude mcp remove hana-diag --scope user 2>&1 | Out-Null
        & claude mcp add hana-diag --scope user -- node $servidor 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Falha "claude mcp add falhou." }
        Ok "servidor 'hana-diag' registrado (node $servidor)"
    }
}

Write-Host "`nPronto. Cadastre uma base com:" -ForegroundColor Green
Write-Host "    .\hana-diag-config.cmd adicionar"
