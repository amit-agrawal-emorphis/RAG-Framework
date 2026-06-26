<#
.SYNOPSIS
  ONE-STOP, bullet-proof terminate for Yuktra-EQ. Run this and the whole app
  shuts down everywhere so NOTHING stays locked:

    1. stops the Windows service  YuktraEQBackend  (and waits for it)
    2. force-kills the service's OWN process by PID (it runs as LocalSystem,
       so a normal taskkill can't touch it -- this is the step that was missing
       and the reason the install folder could not be deleted)
    3. kills every related app/dev process (installed exes + streamlit children)
    4. frees ports 8008 / 8009 / 8010
    5. (optional) removes the service and deletes the install folder

  Needs admin to control a SYSTEM service -> the script SELF-ELEVATES (one UAC
  prompt) and waits for the elevated run to finish.

.PARAMETER RemoveService
  Also delete the service definition (run this before deleting/reinstalling).

.PARAMETER DeleteApp
  Also delete the install folder (implies -RemoveService). This is what the
  uninstaller calls. Without it, files are only unlocked, not removed.

.EXAMPLE
  .\stop_app.ps1                 # just stop everything (app keeps installed)
  .\stop_app.ps1 -RemoveService  # stop + remove service (folder now deletable)
  .\stop_app.ps1 -DeleteApp      # stop + remove service + delete the folder
#>
param(
    [switch]$RemoveService,
    [switch]$DeleteApp,
    [switch]$NoPause,        # don't wait for Enter at the end (for unattended/uninstaller use)
    [string]$InstallDir = (Join-Path $env:ProgramFiles 'Yuktra-EQ')
)

$ErrorActionPreference = 'Continue'
$SvcName = 'YuktraEQBackend'
if ($DeleteApp) { $RemoveService = $true }   # can't delete the folder without removing the service

# ---------------------------------------------------------------------------
# Self-elevate: controlling a LocalSystem service + deleting Program Files both
# require admin. Re-launch elevated, WAIT for it, and propagate its exit code.
# ---------------------------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Re-launching as administrator (approve the UAC prompt)..." -ForegroundColor Yellow
    $argl = @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$PSCommandPath`"",'-InstallDir',"`"$InstallDir`"")
    if ($RemoveService) { $argl += '-RemoveService' }
    if ($DeleteApp)     { $argl += '-DeleteApp' }
    if ($NoPause)       { $argl += '-NoPause' }
    $p = Start-Process powershell.exe -Verb RunAs -ArgumentList $argl -Wait -PassThru
    exit $p.ExitCode
}

Write-Host "`n==== STOPPING Yuktra-EQ ====" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 1) Capture the service's process PID BEFORE we stop it, so we can force-kill
#    it if the service hangs in STOP_PENDING (that hung process is what locks
#    the .exe and blocks folder deletion).
# ---------------------------------------------------------------------------
$svc = Get-Service -Name $SvcName -ErrorAction SilentlyContinue
$svcPid = 0
if ($svc) {
    $cim = Get-CimInstance Win32_Service -Filter "Name='$SvcName'" -ErrorAction SilentlyContinue
    if ($cim) { $svcPid = [int]$cim.ProcessId }

    if ($svc.Status -ne 'Stopped') {
        Write-Host "Stopping service $SvcName (current: $($svc.Status), pid: $svcPid) ..."
        & sc.exe stop $SvcName | Out-Null
        for ($i = 0; $i -lt 15; $i++) {
            Start-Sleep -Milliseconds 600
            if ((Get-Service $SvcName -EA SilentlyContinue).Status -eq 'Stopped') { break }
        }
    }

    # 2) If the service did not stop cleanly, force-kill its process by PID.
    $now = (Get-Service $SvcName -EA SilentlyContinue).Status
    if ($now -ne 'Stopped' -and $svcPid -gt 0) {
        Write-Host "  service still '$now' -> force-killing service process PID $svcPid" -ForegroundColor Yellow
        try { Stop-Process -Id $svcPid -Force -ErrorAction Stop } catch { & taskkill /PID $svcPid /T /F | Out-Null }
        Start-Sleep -Milliseconds 800
    }
    Write-Host "  service status: $((Get-Service $SvcName -EA SilentlyContinue).Status)"

    if ($RemoveService) {
        $nssm = Join-Path $InstallDir 'nssm.exe'
        if (Test-Path $nssm) { & $nssm remove $SvcName confirm | Out-Null } else { & sc.exe delete $SvcName | Out-Null }
        Write-Host "  service removed." -ForegroundColor Green
    }
} else {
    Write-Host "Service $SvcName not installed (skipping)."
}

# ---------------------------------------------------------------------------
# 3) Kill related processes. Installed exe names are killed by name; the broad
#    python/pythonw kill is SCOPED to our install dir or streamlit/launcher
#    command lines so we never nuke an unrelated Python on the machine.
# ---------------------------------------------------------------------------
$killed = 0
foreach ($n in 'yktra-eq-backend','backend','webview-runner','frontend','yuktra-eq','yeq') {
    Get-Process -Name $n -ErrorAction SilentlyContinue | ForEach-Object {
        try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; $killed++ } catch {}
    }
}
$norm = $InstallDir.TrimEnd('\')
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { ($_.ExecutablePath -and $_.ExecutablePath -like "$norm*") -or
                   ($_.CommandLine    -and $_.CommandLine -match 'streamlit|launcher\.py|streamlit_app\.py') } |
    ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $killed++ } catch {}
    }
Write-Host "  processes killed: $killed"

# ---------------------------------------------------------------------------
# 4) Free the ports (anything still listening on ours).
# ---------------------------------------------------------------------------
foreach ($p in 8008,8009,8010) {
    Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction Stop; Write-Host "  freed port $p (PID $($_.OwningProcess))" } catch {}
    }
}

# ---------------------------------------------------------------------------
# 5) Optionally delete the install folder, retrying until the OS releases the
#    file locks (a just-killed process can hold handles for a moment).
# ---------------------------------------------------------------------------
if ($DeleteApp) {
    if (Test-Path -LiteralPath $InstallDir) {
        Write-Host "Deleting $InstallDir ..."
        $deleted = $false
        for ($i = 0; $i -lt 10; $i++) {
            try {
                Get-ChildItem -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue |
                    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
                [System.IO.Directory]::Delete($InstallDir, $true)
                $deleted = $true; break
            } catch { Start-Sleep -Milliseconds 700 }
        }
        if ($deleted) { Write-Host "  install folder deleted." -ForegroundColor Green }
        else { Write-Host "  could NOT delete (a handle is still open). Reboot and retry." -ForegroundColor Red }
    } else {
        Write-Host "Install folder not present: $InstallDir"
    }
}

Start-Sleep -Milliseconds 400
Write-Host "`n==== Yuktra-EQ fully stopped ====" -ForegroundColor Green
if ($RemoveService -and -not $DeleteApp) {
    Write-Host "Service removed -- you can now delete the install folder safely." -ForegroundColor DarkGray
}
if (-not $NoPause -and $Host.Name -eq 'ConsoleHost' -and -not $psISE) { Write-Host "`n(press Enter to close)"; [void](Read-Host) }
