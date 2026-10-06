<#
    Triggers a managed software run - Cimian on Windows, Munki on macOS - on a
    chosen set of devices, by asking Intune to create the agent's trigger flag
    file.

    The flag contracts, both verified against live devices:

      Windows  C:\ProgramData\ManagedInstalls\.cimian.headless
               CimianWatcher runs as SYSTEM, polls every 10 seconds, tests
               existence and LastWriteTime only, deletes the flag as its
               acknowledgement, and serialises runs so a flag arriving mid-run
               defers instead of starting a second. Measured: flag written
               23:46:06, detected 23:46:09, managedsoftwareupdate started
               23:46:10.

      macOS    /Users/Shared/.com.googlecode.munki.checkandinstallatstartup
               Same idea, but see the macOS note below: there is no on-demand
               Intune action to write it.

    Targets are resolved from an inventory that carries deviceName,
    serialNumber, operatingSystem, area, location and catalog for the fleet:
    either a ReportMate API (one call for the whole fleet) or a CSV export with
    those columns. That avoids maintaining a second inventory here, and means
    "everything in room B101" or "everything in the Kiosk catalog" are
    first-class targets.

    The remediation it fires must already exist in Intune and be assigned to the
    devices; remediation/ holds a detection and remediation pair that writes
    the flag.

    DryRun defaults to $true on purpose: a mistyped area should print what it
    would have hit, not fan out across the fleet.
#>

[CmdletBinding()]
param(
    [ValidateSet('windows', 'macos', 'both')]
    [string] $Platform = 'windows',

    [ValidateSet('device', 'area', 'location', 'catalog', 'all')]
    [string] $TargetBy = 'device',

    # Comma-separated. Ignored when TargetBy is 'all'. Matched case-insensitively
    # and exactly - substring matching would make "Animation" also hit a Kiosk
    # device whose area happens to contain it.
    [string] $Target = '',

    # Windows only. headless runs with no UI; bootstrap shows the GUI, which is
    # wrong for anything unattended. Which flag gets written is decided by the
    # remediation passed in -RemediationScriptId, so pass the one that matches;
    # this value labels the run, it is not sent to the device.
    [ValidateSet('headless', 'bootstrap')]
    [string] $RunType = 'headless',

    # A validated string rather than [bool]: an ADO boolean parameter expands to
    # True/False, which does not bind to a [bool] parameter without shell-specific
    # quoting gymnastics that differ between bash and pwsh.
    [ValidateSet('true', 'false')]
    [string] $DryRun = 'true',

    # The Intune remediation (deviceHealthScript) whose body writes the flag.
    # Required for real runs on Windows: initiateOnDemandProactiveRemediation
    # fires a remediation that already exists and is assigned to the device - it
    # does not push a script body.
    [string] $RemediationScriptId,

    # Inventory: a CSV with deviceName, serialNumber, operatingSystem, area,
    # location and catalog columns, or a ReportMate API base URL.
    [string] $InventoryCsv,
    [string] $ReportMateApi = $env:REPORTMATE_API,
    [string] $ReportMatePassphrase = $env:REPORTMATE_PASSPHRASE,

    # Graph auth: an app registration, or fall back to the signed-in az session.
    [string] $TenantId, [string] $ClientId, [string] $ClientSecret
)

$ErrorActionPreference = 'Stop'

function Get-GraphToken {
    if ($TenantId -and $ClientId -and $ClientSecret) {
        $r = Invoke-RestMethod -Method Post -Uri "https://login.microsoftonline.com/$TenantId/oauth2/v2.0/token" -Body @{
            client_id = $ClientId; client_secret = $ClientSecret
            scope = 'https://graph.microsoft.com/.default'; grant_type = 'client_credentials'
        }
        return $r.access_token
    }
    $t = az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv
    if ($LASTEXITCODE -ne 0 -or -not $t) { throw 'No Graph credentials: pass -TenantId/-ClientId/-ClientSecret, or sign in with az.' }
    return $t.Trim()
}

# ---------------------------------------------------------------- resolve targets

if ($TargetBy -ne 'all' -and -not $Target) {
    throw "TargetBy is '$TargetBy' but no -Target was given."
}

if ($InventoryCsv) {
    Write-Host "Resolving targets from $InventoryCsv..."
    $fleet = Import-Csv -Path $InventoryCsv
}
elseif ($ReportMateApi) {
    if (-not $ReportMatePassphrase) {
        throw 'ReportMate passphrase not supplied. Pass -ReportMatePassphrase or set REPORTMATE_PASSPHRASE.'
    }
    Write-Host "Resolving targets from ReportMate..."
    $fleet = Invoke-RestMethod -Method Get -Uri "$($ReportMateApi.TrimEnd('/'))/api/v1/network" `
                -Headers @{ 'X-Client-Passphrase' = $ReportMatePassphrase; 'User-Agent' = 'trigger-managed-run' }
}
else {
    throw 'No inventory: pass -InventoryCsv, or -ReportMateApi (or set REPORTMATE_API).'
}

$wanted = @($Target -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })

$matched = switch ($TargetBy) {
    'all'      { $fleet }
    'device'   { $fleet | Where-Object { $wanted -contains $_.deviceName -or $wanted -contains $_.serialNumber } }
    'area'     { $fleet | Where-Object { $wanted -contains $_.area } }
    'location' { $fleet | Where-Object { $wanted -contains $_.location } }
    'catalog'  { $fleet | Where-Object { $wanted -contains $_.catalog } }
}

# Platform comes from the reported OS rather than a separate field, so a device
# re-imaged to the other platform cannot be targeted wrongly. Classify explicitly:
# an absent or unrecognised operatingSystem is 'unknown', NOT macOS. A
# '-notmatch Windows' test sweeps devices reporting a blank OS into the macOS
# set, which makes them invisible targets.
function Get-DevicePlatform {
    param($Device)
    $os = "$($Device.operatingSystem)".Trim()
    if (-not $os)               { return 'unknown' }
    if ($os -match 'Windows')   { return 'windows' }
    if ($os -match 'macOS|Mac OS|OS X|Darwin') { return 'macos' }
    return 'unknown'
}

$matched = $matched | ForEach-Object {
    $_ | Add-Member -NotePropertyName _platform -NotePropertyValue (Get-DevicePlatform $_) -Force -PassThru
}

$unknown = @($matched | Where-Object { $_._platform -eq 'unknown' })
if ($unknown.Count -gt 0) {
    Write-Warning ("{0} device(s) report no recognisable operating system and are excluded rather than guessed at:`n{1}" -f
        $unknown.Count, (($unknown | ForEach-Object { "    $($_.deviceName) ($($_.serialNumber))" }) -join "`n"))
}

# $device is bound deliberately rather than using $_ inside the switch: a switch
# statement rebinds $_ to the value being switched on, so $_._platform inside a
# switch nested in a Where-Object silently reads the platform string, not the
# device, and every comparison yields null.
$matched = $matched | Where-Object {
    $device = $_
    if ($Platform -eq 'both') { $device._platform -ne 'unknown' }
    else                      { $device._platform -eq $Platform }
}

if (-not $matched) { Write-Host "No devices matched $TargetBy = '$Target' for platform '$Platform'."; return }

Write-Host ("Matched {0} device(s):" -f @($matched).Count)
$matched | Sort-Object deviceName | ForEach-Object {
    "  {0,-26} {1,-10} {2,-14} {3,-12} {4}" -f $_.deviceName, $_.serialNumber, $_.catalog, $_.area, $_.operatingSystem
}

# ---------------------------------------------------------------- macOS gap

$macs = @($matched | Where-Object { $_._platform -eq 'macos' })
if ($macs.Count -gt 0) {
    Write-Host ''
    Write-Warning @"
$($macs.Count) macOS device(s) matched, and they are NOT triggered by this script.
Intune shell scripts run on their own schedule; there is no on-demand run action
equivalent to initiateOnDemandProactiveRemediation for macOS. Until a transport is
agreed (a LaunchDaemon watching a config, an MDM custom command, or Munki's own
manifest), Macs must be excluded rather than silently skipped.
"@
}

$targets = @($matched | Where-Object { $_._platform -eq 'windows' })
if (-not $targets) { return }

if ($DryRun -eq 'true') {
    Write-Host ''
    Write-Host "DRY RUN - would trigger a '$RunType' run on $($targets.Count) Windows device(s). Re-run with -DryRun false to fire."
    return
}
if (-not $RemediationScriptId) {
    throw 'RemediationScriptId is required for a real run: the on-demand call fires an existing remediation, it does not push a script.'
}

# ---------------------------------------------------------------- fire

$headers = @{ Authorization = "Bearer $(Get-GraphToken)"; 'Content-Type' = 'application/json' }
$ok = 0; $failed = 0

foreach ($d in $targets) {
    try {
        $q = "https://graph.microsoft.com/beta/deviceManagement/managedDevices?`$filter=serialNumber eq '$($d.serialNumber)'"
        $md = (Invoke-RestMethod -Method Get -Uri $q -Headers $headers).value | Select-Object -First 1
        if (-not $md) { Write-Warning "$($d.deviceName): not found in Intune by serial $($d.serialNumber)"; $failed++; continue }

        Invoke-RestMethod -Method Post -Headers $headers `
            -Uri "https://graph.microsoft.com/beta/deviceManagement/managedDevices/$($md.id)/initiateOnDemandProactiveRemediation" `
            -Body (@{ scriptPolicyId = $RemediationScriptId } | ConvertTo-Json) | Out-Null

        Write-Host "  triggered: $($d.deviceName)"
        $ok++
    }
    catch {
        Write-Warning "  $($d.deviceName): $($_.Exception.Message)"
        $failed++
    }
}

Write-Host ''
Write-Host "Triggered $ok, failed $failed."
if ($failed -gt 0) { exit 1 }
