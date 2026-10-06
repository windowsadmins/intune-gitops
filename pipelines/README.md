# Pipelines

Reusable pipeline pieces for a client repo (cimian-gitops, munki-gitops, or
your own) that keeps its manifest tree and wants this engine to lint, plan and
apply it. The client repo pins this repo to a release tag, so an engine change
reaches its pipeline only when it moves the pin.

Both run the same four stages: **lint** gates everything, **test** runs this
repo's offline suite plus any platform checks the caller adds, **plan** prints
the assignments, and **apply** writes them, in whatIf mode unless told
otherwise.

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
      ref: refs/tags/v0.1.0

stages:
  - template: pipelines/azure/intune-mgmt.yml@intune_gitops
    parameters:
      platform: windows
      manifestsPath: intune/manifests
      serviceConnection: intune-graph
```

The template checks out the calling repo at `$(Pipeline.Workspace)/s/self` and
this one at `$(Pipeline.Workspace)/s/intune-gitops`. Paths in parameters are
relative to the calling repo; steps passed in `validationSteps` should set
`workingDirectory: $(Pipeline.Workspace)/s/self`.

| Parameter | Default | Meaning |
|---|---|---|
| `platform` | required | `windows` or `macos` |
| `manifestsPath` | required | The manifest tree, relative to the calling repo |
| `serviceConnection` | required | Azure service connection whose identity holds the Graph permissions (workload identity federation, no secret) |
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
- uses: windowsadmins/intune-gitops/pipelines/github@v0.1.0
  with:
    stage: plan
    platform: macos
    manifests: intune/manifests
```

`github/example-caller.yml` is a complete workflow to copy, including Graph
sign-in through OIDC with `azure/login` and no client secret.

| Input | Default | Meaning |
|---|---|---|
| `stage` | required | `lint`, `plan`, `apply` or `test` |
| `platform` | required | `windows` or `macos` |
| `manifests` | `manifests` | The manifest tree in the caller's workspace |
| `what-if` | `true` | For `apply`, log every write instead of making it |
| `graph-token` | none | For `apply`, a Graph access token |
| `min-desired-assignments` | `3` | The assignment floor |
| `protected-profiles` | none | Profiles another pipeline owns |
| `laptop-models-file` | none | macOS laptops Intune reports by identifier |
