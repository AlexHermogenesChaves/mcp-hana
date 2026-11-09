@echo off
rem Configurador das bases do conector hana-diag (SELECTs somente leitura em SAP HANA / SQL Server).
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Conector nao instalado. Rode: powershell -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" -RegistrarClaude
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m hana_diag_mcp.configurar %*
