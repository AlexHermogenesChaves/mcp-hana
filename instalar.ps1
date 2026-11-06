<#
.SYNOPSIS
  Instala o conector MCP hana-diag (diagnóstico somente leitura em SAP HANA).

.DESCRIPTION
  1. Confere o Python (>= 3.10).
  2. Confere o SAP HANA Client (driver ODBC HDBODBC + hdbuserstore). Se faltar, instala:
       - a partir de -InstaladorHanaClient (zip baixado ou pasta/compartilhamento já extraído), ou
       - baixando de tools.hana.ondemand.com, SOMENTE com -AceitoLicencaSAP
         (quem roda declara que leu e aceita a SAP Developer License 3.2), ou
       - sem nenhum dos dois: abre a página de download e para, explicando o que fazer.
  3. Cria o ambiente virtual (.venv) e instala o pacote.
  4. Roda os testes da guarda de somente leitura.
  5. Opcional (-RegistrarClaude): registra o servidor MCP no Claude Code do usuário.

.EXAMPLE
  .\instalar.ps1
.EXAMPLE
  .\instalar.ps1 -InstaladorHanaClient "\\servidor\instaladores\hanaclient-latest-windows-x64.zip" -RegistrarClaude
.EXAMPLE
  .\instalar.ps1 -AceitoLicencaSAP -RegistrarClaude
#>
[CmdletBinding()]
param(
    [string]$InstaladorHanaClient,
    [switch]$AceitoLicencaSAP,
    [string]$PastaHanaClient = "C:\Program Files\sap\hdbclient",
    [switch]$RegistrarClaude,
    [string]$Python = "python"
)

# "Continue" de propósito: no PowerShell 5.1, com "Stop", qualquer linha que pip ou
# unittest escrevam no stderr vira exceção. Os cmdlets críticos usam -ErrorAction Stop
# e os executáveis nativos são conferidos por $LASTEXITCODE.
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues = @{
    "Invoke-WebRequest:ErrorAction" = "Stop"; "Expand-Archive:ErrorAction" = "Stop"
    "Start-Process:ErrorAction" = "Stop"; "New-Item:ErrorAction" = "Stop"
}
$Raiz = $PSScriptRoot
$UrlPagina = "https://tools.hana.ondemand.com/#hanatools"
$UrlZip = "https://tools.hana.ondemand.com/additional/hanaclient-latest-windows-x64.zip"
$UrlLicenca = "https://tools.hana.ondemand.com/developer-license-3_2.txt"

function Passo($texto) { Write-Host "`n==> $texto" -ForegroundColor Cyan }
function Ok($texto) { Write-Host "    OK  $texto" -ForegroundColor Green }

function Test-HanaClient {
    $odbc = Get-OdbcDriver -Platform "64-bit" -ErrorAction SilentlyContinue | Where-Object Name -eq "HDBODBC"
    return [bool]$odbc -and (Test-Path (Join-Path $PastaHanaClient "hdbuserstore.exe"))
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-InstaladorBaixado {
    $destino = Join-Path $env:TEMP "hana-diag-install"
    New-Item -ItemType Directory -Force $destino | Out-Null
    $zip = Join-Path $destino "hanaclient-latest-windows-x64.zip"

    Write-Host "    Baixando $UrlZip"
    Write-Host "    (aceite declarado da SAP Developer License 3.2: $UrlLicenca)"
    $sessao = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $cookie = New-Object System.Net.Cookie("eula_3_2_agreed", "tools.hana.ondemand.com/developer-license-3_2.txt", "/", "tools.hana.ondemand.com")
    $sessao.Cookies.Add($cookie)
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -Uri $UrlZip -OutFile $zip -WebSession $sessao -UseBasicParsing
    $sha1Esperado = ((Invoke-WebRequest -Uri "$UrlZip.sha1" -WebSession $sessao -UseBasicParsing).Content.ToString().Trim() -split '\s+')[0]
    $sha1 = (Get-FileHash $zip -Algorithm SHA1).Hash
    if ($sha1 -ne $sha1Esperado.ToUpper()) {
        throw "SHA1 do download não confere (esperado $sha1Esperado, obtido $sha1). Arquivo descartado: $zip"
    }
    Ok "download conferido por SHA1"
    return $zip
}

function Install-HanaClient {
    if ($InstaladorHanaClient) {
        if (-not (Test-Path $InstaladorHanaClient)) { throw "Instalador não encontrado: $InstaladorHanaClient" }
        $origem = $InstaladorHanaClient
    } elseif ($AceitoLicencaSAP) {
        $origem = Get-InstaladorBaixado
    } else {
        Start-Process $UrlPagina
        Write-Host @"

    O SAP HANA Client não está instalado nesta máquina.
    O download exige aceitar a SAP Developer License 3.2, por isso o instalador não baixa sozinho.

    Escolha uma opção e rode de novo:
      a) Na página que abriu, baixe 'hanaclient-latest-windows-x64.zip' (aceitando a licença) e rode:
           .\instalar.ps1 -InstaladorHanaClient "<caminho do zip>"
      b) Se você leu e aceita a licença ($UrlLicenca):
           .\instalar.ps1 -AceitoLicencaSAP
"@ -ForegroundColor Yellow
        exit 2
    }

    if ((Get-Item $origem).PSIsContainer) {
        $pasta = $origem
    } else {
        $pasta = Join-Path $env:TEMP ("hana-diag-install\extraido-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
        Write-Host "    Extraindo em $pasta"
        Expand-Archive -Path $origem -DestinationPath $pasta -Force
    }
    $hdbinst = Get-ChildItem -Path $pasta -Recurse -Filter "hdbinst.exe" | Select-Object -First 1
    if (-not $hdbinst) { throw "hdbinst.exe não encontrado em $pasta. O arquivo é mesmo o instalador do SAP HANA Client para Windows?" }

    $argumentos = @("--batch", "--path=`"$PastaHanaClient`"")
    Write-Host "    Instalando SAP HANA Client em $PastaHanaClient (pede permissão de administrador)"
    if (Test-Admin) {
        $p = Start-Process -FilePath $hdbinst.FullName -ArgumentList $argumentos -Wait -PassThru -NoNewWindow
    } else {
        $p = Start-Process -FilePath $hdbinst.FullName -ArgumentList $argumentos -Wait -PassThru -Verb RunAs
    }
    if ($p.ExitCode -ne 0) { throw "hdbinst terminou com código $($p.ExitCode)." }
    if (-not (Test-HanaClient)) { throw "Instalação terminou, mas o driver HDBODBC/hdbuserstore não foi encontrado." }
    Ok "SAP HANA Client instalado"
}

# ---------------------------------------------------------------- 1. Python
Passo "Python"
$versao = $null
if (Get-Command $Python -ErrorAction SilentlyContinue) {
    $versao = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2]); sys.exit(0 if sys.version_info >= (3, 10) else 1)"
}
if (-not $versao -or $LASTEXITCODE -ne 0) {
    throw "Python 3.10+ não encontrado ('$Python'). Instale com: winget install Python.Python.3.13  — e rode de novo."
}
Ok "Python $versao"

# ---------------------------------------------------------------- 2. HANA client
Passo "SAP HANA Client (driver ODBC HDBODBC + hdbuserstore)"
if (Test-HanaClient) {
    Ok "já instalado em $PastaHanaClient"
} else {
    Install-HanaClient
}

# ---------------------------------------------------------------- 3. venv + pacote
Passo "Ambiente virtual e dependências"
$venvPy = Join-Path $Raiz ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    & $Python -m venv (Join-Path $Raiz ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar o ambiente virtual." }
}
& $venvPy -m pip install -q --disable-pip-version-check -e $Raiz
if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar o pacote." }
Ok "pacote instalado em .venv"

# O cadastro (config\conexoes.dat) nasce no primeiro "hana-diag-config adicionar",
# já cifrado com DPAPI para o usuário do Windows que rodar o configurador.

# ---------------------------------------------------------------- 4. testes
Passo "Testes da guarda de somente leitura"
Push-Location $Raiz
try {
    $saida = & $venvPy -m unittest discover -s tests 2>&1
    $codigo = $LASTEXITCODE
    $saida | ForEach-Object { if ($_ -is [Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" } } |
        Where-Object { $_.Trim() } | Select-Object -Last 2 | ForEach-Object { Write-Host "    $_" }
    if ($codigo -ne 0) { throw "Testes da guarda falharam — não use o conector até corrigir." }
} finally { Pop-Location }
Ok "guarda validada"

# ---------------------------------------------------------------- 5. Claude Code
if ($RegistrarClaude) {
    Passo "Registrando no Claude Code (escopo do usuário)"
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Host "    CLI 'claude' não encontrada; registre manualmente (README)." -ForegroundColor Yellow
    } else {
        & claude mcp remove hana-diag --scope user 2>$null | Out-Null
        & claude mcp add hana-diag --scope user -- $venvPy -m hana_diag_mcp.server
        Ok "servidor 'hana-diag' registrado"
    }
}

Write-Host "`nPronto. Próximo passo: cadastrar uma base com" -ForegroundColor Green
Write-Host "    .\.venv\Scripts\hana-diag-config.exe adicionar"
