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

Apply writes to a live tenant, so it is gated the same way on both systems. A
real (non-whatIf) apply runs only when all of these hold; anything else runs
apply in whatIf mode, which logs every write and makes none:

- whatIf is off (it defaults to on);
- the run is not for a pull request;
- it is building the default branch;
- it is a deployment to an environment (Azure DevOps) or a job bound to an
  environment (GitHub), so approvals and branch checks attach there.

Configure that environment before the first real run: required reviewers,
and in Azure DevOps a branch control check limited to the default branch; in
GitHub, deployment branches limited to the default branch.

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
references `writeServiceConnection` only inside the apply deployment job, so the
environment's approvals guard it. Put the same approval and branch control
checks on the write service connection itself, and do not grant other
pipelines access to it, so a pipeline that skips the template cannot borrow it.

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
| `defaultBranch` | `refs/heads/main` | The only branch a real apply runs from |
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
