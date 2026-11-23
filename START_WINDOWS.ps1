# Avvia SalesForB. La prima volta chiede email e password del gestore della piattaforma.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
py -3 manage.py init --se-manca
if ($LASTEXITCODE -ne 0) { throw "Preparazione non riuscita." }
py -3 -m server
