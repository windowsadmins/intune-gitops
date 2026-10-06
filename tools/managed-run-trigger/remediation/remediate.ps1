<#
    Remediation half: writes the flag CimianWatcher polls for. The watcher runs
    as SYSTEM, checks every 10 seconds, starts managedsoftwareupdate, and deletes
    the flag as its acknowledgement.

    Upload one remediation per run type: .cimian.headless (no UI, for anything
    unattended) or .cimian.bootstrap (shows the status window).
#>
$flag = '.cimian.headless'
$flagDir = 'C:\ProgramData\ManagedInstalls'

New-Item -ItemType Directory -Path $flagDir -Force | Out-Null
Set-Content -Path (Join-Path $flagDir $flag) -Value (Get-Date -Format o) -Encoding ascii
Write-Output "Wrote $flag"
exit 0
