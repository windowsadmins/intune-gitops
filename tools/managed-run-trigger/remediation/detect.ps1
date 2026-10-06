<#
    Detection half of the managed-run remediation. Exits 1 (needs remediation)
    unless a trigger flag is already waiting, so an on-demand run always writes
    a fresh flag and a run already queued is not queued twice.
#>
$flagDir = 'C:\ProgramData\ManagedInstalls'
$pending = @('.cimian.headless', '.cimian.bootstrap') |
    Where-Object { Test-Path (Join-Path $flagDir $_) }

if ($pending) {
    Write-Output "Run already queued: $($pending -join ', ')"
    exit 0
}
Write-Output 'No run queued.'
exit 1
