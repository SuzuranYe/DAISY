#Requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

# Keep this file ASCII-only: Windows PowerShell 5.1 can misread UTF-8 without BOM.
Write-Host "DAISY Python installer"
Write-Host "This script installs or updates Python 3.14 only."
Write-Host "Use the Environment Check page in the DAISY GUI to detect the remaining tools and install supported ones."
Write-Host ""
Write-Host "Package: Python 3.14 [Python.Python.3.14]"
Write-Host "Purpose: Required by the DAISY GUI and all tasks."
Write-Host "This action accepts the required WinGet source and package agreements."

$answer = Read-Host "Install or update Python 3.14? [y/N]"
if ($answer -notmatch "^[Yy]$") {
    Write-Host "Cancelled; no package was installed."
    exit 0
}

$winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
if ($null -eq $winget) {
    throw "WinGet was not found. Install or update Microsoft App Installer first."
}

& $winget.Source install `
    --exact `
    --id "Python.Python.3.14" `
    --source winget `
    --accept-source-agreements `
    --accept-package-agreements `
    --disable-interactivity
$installExitCode = $LASTEXITCODE

if ($installExitCode -ne 0) {
    & $winget.Source list `
        --exact `
        --id "Python.Python.3.14" `
        --source winget `
        --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install or update Python 3.14; WinGet exit code: $installExitCode"
    }
    Write-Warning "WinGet returned $installExitCode, but Python 3.14 is available."
}

$machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$env:Path = @($machinePath, $userPath) -join ";"

$pythonLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
$pythonRuntime = Get-Command "python.exe" -ErrorAction SilentlyContinue
if ($null -ne $pythonLauncher) {
    & $pythonLauncher.Source -3.14 --version
}
elseif ($null -ne $pythonRuntime) {
    & $pythonRuntime.Source --version
}
else {
    Write-Warning "Python was installed but is not visible in this PowerShell session."
}

Write-Host ""
Write-Host "WinGet reports Python 3.14 is installed."
Write-Host "Open the project root and double-click Start_DAISY_GUI.pyw."
Write-Host "Use the Environment Check page to detect the remaining tools and install supported ones."
