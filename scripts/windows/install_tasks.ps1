<#
.SYNOPSIS
    Registers two Windows scheduled tasks that keep Pilotage running:
    "Pilotage Web" (waitress, Tailscale address only) and "Pilotage Bot" (Discord bot + agent).

.DESCRIPTION
    Both tasks start at logon of the current user, run hidden, and restart every minute
    if they stop (up to 999 times). They run as the current user on purpose: Claude Code
    is logged in with this user's subscription.
    Run once from the repository root, in a normal (non-admin) PowerShell:
        powershell -ExecutionPolicy Bypass -File scripts\windows\install_tasks.ps1 -ListenAddress 100.x.y.z:8000

.PARAMETER ListenAddress
    Tailscale IP and port for the web interface (see `tailscale ip -4`).

.PARAMETER Remove
    Unregister both tasks instead.
#>
param(
    [string]$ListenAddress = "127.0.0.1:8000",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $Repo ".venv\Scripts\python.exe"
$Waitress = Join-Path $Repo ".venv\Scripts\waitress-serve.exe"
$Tasks = @("Pilotage Web", "Pilotage Bot")

if ($Remove) {
    foreach ($name in $Tasks) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "Tâche supprimée : $name"
        }
    }
    return
}

if (-not (Test-Path $Python)) { throw "Environnement virtuel introuvable : $Python" }
if ($env:ANTHROPIC_API_KEY) {
    Write-Warning "ANTHROPIC_API_KEY est définie : avec AGENT_AUTH=subscription le bot la retire, mais vérifiez qu'elle n'est pas voulue."
}

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$web = New-ScheduledTaskAction -Execute $Waitress `
    -Argument "--listen=$ListenAddress config.wsgi:application" -WorkingDirectory $Repo
$bot = New-ScheduledTaskAction -Execute $Python `
    -Argument "manage.py run_bot" -WorkingDirectory $Repo

Register-ScheduledTask -TaskName "Pilotage Web" -Action $web -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Register-ScheduledTask -TaskName "Pilotage Bot" -Action $bot -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

foreach ($name in $Tasks) { Start-ScheduledTask -TaskName $name }
Write-Host "Tâches installées et démarrées : $($Tasks -join ', ')"
Write-Host "Interface web : http://$ListenAddress/  ·  Journaux : $Repo\logs\pilotage.log"
