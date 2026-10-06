# =============================================================================
# Settings Catalog Definition Finder
# =============================================================================
# Searches the Microsoft Intune Settings Catalog for setting definitions by
# name or description and prints their definition IDs, for use in Settings
# Catalog YAML or JSON.
#
# Authenticates to Microsoft Graph with the current az session (delegated).
# Run 'az login' first; no service-principal secret is required.
#
# Usage:
#   ./Get-SettingsCatalogDefinition.ps1 -SearchTerm "user profile"
#   ./Get-SettingsCatalogDefinition.ps1 -SearchTerm "defender" -TenantId <tenant-id>
#
# Prerequisites:
#   - PowerShell 7 (Windows, macOS or Linux) and the Azure CLI
#   - An account that can read Intune configuration
#     (DeviceManagementConfiguration.Read.All)
# =============================================================================

param(
    [string]$SearchTerm = "user profile",

    # Optional. When given, signs in to (or switches to) this tenant first.
    [string]$TenantId
)

Write-Host ""
Write-Host "======================================================================"
Write-Host "  Settings Catalog Definition Finder"
Write-Host "======================================================================"
Write-Host ""

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Error "Azure CLI is not installed. See https://learn.microsoft.com/cli/azure/install-azure-cli"
    exit 1
}

Write-Host "Checking Azure CLI login status..."
$currentTenant = az account show --query "tenantId" -o tsv 2>$null
if (-not $currentTenant) {
    Write-Host "Not logged in. Starting az login..."
    if ($TenantId) { az login --tenant $TenantId --allow-no-subscriptions | Out-Null }
    else { az login --allow-no-subscriptions | Out-Null }
} elseif ($TenantId -and $currentTenant -ne $TenantId) {
    Write-Host "Logged into a different tenant. Switching..."
    az login --tenant $TenantId --allow-no-subscriptions | Out-Null
} else {
    Write-Host "Using the current az session."
}
Write-Host ""

# Get a Microsoft Graph access token from the current az session.
# The signed-in user's delegated token is used; no service-principal secret.
Write-Host "Authenticating to Microsoft Graph via the current az session..."
$token = az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv 2>$null

if (-not $token) {
    Write-Error "Failed to obtain a Microsoft Graph access token from the az session. Run 'az login' and retry."
    exit 1
}

Write-Host "Authentication successful."
Write-Host ""

$headers = @{
    Authorization = "Bearer $token"
    "Content-Type" = "application/json"
}

Write-Host "Searching Settings Catalog for: $SearchTerm`n"

# Query the settingDefinitions endpoint
$url = "https://graph.microsoft.com/beta/deviceManagement/configurationSettings"
$allSettings = @()

try {
    do {
        $response = Invoke-RestMethod -Uri $url -Headers $headers -Method Get
        $allSettings += $response.value
        $url = $response.'@odata.nextLink'
    } while ($url)
    
    Write-Host "Total settings retrieved: $($allSettings.Count)"
    
    # Filter by search term
    $found = $allSettings | Where-Object { 
        $_.displayName -like "*$SearchTerm*" -or 
        $_.description -like "*$SearchTerm*"
    }
    
    if ($found) {
        Write-Host "`nFound $($found.Count) matching settings:`n"
        foreach ($setting in $found) {
            Write-Host ("=" * 100)
            Write-Host "Display Name: $($setting.displayName)"
            Write-Host "Definition ID: $($setting.id)"
            Write-Host "Description: $($setting.description)"
            if ($setting.categoryId) { Write-Host "Category: $($setting.categoryId)" }
            if ($setting.settingUsage) { Write-Host "Usage: $($setting.settingUsage)" }
            Write-Host ""
        }
        
        Write-Host ""
        Write-Host "======================================================================"
        Write-Host "  Use the 'Definition ID' value in your Settings Catalog YAML files"
        Write-Host "======================================================================"
        Write-Host ""
    } else {
        Write-Host "`nNo settings found matching '$SearchTerm'"
        Write-Host ""
        Write-Host "Try alternative search terms like:"
        Write-Host "  - For security settings: ./Get-SettingsCatalogDefinition.ps1 -SearchTerm 'defender'"
        Write-Host "  - For update settings: ./Get-SettingsCatalogDefinition.ps1 -SearchTerm 'windows update'"
        Write-Host "  - For user settings: ./Get-SettingsCatalogDefinition.ps1 -SearchTerm 'user experience'"
        Write-Host ""
    }
}
catch {
    Write-Error "Failed to query Graph API: $($_.Exception.Message)"
    if ($_.Exception.Response) {
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        $reader.BaseStream.Position = 0
        $responseBody = $reader.ReadToEnd()
        Write-Host "Response: $responseBody"
    }
    Write-Host ""
}
