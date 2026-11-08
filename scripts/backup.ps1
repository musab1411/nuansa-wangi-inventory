param(
    [int]$Keep = 14
)

# Backup database ke backups\*.json dan catat ke logs\backup.log.
# Cocok dipanggil dari Task Scheduler Windows.
# Pakai:  .\scripts\backup.ps1
#         .\scripts\backup.ps1 -Keep 30

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null
$log = Join-Path $root "logs\backup.log"

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $log -Value $line
}

Write-Log "mulai backup (keep=$Keep)"
& $python scripts/backup.py --keep $Keep 2>&1 | ForEach-Object {
    Write-Host $_
    Add-Content -Path $log -Value $_
}

if ($LASTEXITCODE -ne 0) {
    Write-Log "GAGAL backup (exit code $LASTEXITCODE)"
    exit $LASTEXITCODE
}

Write-Log "backup selesai"
exit 0
