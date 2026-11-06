@echo off
rem Configurador das bases do conector hana-diag (SELECTs somente leitura em SAP HANA / SQL Server).
node "%~dp0src\configurar.js" %*
