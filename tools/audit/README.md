# Intune audit suite

Read-only audits of an Intune tenant, grouped by domain. Nothing here writes to
Intune. It covers Windows, macOS and iOS alike, because it is scoped by object
type rather than by OS.

## Running

Sign in with the Azure CLI as an identity that can read Intune and the directory:

```
az login --tenant <your-tenant-id>
```

Then run every domain:

```
./audit-intune.sh
```

Or one or more domains, with `--json` for machine-readable output:

```
./audit-intune.sh apps compliance --json
```

The suite calls Graph through `az rest`, so it has no Python dependencies and
runs anywhere `az` and Python 3 do, including a pipeline agent signed in through
a service connection.

The identity needs these Microsoft Graph permissions, read-only:
`DeviceManagementConfiguration.Read.All`, `DeviceManagementApps.Read.All`,
`DeviceManagementManagedDevices.Read.All`, `DeviceManagementServiceConfig.Read.All`
and `GroupMember.Read.All`.

## Domains

| Domain | What it flags |
|---|---|
| `assignments` | Every assignable object: assigned to deleted groups (orphans), empty groups, a group both included and excluded, All Devices / All Users targets, unassigned objects |
| `config` | Profile conflicts reported by devices, same-setting-family overlap (`Assigned`/`Shared` variants of one profile sharing a group, which predicts a conflict), error hotspots |
| `apps` | App intent collisions: install and uninstall on one group, or required and available |
| `compliance` | Unassigned compliance policies, notify-only policies that never block or retire, current non-compliant and error counts |

## Notes

- Findings carry a severity (`high`, `medium`, `low`, `info`) and a category.
  Text output groups by domain, then category.
- `config` uses the `DeviceConfigurationPolicyStatuses` report export, which is
  intermittently slow; the client polls for up to about 12 minutes.
- Graph reports do not expose per-setting CSP error codes; only the portal's
  per-device view does.
- Device-reported status (conflicts, errors, non-compliance) lags a change by a
  device check-in cycle, up to 8 hours. The `assignments` and `config` overlap
  checks read live assignments and reflect intent immediately.
- `--fail-on-high` exits non-zero when any high finding is present, for use as a
  scheduled pipeline gate.
