#Requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

Write-Host "DAISY Python cold-start installer"
Write-Host "This script only installs Python 3.14."
Write-Host "ExifTool, ffprobe, and 7-Zip are detected and installed from the DAISY GUI."
Write-Host ""
Write-Host "Package: Python 3.14 [Python.Python.3.14]"
Write-Host "Purpose: Runs the DAISY GUI and every DAISY task."
Write-Host "WinGet source and package agreements will be accepted for this package."

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
        throw "Failed to install Python 3.14; WinGet exit code: $installExitCode"
    }
    Write-Warning "WinGet returned $installExitCode, but Python 3.14 is installed."
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
Write-Host "Python installation is complete."
Write-Host "Return to the project root and double-click Start_DAISY_GUI.pyw."
Write-Host "The Environment Check page can install the remaining tools."
