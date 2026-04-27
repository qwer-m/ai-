# Codex Configuration Script for API Router (Windows)
# This script configures Codex to use your API Router instance
# Run with: powershell -ExecutionPolicy Bypass -File setup-codex.ps1 -BaseUrl https://your-domain.tld -ApiKey YOUR_KEY
# Or via one-liner: & { $url='https://your-domain.tld'; $key='YOUR_KEY'; iwr -useb $url/setup-codex.ps1 | iex }

param(
    [string]$BaseUrl,
    [string]$ApiKey,
    [switch]$Test,
    [switch]$Show,
    [switch]$Help
)

# Check for pre-set variables from one-liner command (use different names to avoid conflict)
if (-not $BaseUrl -and (Test-Path Variable:url)) { $BaseUrl = $url }
if (-not $ApiKey -and (Test-Path Variable:key)) { $ApiKey = $key }

# Configuration
$DefaultBaseUrl = "http://localhost:8080"
$CodexConfigDir = "$env:USERPROFILE\.codex"
$CodexConfigFile = "$CodexConfigDir\config.toml"

# Color functions for output
function Write-Info {
    param([string]$Message)
    Write-Host "[INFO]" -ForegroundColor Blue -NoNewline
    Write-Host " $Message"
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS]" -ForegroundColor Green -NoNewline
    Write-Host " $Message"
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[WARNING]" -ForegroundColor Yellow -NoNewline
    Write-Host " $Message"
}

function Write-Error {
    param([string]$Message)
    Write-Host "[ERROR]" -ForegroundColor Red -NoNewline
    Write-Host " $Message"
}

function Write-Utf8NoBomFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )

    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

# Function to show help
function Show-Help {
    Write-Host @"
Codex Configuration Script for API Router (Windows)

Usage: powershell -ExecutionPolicy Bypass -File setup-codex.ps1 [OPTIONS]

Options:
  -BaseUrl <URL> Set the API Router base URL (default: $DefaultBaseUrl)
  -ApiKey <KEY>  Set the API key
  -Test          Test API connection only (requires -BaseUrl and -ApiKey)
  -Show          Show current settings and exit
  -Help          Show this help message

Examples:
  .\setup-codex.ps1 -BaseUrl https://your-domain.tld -ApiKey your-api-key-here
  .\setup-codex.ps1 -Test -BaseUrl https://your-domain.tld -ApiKey your-api-key-here
  .\setup-codex.ps1 -Show

Interactive mode (no arguments):
  .\setup-codex.ps1

PowerShell Execution Policy:
  If you get an execution policy error, run:
  powershell -ExecutionPolicy Bypass -File setup-codex.ps1
"@
    exit 0
}

# Function to backup existing settings
function Backup-Settings {
    if (Test-Path $CodexConfigFile) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $backupFile = "$CodexConfigFile.backup.$timestamp"
        Copy-Item -Path $CodexConfigFile -Destination $backupFile
        Write-Info "Backed up existing settings to: $backupFile"
    }
}

# Function to create settings directory
function New-SettingsDirectory {
    if (-not (Test-Path $CodexConfigDir)) {
        New-Item -ItemType Directory -Path $CodexConfigDir -Force | Out-Null
        Write-Info "Created Codex configuration directory: $CodexConfigDir"
    }
}

# Function to validate API key format
function Test-ApiKey {
    param([string]$ApiKey)
    
    if ($ApiKey -match '^[A-Za-z0-9_-]+$') {
        return $true
    } else {
        Write-Error "Invalid API key format. API key should contain only alphanumeric characters, hyphens, and underscores."
        return $false
    }
}

# Function to test API connection
function Test-ApiConnection {
    param(
        [string]$BaseUrl,
        [string]$ApiKey
    )
    
    Write-Info "Testing API connection..."
    
    try {
        $headers = @{
            "Content-Type" = "application/json"
            "X-API-Key" = $ApiKey
        }
        
        # Team vs user balance with legacy fallback
        $isTeam = $BaseUrl.EndsWith("/team")
        if ($isTeam) {
            $uri = "$BaseUrl/api/v1/team/stats/spending"
            $balanceField = "daily_remaining"
            $balanceLabel = "Daily remaining"
        } else {
            $uri = "$BaseUrl/api/v1/user/balance"
            $balanceField = "balance"
            $balanceLabel = "Current balance"
        }
        
        $response = Invoke-RestMethod -Uri $uri -Method Get -Headers $headers -ErrorAction Stop
        
        if ($response.$balanceField) {
            Write-Success "API connection successful! ${balanceLabel}: `$$($response.$balanceField)"
        } else {
            Write-Success "API connection successful!"
        }
        return $true
    }
    catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode -eq 401) {
            Write-Error "API key authentication failed. Please check your API key."
        } elseif ($_.Exception.Message -like "*Unable to connect*" -or $_.Exception.Message -like "*could not be resolved*") {
            Write-Error "Cannot connect to API server. Please check the URL and your internet connection."
        } else {
            Write-Error "API test failed: $($_.Exception.Message)"
        }
        return $false
    }
}

# Function to create or update Codex configuration
function New-Settings {
    param(
        [string]$BaseUrl,
        [string]$ApiKey
    )

    $configFile = "$CodexConfigDir\config.toml"
    $authFile = "$CodexConfigDir\auth.json"

    # Update or create config.toml
    if (Test-Path $configFile) {
        Write-Info "Updating existing config.toml (preserving other settings)..."

        $content = Get-Content $configFile -Raw

        # Check if apirouter section exists
        if ($content -match '\[model_providers\.apirouter\]') {
            # Update base_url line
            $content = $content -replace 'base_url = ".*?"', "base_url = `"$BaseUrl/v1`""
        } else {
            # Append apirouter section
            $content += @"

[model_providers.apirouter]
name = "apirouter"
base_url = "$BaseUrl/v1"
wire_api = "responses"
requires_openai_auth = true
env_key = "APIROUTER_API_KEY"
"@
        }

        # Update model_provider if needed
        if ($content -notmatch 'model_provider = "apirouter"') {
            if ($content -match 'model_provider = ') {
                $content = $content -replace 'model_provider = ".*?"', 'model_provider = "apirouter"'
            } else {
                $content = "model_provider = `"apirouter`"`n" + $content
            }
        }

        Set-Content -Path $configFile -Value $content -Encoding UTF8
    } else {
        # Create new config.toml with defaults
        Write-Info "Creating new config.toml with defaults..."
        $config = @"
model_provider = "apirouter"
model = "gpt-5-codex"
model_reasoning_effort = "high"

[model_providers.apirouter]
name = "apirouter"
base_url = "$BaseUrl/v1"
wire_api = "responses"
requires_openai_auth = true
env_key = "APIROUTER_API_KEY"
"@
        Set-Content -Path $configFile -Value $config -Encoding UTF8
    }

    Write-Success "Codex configuration written to: $configFile"

    # Update or create auth.json
    if (Test-Path $authFile) {
        Write-Info "Updating existing auth.json (preserving other keys)..."

        try {
            $auth = Get-Content $authFile -Raw | ConvertFrom-Json
            $authHash = @{}
            $auth.PSObject.Properties | ForEach-Object {
                $authHash[$_.Name] = $_.Value
            }
            $authHash["OPENAI_API_KEY"] = $ApiKey
            $authHash["APIROUTER_API_KEY"] = $ApiKey
            $auth = [PSCustomObject]$authHash
            $authJson = ($auth | ConvertTo-Json) + "`n"
            Write-Utf8NoBomFile -Path $authFile -Content $authJson
        }
        catch {
            Write-Warning "Failed to merge auth.json, creating new one..."
            $auth = @{
                OPENAI_API_KEY = $ApiKey
                APIROUTER_API_KEY = $ApiKey
            }
            $authJson = ($auth | ConvertTo-Json) + "`n"
            Write-Utf8NoBomFile -Path $authFile -Content $authJson
        }
    } else {
        Write-Info "Creating new auth.json..."
        $auth = @{
            OPENAI_API_KEY = $ApiKey
            APIROUTER_API_KEY = $ApiKey
        }
        $authJson = ($auth | ConvertTo-Json) + "`n"
        Write-Utf8NoBomFile -Path $authFile -Content $authJson
    }

    Write-Success "Codex auth file written to: $authFile"
    return $true
}

# Function to display current settings
function Show-Settings {
    if (Test-Path $CodexConfigFile) {
        Write-Info "Current Codex settings:"
        Write-Host "----------------------------------------"
        Get-Content $CodexConfigFile
        Write-Host "----------------------------------------"
    } else {
        Write-Info "No existing Codex configuration found."
    }
    
    Write-Host ""
    Write-Info "Current environment variables:"
    Write-Host "----------------------------------------"
    $apirouterKey = [Environment]::GetEnvironmentVariable("APIROUTER_API_KEY", [EnvironmentVariableTarget]::User)
    
    if ($apirouterKey) {
        $maskedKey = if ($apirouterKey.Length -gt 12) { 
            "$($apirouterKey.Substring(0, 8))...$($apirouterKey.Substring($apirouterKey.Length - 4))" 
        } else { 
            "$($apirouterKey.Substring(0, [Math]::Min(4, $apirouterKey.Length)))..." 
        }
        Write-Info "APIROUTER_API_KEY: $maskedKey"
    } else {
        Write-Info "APIROUTER_API_KEY: (not set)"
    }
    Write-Host "----------------------------------------"
}

# Main function
function Main {
    Write-Info "Codex Configuration Script for API Router"
    Write-Host "======================================="
    Write-Host ""
    
    # Handle command line arguments
    if ($Help) {
        Show-Help
    }
    
    if ($Show) {
        Show-Settings
        exit 0
    }
    
    # Interactive mode if no URL or Key provided
    if (-not $BaseUrl -and -not $ApiKey) {
        Write-Info "Interactive setup mode"
        Write-Host ""
        
        # Get base URL
        $inputUrl = Read-Host "Enter API Router URL [$DefaultBaseUrl]"
        if ([string]::IsNullOrWhiteSpace($inputUrl)) {
            $BaseUrl = $DefaultBaseUrl
        } else {
            $BaseUrl = $inputUrl
        }
        
        # Get API key
        while ([string]::IsNullOrWhiteSpace($ApiKey)) {
            $ApiKey = Read-Host "Enter your API key"
            if ([string]::IsNullOrWhiteSpace($ApiKey)) {
                Write-Warning "API key is required"
            } elseif (-not (Test-ApiKey $ApiKey)) {
                $ApiKey = ""
            }
        }
    }
    
    # Validate inputs
    if ([string]::IsNullOrWhiteSpace($BaseUrl) -or [string]::IsNullOrWhiteSpace($ApiKey)) {
        Write-Error "Both URL and API key are required"
        Write-Info "Use -Help for usage information"
        exit 1
    }
    
    # Validate API key
    if (-not (Test-ApiKey $ApiKey)) {
        exit 1
    }
    
    # Remove trailing slash from URL
    $BaseUrl = $BaseUrl.TrimEnd('/')
    
    Write-Info "Configuration:"
    Write-Info "  Base URL: $BaseUrl"
    $maskedKey = if ($ApiKey.Length -gt 12) { 
        "$($ApiKey.Substring(0, 8))...$($ApiKey.Substring($ApiKey.Length - 4))" 
    } else { 
        "$($ApiKey.Substring(0, [Math]::Min(4, $ApiKey.Length)))..." 
    }
    Write-Info "  API Key: $maskedKey"
    Write-Host ""
    
    # Test API connection
    $connectionSuccess = Test-ApiConnection -BaseUrl $BaseUrl -ApiKey $ApiKey
    
    if (-not $connectionSuccess) {
        if ($Test) {
            exit 1
        }
        
        $continue = Read-Host "API test failed. Continue anyway? (y/N)"
        if ($continue -notmatch '^[Yy]$') {
            Write-Info "Setup cancelled"
            exit 1
        }
    }
    
    # Exit if test only
    if ($Test) {
        Write-Success "API test completed successfully"
        exit 0
    }
    
    # Create settings directory
    New-SettingsDirectory
    
    # Backup existing settings
    Backup-Settings
    
    # Create new settings
    if (New-Settings -BaseUrl $BaseUrl -ApiKey $ApiKey) {
        Write-Host ""
        
        # Also set environment variables for Windows
        Write-Info "Setting environment variables..."
        try {
            # Set user environment variables (persistent across sessions)
            [Environment]::SetEnvironmentVariable("APIROUTER_API_KEY", $ApiKey, [EnvironmentVariableTarget]::User)
            
            # Also set for current session
            $env:APIROUTER_API_KEY = $ApiKey
            
            Write-Success "Environment variables set successfully"
        }
        catch {
            Write-Warning "Failed to set environment variables: $($_.Exception.Message)"
            Write-Info "You may need to set them manually:"
            Write-Info "  APIROUTER_API_KEY=$ApiKey"
        }
        
        Write-Host ""
        Write-Success "Codex has been configured successfully!"
        Write-Info "You can now use Codex with your API Router API router."
        Write-Info ""
        Write-Info "Configuration file location: $CodexConfigFile"
        Write-Info "Environment variable APIROUTER_API_KEY has been set"
        
        if (Test-Path $CodexConfigFile) {
            Write-Host ""
            Write-Info "Current settings:"
            Get-Content $CodexConfigFile
        }
    } else {
        Write-Error "Failed to create Codex settings"
        exit 1
    }
}

# Run main function
Main

