# install.ps1 -- Install the windows-toast client of push-relay.
#
#   1. Verifies Python 3.11+ is available.
#   2. Creates a venv at ~/.push-relay/windows-toast/venv.
#   3. pip installs the push_relay package (from repo root) + windows-toast deps.
#   4. Snapshot-copies the client script.
#   5. Registers the PushRelay-WindowsToast scheduled task (logon, hidden).
#   6. Disables any pre-existing ToastPushListener task (does not delete).
#
# Run from the repo root:  pwsh -File .\clients\windows-toast\install.ps1
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

$repo   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$client = 'windows-toast'
$dir    = Join-Path $env:USERPROFILE ".push-relay\$client"
$venv   = Join-Path $dir 'venv'
[void](New-Item -ItemType Directory -Force -Path $dir)

# Locate Python. Prefer 3.12 (winsdk has prebuilt wheels for it).
$py = $null
foreach ($candidate in @(
    "$env:LocalAppData\Programs\Python\Python312\python.exe",
    "$env:ProgramFiles\Python312\python.exe",
    "$env:LocalAppData\Programs\Python\Python311\python.exe",
    "$env:LocalAppData\Programs\Python\Python313\python.exe"
)) {
    if (Test-Path $candidate) { $py = $candidate; break }
}
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw "Python 3.11 or 3.12 not found. Install via: winget install -e --id Python.Python.3.12" }

Write-Host "Using Python: $py"

# Create venv (skip if already present + healthy).
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    Write-Host "Creating venv at $venv ..."
    & $py -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}
$venvPy = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $venvPy)) { throw "venv python not found at $venvPy" }

Write-Host "Installing dependencies..."
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -e $repo
& $venvPy -m pip install --quiet -r (Join-Path $PSScriptRoot 'requirements.txt')

# Snapshot the client script.
Copy-Item (Join-Path $PSScriptRoot 'windows_toast.py') (Join-Path $dir 'windows_toast.py') -Force

# VAPID gate.
$vapid = Join-Path $env:USERPROFILE '.push-relay\vapid.json.dpapi'
if (-not (Test-Path $vapid)) {
    Write-Warning "VAPID not configured at $vapid"
    Write-Host "  Generate with: $venvPy $repo\tools\new-vapid.py mailto:you@example.com"
    Write-Host "  Or migrate from toast-push:"
    Write-Host "    Copy-Item `"$env:USERPROFILE\.toast-push\vapid.json.dpapi`" `"$vapid`""
    Write-Host "  Then re-run this install script."
    return
}

# Subs file (empty if missing).
$subs = Join-Path $dir 'subs.json'
if (-not (Test-Path $subs)) { Set-Content -LiteralPath $subs -Value '[]' -Encoding UTF8 }

# Disable legacy ToastPushListener task (do not delete) for clean cutover.
try {
    $legacy = Get-ScheduledTask -TaskName 'ToastPushListener' -ErrorAction Stop
    if ($legacy.State -ne 'Disabled') {
        Stop-ScheduledTask -TaskName 'ToastPushListener' -ErrorAction SilentlyContinue
        Disable-ScheduledTask -TaskName 'ToastPushListener' | Out-Null
        Write-Host "Disabled legacy ToastPushListener (re-enable with Enable-ScheduledTask)."
    }
} catch {}

# Register / re-register scheduled task.
$taskName = "PushRelay-WindowsToast"
try { Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop } catch {}
try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop } catch {}

$pyw = Join-Path $venv 'Scripts\pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $venvPy }   # fallback
$argList = "`"$dir\windows_toast.py`""
$action  = New-ScheduledTaskAction -Execute $pyw -Argument $argList -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -Hidden -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 4
$log = Join-Path $dir 'listener.log'
if (Test-Path $log) { Write-Host "--- last log lines ---"; Get-Content $log -Tail 6 }

Write-Host ""
Write-Host "=== Install complete ==="
Write-Host "State dir:   $dir"
Write-Host "Venv python: $pyw"
Write-Host ""
Write-Host "Next:"
Write-Host "  1. Open the PWA on your phone (https://<user>.github.io/push-relay/)"
Write-Host "  2. Tap Enable, copy the JSON."
Write-Host "  3. On PC: $venvPy $repo\tools\add-sub.py --label '<my-phone>'"
Write-Host "     (reads JSON from clipboard)"
