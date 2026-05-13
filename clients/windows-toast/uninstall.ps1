[CmdletBinding()] param(
    [switch]$KeepState,
    [switch]$RestoreLegacy
)
$ErrorActionPreference = 'SilentlyContinue'
$task = 'PushRelay-WindowsToast'
$dir  = Join-Path $env:USERPROFILE '.push-relay\windows-toast'

Write-Host "=== Uninstall $task ==="
Stop-ScheduledTask -TaskName $task
Unregister-ScheduledTask -TaskName $task -Confirm:$false

Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*windows_toast.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

if ($RestoreLegacy) {
    try {
        Enable-ScheduledTask -TaskName 'ToastPushListener' | Out-Null
        Start-ScheduledTask -TaskName 'ToastPushListener'
        Write-Host "Re-enabled legacy ToastPushListener."
    } catch { Write-Host "Could not re-enable ToastPushListener: $_" }
}

if (-not $KeepState -and (Test-Path $dir)) {
    Remove-Item -Recurse -Force $dir
    Write-Host "Removed $dir"
}
Write-Host "Note: ~/.push-relay/vapid.json.dpapi (shared VAPID) preserved."
