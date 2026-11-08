param(
    [int]$Port = 8443,
    [string]$BindAddress = "0.0.0.0"
)

# Jalankan API dengan HTTPS (self-signed) - Nuansa Wangi.
# Pakai:  .\scripts\run_https.ps1
#         .\scripts\run_https.ps1 -Port 9443
#         .\scripts\run_https.ps1 -BindAddress 127.0.0.1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$cert = Join-Path $root "certs\server.crt"
$key = Join-Path $root "certs\server.key"

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

if (-not (Test-Path $cert) -or -not (Test-Path $key)) {
    Write-Host "Sertifikat belum ada, membuat self-signed cert..." -ForegroundColor Yellow
    & $python scripts/make_cert.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host ""
Write-Host "Dashboard : https://localhost:$Port/dashboard" -ForegroundColor Green
Write-Host "Health    : https://localhost:$Port/health" -ForegroundColor Green
Write-Host "Metrics   : https://localhost:$Port/metrics" -ForegroundColor Green
Write-Host "Sertifikat self-signed: browser akan menampilkan peringatan." -ForegroundColor DarkYellow
Write-Host "Klik 'Advanced' -> 'Proceed' untuk demo lokal." -ForegroundColor DarkYellow
Write-Host ""

& $python -m uvicorn app.main:app --app-dir server --host $BindAddress --port $Port `
    --ssl-certfile $cert --ssl-keyfile $key

exit $LASTEXITCODE
