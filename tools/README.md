# Tenant tools

Small, standalone tools for an Intune tenant. Each authenticates as whoever is
signed in to the Azure CLI, so there is no secret to store:

```
az login --tenant <your-tenant-id>
```

In a pipeline, sign in through a workload identity (an Azure DevOps service
connection or GitHub OIDC) instead. Each tool lists the Graph permissions it
needs.

| Tool | Platform | What it does | Writes? |
|---|---|---|---|
| [`audit/`](audit/) | all | Read-only tenant audit: orphaned and empty-group assignments, conflicts, app intent collisions, compliance gaps | no |
| [`reinstall-profile/`](reinstall-profile/) | Windows, macOS | Forces a clean reinstall of one configuration profile on chosen devices, through a temporary exclusion group | yes, dry run by default |
| [`autopilot-unlock/`](autopilot-unlock/) | Windows | Deletes Autopilot registrations by serial, releasing the MDM lock so a device can be reused or returned | yes, dry run by default |
| [`managed-run-trigger/`](managed-run-trigger/) | Windows (macOS listed, not triggered) | Starts a Cimian run on chosen devices by firing a remediation that writes the client's trigger flag | yes, dry run by default |
| [`settings-catalog-lookup/`](settings-catalog-lookup/) | all | Finds Settings Catalog definition IDs by name | no |

## reinstall-profile

A configuration profile can report success while its payload never installed
properly; VPN profiles are the usual victim. MDM profiles are locked on the
device, so the fix has to come from the server. This tool adds the devices to a
temporary group, excludes that group from the profile so MDM removes it, waits,
drops the exclusion so MDM reinstalls it clean, waits again, then deletes the
group. The profile's original assignments are captured first and restored in a
`finally` block, so an interrupted run never leaves a device without the profile.

Print the plan first:

```
python3 reinstall-profile/reinstall_profile.py --profile CorpVPN --serials SERIAL001
```

Then run it:

```
python3 reinstall-profile/reinstall_profile.py --profile CorpVPN --serials SERIAL001,SERIAL002 --apply
```

## autopilot-unlock

Deletes each device's Windows Autopilot registration, which releases the MDM
lock. Takes serials, or a CSV with a `serial` (or `Serial#`, `SerialNumber`)
column and an optional `equipment_id` column, and writes a Markdown report.
Needs `DeviceManagementServiceConfig.ReadWrite.All`.

It is a dry run unless `--apply` is passed: it looks every device up and lists
the registrations it would remove. Check that list, then delete:

```
python3 autopilot-unlock/unlock_autopilot_devices.py --serials SERIAL001 SERIAL002
```

```
python3 autopilot-unlock/unlock_autopilot_devices.py --serials SERIAL001 SERIAL002 --apply
```

## managed-run-trigger

Starts a Cimian run now rather than at the next scheduled check-in. Intune's
`initiateOnDemandProactiveRemediation` runs an existing, assigned remediation on
a device; the one in `remediation/` writes
`C:\ProgramData\ManagedInstalls\.cimian.headless`, which CimianWatcher picks up
within ten seconds. Upload that pair as a remediation, assign it to the devices
you want to be able to trigger, and pass its id as `-RemediationScriptId`.

Targets come from an inventory CSV (columns `deviceName`, `serialNumber`,
`operatingSystem`, `area`, `location`, `catalog`) or a ReportMate API. It is a
dry run unless `-DryRun false` is passed:

```
pwsh managed-run-trigger/trigger-managed-run.ps1 -InventoryCsv fleet.csv -TargetBy location -Target B101
```

macOS devices that match are listed and left alone: Intune has no on-demand
action for shell scripts, so there is no transport to write Munki's
`/Users/Shared/.com.googlecode.munki.checkandinstallatstartup` flag on demand.

Needs `DeviceManagementManagedDevices.PrivilegedOperations.All` and
`DeviceManagementManagedDevices.Read.All`.

## settings-catalog-lookup

```
pwsh settings-catalog-lookup/Get-SettingsCatalogDefinition.ps1 -SearchTerm "defender"
```

Prints each matching setting's display name, definition ID and description.
Pass `-TenantId` to sign in to a specific tenant first.
