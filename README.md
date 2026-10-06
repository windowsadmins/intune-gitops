# intune-gitops

The Intune half of a GitOps-managed fleet, for **both Windows and macOS**.

- **engine/** turns a reviewed manifest tree into Intune assignments. Three keys
  the client ignores (`managed_profiles`, `managed_scripts`, `managed_apps`)
  ride along in the same YAML the client reads, and become assignments against
  Entra groups. A condition becomes an assignment filter.
- **enrollment/** turns an inventory projection into the Entra group ladder
  those assignments target.
- **tools/** holds standalone tenant tools: a read-only audit, a profile
  reinstall, Autopilot unlock, an on-demand managed-run trigger and a Settings
  Catalog lookup. See [tools/README.md](tools/README.md).
- **pipelines/** holds an Azure Pipelines stages template and a GitHub
  composite action that client repos call, pinned to a tag. See
  [pipelines/README.md](pipelines/README.md).
- **policies/wdac/** holds App Control for Business samples that trust what
  your managed installer writes. See [policies/wdac/README.md](policies/wdac/README.md).

This is the shared engine behind two sample repos, which keep their own
manifests, profiles and client consumers and check this repo out at a pinned
tag:

| Repo | Platform | Client |
|---|---|---|
| [cimian-gitops](https://github.com/windowsadmins/cimian-gitops) | Windows | [Cimian](https://github.com/windowsadmins/cimian) |
| [munki-gitops](https://github.com/rodchristiansen/munki-gitops) | macOS | [Munki](https://github.com/munki/munki) |

Intune manages both, so one engine serves both. The only platform-specific code
is the condition translator, and the two sit side by side in
`engine/conditions/`.

## The address

A manifest lives at `Assigned/Staff/IT.yaml`. That path is `usage / catalog /
area`, three inventory columns. The same columns built the Entra group
`Devices-Assigned-Staff-IT` in the enrollment layer, which already exists and
already contains the right machines.

```
manifests/Assigned/Staff/IT.yaml   <->   Devices-Assigned-Staff-IT
```

A manifest path and a group name are the same address written twice, so the
engine never has to decide who anything applies to. `enrollment/shared/hierarchy.py`
is the only place that spells that address.

## Try it offline

Nothing here needs a tenant until it writes. Install PyYAML, then lint and plan
the sample trees:

```
pip install pyyaml
```

```
python3 engine/stages/lint_conditions.py --platform windows tests/fixtures/windows/manifests
```

```
python3 engine/stages/plan_assignments.py --platform macos tests/fixtures/macos/manifests
```

The plan is the artefact to read in a pull request. To see the failure modes,
lint a broken tree; it reports six findings and exits 1:

```
python3 engine/stages/lint_conditions.py --platform windows tests/fixtures/windows/broken
```

The group ladder plans offline too. With no `GRAPH_TOKEN` set it prints the
groups it would build and makes no calls:

```
cd enrollment && python3 -m consumers.intune path/to/intune.csv --what-if
```

## Stages

| Stage | Does |
|---|---|
| `lint_conditions` | Fails the build on anything the apply stage cannot honour. Runs first and gates everything. |
| `plan_assignments` | Resolves the tree into the assignments it implies. No tenant needed. |
| `apply_assignments` | Writes them. Refuses a partial plan, refuses an empty assignment set, skips anything unchanged. |

Each takes `--platform windows|macos` (or `INTUNE_PLATFORM`) and the path to a
manifest tree. What `apply_assignments` writes:

| Key | Windows | macOS |
|---|---|---|
| `managed_profiles` | `deviceConfigurations`, Settings Catalog `configurationPolicies` | the same |
| `managed_scripts` | `deviceHealthScripts` (remediations, never detect-only) | `deviceShellScripts` |
| `managed_apps` | `mobileApps`, required | `mobileApps`, required |

## Conditions become assignment filters

A client evaluates a condition on the device, against facts it collects
locally, so a condition can reference almost anything. An assignment filter can
only reference the handful of properties Intune holds. Everything hard about the
translators comes from that gap.

| Fact | Windows (Cimian) | macOS (Munki) |
|---|---|---|
| `hostname` | `==` `!=` `CONTAINS` `DOES_NOT_CONTAIN` `BEGINSWITH` | `==` `!=` `CONTAINS` `BEGINSWITH` |
| `machine_model` | `CONTAINS` / `DOES_NOT_CONTAIN`, on manufacturer or model | not supported: filter `model` is the marketing name, never the identifier |
| `machine_type` | not supported: Windows model names carry no form factor | `laptop` / `desktop`, by the `MacBook` model prefix |
| `os_version` | `== "10.0.22631"` (any UBR), `BEGINSWITH` | |
| `os_vers_major` | not supported: Windows reports `10.0.<build>` | `==` / `!=` a major version |
| `arch`, `serial_number`, `catalogs`, custom facts | not supported | not supported |

`AND`, `OR`, `NOT` and parentheses work everywhere, with three rules Intune
enforces and the translators follow (each verified against Graph's
`validateFilter`):

- **No unary `not`.** Intune rejects `(not ...)` on every property, so `NOT` is
  pushed down to the predicates (De Morgan over `and`/`or`), and each predicate
  emits its negated operator. `NOT hostname CONTAINS "KIOSK"` becomes
  `device.deviceName -notContains "KIOSK"`.
- **No `-endsWith` on deviceName.** `hostname ENDSWITH` is untranslatable.
- **No `-notStartsWith` on deviceName or model.** `NOT hostname BEGINSWITH` is
  untranslatable; `-notContains` is accepted and used.
- **No ordered comparison.** Filters have no `-ge` or `-lt` on `osVersion`, so
  `os_vers_major >= 15` or `os_version >= "10.0.22000"` are untranslatable
  rather than silently wrong.

An untranslatable condition is a lint failure, never an unfiltered assignment:
assigning without the filter would reach every device in the group instead of
the subset the condition meant.

On macOS, a laptop that Intune reports only by a bare identifier (`Mac16,1`)
has no `MacBook` prefix. List those in a YAML file and point
`INTUNE_LAPTOP_MODELS_FILE` at it:

```
models:
  - model: Mac16,1
    form_factor: laptop
```

A filtered assignment always carries the filter's id. Filters are created on
demand, named `Cimian: <condition>` or `Munki: <condition>`, reused by name, and
their rule is rewritten in place when the translation changes. A filter counts as
the pipeline's own only when its description starts with `Generated from manifest
condition:`; a hand-made filter with the same name fails the run rather than
being overwritten. Two blocks that
name the same item for the same group become one filter joined with `OR`, since
Intune takes one filter per group per assignment.

## Guards

**Assignment is a full replace.** You send the complete list of who a policy
applies to. An empty list is a successful call that unassigns the policy from
every device, and it is exactly what the code produces when a manifest walk
returns nothing. `MIN_DESIRED_ASSIGNMENTS` is the floor that catches it.

**Ownership markers.** Only objects whose description carries
`INTUNE_MANAGED_MARKER` are ever modified. Everything else in the tenant belongs
to somebody else.

**Exclusions that subtract nothing are not sent.** An exclusion is live only
when its group is a strict descendant of a group the item actually reaches.

**Membership removals are capped.** The enrollment layer refuses a desired set
below `MIN_DESIRED_MEMBERSHIPS` and skips any group where one run would remove
more than `MAX_REMOVAL_PCT` of its members.

Set every floor and cap from your own baseline and record that baseline next to
it, with a date.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `INTUNE_PLATFORM` | unset | `windows` or `macos`, when `--platform` is not given |
| `WHATIF` | unset | `true` to log every write instead of making it |
| `GRAPH_TOKEN` | unset | Required only to write |
| `MIN_DESIRED_ASSIGNMENTS` | `3` | Floor below which an assignment run is treated as a parse failure |
| `MIN_DESIRED_MEMBERSHIPS` | `10` | The same, for group membership |
| `MAX_REMOVAL_PCT` | `10` | Share of a group one run may remove |
| `INTUNE_MANAGED_MARKER` | `managed-by-gitops` | Marks the objects this pipeline owns |
| `INTUNE_GROUP_PREFIX` | `Devices` | First component of every group name |
| `PROTECTED_PROFILES` | `protected-profiles.yaml` | Profiles another pipeline owns |
| `INTUNE_LAPTOP_MODELS_FILE` | unset | macOS laptop identifiers, see above |
| `ENROLLMENT_CONSUMERS` | unset | Client consumers for the triggers, see `enrollment/README.md` |
| `WEBHOOK_SECRET` | unset | HMAC key for the generic webhook; unset refuses every request |

## Authentication

The engine reads a Graph token from `GRAPH_TOKEN` and nothing else. Locally, sign
in with the Azure CLI and hand its token over:

```
az login --tenant <your-tenant-id>
```

```
export GRAPH_TOKEN="$(az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv)"
```

In a pipeline, use a workload identity (an Azure DevOps service connection or
GitHub OIDC federated credential) and the same `az account get-access-token`
call. Neither needs a client secret. Use a read-only identity for planning and a separate
write identity reachable only from the gated apply; see
[pipelines/README.md](pipelines/README.md).

The identity needs these Microsoft Graph application permissions:

- `DeviceManagementConfiguration.ReadWrite.All`: profiles, Settings Catalog,
  scripts and assignment filters
- `DeviceManagementApps.ReadWrite.All`: app assignments
- `DeviceManagementManagedDevices.Read.All`: resolve devices by serial
- `Group.ReadWrite.All` and `GroupMember.ReadWrite.All`: build the group ladder
  (`Group.Read.All` is enough for the engine alone)

## Layout

| Path | What it is |
|---|---|
| `engine/conditions/` | `common.py` (grammar, NOT pushdown, the rules Intune enforces), `windows.py`, `macos.py` |
| `engine/lib/` | Manifest walk, assignment guards, assignment filters |
| `engine/stages/` | Lint, plan, apply |
| `enrollment/` | Group ladder, Graph client, membership guards, triggers. See its README. |
| `tools/` | Standalone tenant tools. See its README. |
| `pipelines/` | Azure Pipelines template and GitHub composite action. See its README. |
| `policies/wdac/` | App Control base and supplemental samples. See its README. |
| `tests/` | In-memory Graph, sample and broken trees per platform. No network. |

## Tests

```
python3 -m unittest discover -s tests
```

CI runs them on every pull request, along with the lint and plan stages
against both sample trees.

## License

MIT. See [LICENSE](LICENSE).
