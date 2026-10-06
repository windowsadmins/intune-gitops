# Pipelines

Reusable pipeline pieces for a client repo (cimian-gitops, munki-gitops, or
your own) that keeps its manifest tree and wants this engine to lint, plan and
apply it. The client repo pins this repo to a release tag, so an engine change
reaches its pipeline only when it moves the pin.

Both run the same four stages: **lint** gates everything, **test** runs this
repo's offline suite plus any platform checks the caller adds, **plan** prints
the assignments, and **apply** writes them, in whatIf mode unless told
otherwise.

## The apply gate

Apply writes to a live tenant. The YAML has conditions that keep a real
(non-whatIf) apply off pull requests and off any branch but `main`, and they
run apply in whatIf mode otherwise. **Those conditions are a convenience, not
the gate.** Whoever pushes a branch controls the calling pipeline's YAML and can
pass `whatIf: false` or call the stages differently. The boundary has to be on
resources the branch author cannot edit.

### Azure Pipelines: required configuration

Put **all three** of these checks on **both** the write service connection and
the environment the apply deploys to (Project settings, then Service
connections or Environments, then Approvals and checks). Do this before the
first real run; without them the write identity is open to any branch.

1. **Approvals.** At least one approver who is not the change author.
2. **Branch control.** Allowed branches `refs/heads/main`, with
   *Verify branch protection* turned on, so the check fails for a branch
   without the main branch's policies.
3. **Required template.** Require that runs extend or include
   `pipelines/azure/intune-mgmt.yml` from the `windowsadmins/intune-gitops`
   repository at the release tag you pin (for example `refs/tags/v0.1.1`). A
   pipeline that skips the template, or points it at another ref, then cannot
   use the connection.

Also restrict the write service connection's pipeline permissions to the one
pipeline that runs the template, rather than "grant access to all pipelines".

The template hardcodes `refs/heads/main` as the only branch a real apply runs
from; a repository whose default branch has another name should fork the
template rather than loosen it.

### GitHub Actions: required configuration

The boundary is the environment's protection rules and the federated
credential, not the `if:` in the workflow:

1. On the `intune-production` environment, set **required reviewers** and
   **deployment branches** limited to `main`.
2. Give the write app registration exactly one federated credential, with
   subject `repo:<owner>/<repo>:environment:intune-production`. A job can only
   present that subject from inside the environment, so only a reviewed run on
   `main` can sign in as the writer.

The composite action also forces whatIf on a pull request or any ref other than
the default branch, which is again a convenience on top of those rules.

## Two identities

An environment gate protects nothing if the credential it guards is reachable
from outside it. So the pieces use two identities, each an app registration or
managed identity with workload identity federation and no secret:

| Identity | Graph permissions | Used by |
|---|---|---|
| Read-only | `DeviceManagementConfiguration.Read.All`, `DeviceManagementApps.Read.All`, `DeviceManagementManagedDevices.Read.All`, `Group.Read.All` | the whatIf apply, on pull requests and every non-default branch |
| Write | the `ReadWrite` permissions in the top-level README | the real apply only |

Lint, test and plan need no identity at all.

**Azure Pipelines.** Make one service connection per identity. The template
references `writeServiceConnection` only inside the apply deployment job; the
checks under "Azure Pipelines: required configuration" on that connection are
what stop any other pipeline or branch from using it.

**GitHub Actions.** The example grants no `id-token: write` at workflow level.
Only the two apply jobs get it, and each signs in as a different app
registration, chosen by the federated credential's subject:

| Federated credential subject | App registration |
|---|---|
| `repo:<owner>/<repo>:environment:intune-production` | write |
| `repo:<owner>/<repo>:environment:intune-plan` | read-only |
| `repo:<owner>/<repo>:pull_request`, `repo:<owner>/<repo>:ref:refs/heads/<branch>` | read-only, if you add them at all |

Never give the write app registration a `pull_request` or branch subject: either
would let a job outside the environment mint a write token. Store the client
IDs as repository variables (`AZURE_READ_CLIENT_ID`, `AZURE_WRITE_CLIENT_ID`,
`AZURE_TENANT_ID`); they are identifiers, not secrets.

Third-party actions in the example and in this repo's CI are pinned to commit
SHAs, with the version in a comment. Pin this repo's action to a release tag, or
to that tag's commit SHA if your policy requires SHAs for every action.

## Azure Pipelines

`azure/intune-mgmt.yml` is a stages template. Declare this repo as a GitHub
repository resource pinned to a tag (it needs a GitHub service connection, even
for a public repo), then call the template. `azure/example-caller.yml` is a
complete pipeline to copy:

```
resources:
  repositories:
    - repository: intune_gitops
      type: github
      endpoint: github-public
      name: windowsadmins/intune-gitops
      ref: refs/tags/v0.1.1

stages:
  - template: pipelines/azure/intune-mgmt.yml@intune_gitops
    parameters:
      platform: windows
      manifestsPath: intune/manifests
      readServiceConnection: intune-graph-read
      writeServiceConnection: intune-graph-write
      environment: intune-production
```

The template checks out the calling repo at `$(Pipeline.Workspace)/s/self` and
this one at `$(Pipeline.Workspace)/s/intune-gitops`. Paths in parameters are
relative to the calling repo; steps passed in `validationSteps` should set
`workingDirectory: $(Pipeline.Workspace)/s/self`.

| Parameter | Default | Meaning |
|---|---|---|
| `platform` | required | `windows` or `macos` |
| `manifestsPath` | required | The manifest tree, relative to the calling repo |
| `readServiceConnection` | required | Read-only connection for the whatIf apply (see "Two identities") |
| `writeServiceConnection` | required | Write connection, referenced only inside the gated apply deployment |
| `environment` | required | Environment the real apply deploys to; put approvals and checks on it |
| `engineRepository` | `intune_gitops` | The alias the caller gave this repo |
| `whatIf` | `true` | Log every write instead of making it |
| `validationSteps` | none | Platform checks for the test stage, such as catalog waterfalls or PayloadVersion |
| `minDesiredAssignments` | `3` | The assignment floor; set it from your baseline |
| `protectedProfiles` | none | Profiles another pipeline owns |
| `laptopModelsFile` | none | macOS laptops Intune reports by identifier |

## GitHub Actions

`github/action.yml` is a composite action. The engine comes from the action's
own checkout, so pinning the action to a tag pins the engine and there is no
second checkout:

```
- uses: windowsadmins/intune-gitops/pipelines/github@v0.1.1
  with:
    stage: plan
    platform: macos
    manifests: intune/manifests
```

`github/example-caller.yml` is a complete workflow to copy, including Graph
sign-in through OIDC with `azure/login` and no client secret, scoped as
described under "Two identities". A composite action
cannot hold an environment, so the gate lives in the caller: the example splits
apply into a whatIf job and a real job bound to the `intune-production`
environment, and the action itself forces whatIf on a pull request or any ref
other than the default branch.

| Input | Default | Meaning |
|---|---|---|
| `stage` | required | `lint`, `plan`, `apply` or `test` |
| `platform` | required | `windows` or `macos` |
| `manifests` | `manifests` | The manifest tree in the caller's workspace |
| `what-if` | `true` | For `apply`, log every write instead of making it. Forced on for a pull request or a non-default ref |
| `graph-token` | none | For `apply`, a Graph access token |
| `min-desired-assignments` | `3` | The assignment floor |
| `protected-profiles` | none | Profiles another pipeline owns |
| `laptop-models-file` | none | macOS laptops Intune reports by identifier |
