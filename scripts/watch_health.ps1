param(
    [string]$Url = "http://localhost:8000/health",
    [int]$IntervalSeconds = 30,
    [int]$MaxFailures = 3,
    [int]$DurationMinutes = 0,
    [switch]$ExitOnFailure
)

# Pantau kesehatan API secara berkala dan catat ke logs\health.log.
# Pakai:  .\scripts\watch_health.ps1
#         .\scripts\watch_health.ps1 -Url https://localhost:8443/health -IntervalSeconds 10
#         .\scripts\watch_health.ps1 -DurationMinutes 5
# Ctrl+C untuk berhenti.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

New-Item -ItemType Directory -Force -Path (Join-Path $root "logs") | Out-Null
$log = Join-Path $root "logs\health.log"

function Write-Status([string]$Message, [string]$Color = "Gray") {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line -ForegroundColor $Color
    Add-Content -Path $log -Value $line
}

$deadline = $null
if ($DurationMinutes -gt 0) {
    $deadline = (Get-Date).AddMinutes($DurationMinutes)
}

Write-Status "memantau $Url setiap $IntervalSeconds detik (max gagal berturut-turut: $MaxFailures)"
$failures = 0

while ($true) {
    try {
        $health = Invoke-RestMethod -Uri $Url -TimeoutSec 10
        Write-Status ("OK  backend={0} uptime={1}s version={2}" -f `
            $health.backend, $health.uptime_seconds, $health.version) "Green"
        $failures = 0
    }
    catch {
        $failures++
        Write-Status ("GAGAL ({0}/{1}): {2}" -f $failures, $MaxFailures, $_.Exception.Message) "Red"
        if ($failures -ge $MaxFailures) {
            Write-Status "ALERT: API tidak sehat $MaxFailures kali berturut-turut" "Yellow"
            if ($ExitOnFailure) { exit 1 }
            $failures = 0
        }
    }

    if ($deadline -and (Get-Date) -ge $deadline) {
        Write-Status "selesai (durasi tercapai)"
        break
    }
    Start-Sleep -Seconds ([Math]::Max(1, $IntervalSeconds))
}
