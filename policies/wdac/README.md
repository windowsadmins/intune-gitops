# App Control for Business (WDAC) samples

Two policies that let software installed by your management tools run under App
Control without listing every app by hash.

| File | Type | What it allows |
|---|---|---|
| `managed-installers-base.xml` | Base | Windows, Microsoft-signed and Store code, plus anything a **managed installer** wrote (the `Enabled:Managed Installer` option). Ships in **audit mode**. |
| `enterprise-binaries-supplemental.xml` | Supplemental to the base | Binaries signed by your own enterprise code-signing certificate, such as in-house tools and repackaged installers. |

The PolicyIDs here were generated for this repo. Generate your own before you
deploy, so your policies cannot collide with anyone else who copied these; the
supplemental's `BasePolicyID` must equal the base's `PolicyID`.

## Make them yours

Give both policies new IDs, then point the supplemental at the new base. In
PowerShell on Windows, with the ConfigCI module:

```
Set-CIPolicyIdInfo -FilePath .\managed-installers-base.xml -ResetPolicyID
```

```
Set-CIPolicyIdInfo -FilePath .\enterprise-binaries-supplemental.xml -ResetPolicyID -BasePolicyToSupplementPath .\managed-installers-base.xml
```

The supplemental's signer is a placeholder: `Example Enterprise Code Signing
Certificate` with an all-zero TBS hash, which matches nothing. Replace it with a
signer rule built from your certificate:

```
Add-SignerRule -FilePath .\enterprise-binaries-supplemental.xml -CertificatePath .\your-signing-cert.cer -User -Kernel
```

Then delete the placeholder `ID_SIGNER_S_0` signer and its references.

## Managed installers

`Enabled:Managed Installer` trusts files written by a process that an AppLocker
policy designates as a managed installer. The base policy does not designate
one. Do that separately: Intune's built-in managed installer setting covers the
Intune Management Extension, and a custom AppLocker `ManagedInstaller` rule
collection covers a client such as Cimian (`managedsoftwareupdate.exe` and the
installers it launches).

## Deploying

Leave the base in audit mode until the `CodeIntegrity` operational event log
shows no blocks you did not expect, across a full software update cycle. Then
remove the `Enabled:Audit Mode` rule:

```
Set-RuleOption -FilePath .\managed-installers-base.xml -Option 3 -Delete
```

Convert each policy to binary and upload it through an Intune App Control for
Business policy, or as a custom OMA-URI under
`./Vendor/MSFT/ApplicationControl/Policies/<PolicyID>/Policy`:

```
ConvertFrom-CIPolicy -XmlFilePath .\managed-installers-base.xml -BinaryFilePath .\base.cip
```
