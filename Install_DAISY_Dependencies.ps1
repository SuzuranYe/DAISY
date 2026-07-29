#Requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$packages = @(
    [pscustomobject]@{
        Name = "Python 3.14"
        Id = "Python.Python.3.14"
        Purpose = "Runs the DAISY GUI and every task."
    }
    [pscustomobject]@{
        Name = "ExifTool"
        Id = "OliverBetz.ExifTool"
        Purpose = "Reads photo and video metadata and validates media structure."
    }
    [pscustomobject]@{
        Name = "FFmpeg (ffprobe)"
        Id = "Gyan.FFmpeg"
        Purpose = "Reads audio and video streams and validates media containers."
    }
    [pscustomobject]@{
        Name = "7-Zip"
        Id = "7zip.7zip"
        Purpose = "Reads and tests 7z, RAR, TAR, and other archive formats."
    }
)

$winget = Get-Command "winget.exe" -ErrorAction SilentlyContinue
if ($null -eq $winget) {
    throw "WinGet was not found. Install or update Microsoft App Installer first."
}

Write-Host "DAISY dependency setup"
Write-Host "Each package is explained and confirmed separately."

foreach ($package in $packages) {
    Write-Host ""
    Write-Host ("Package: {0} [{1}]" -f $package.Name, $package.Id)
    Write-Host ("Purpose: {0}" -f $package.Purpose)
    Write-Host "WinGet source and package agreements will be accepted for this package."
    $answer = Read-Host "Install or update this package? [y/N]"
    if ($answer -notmatch "^[Yy]$") {
        Write-Host "Skipped."
        continue
    }

    Write-Host ("Installing or updating {0}..." -f $package.Name)
    & $winget.Source install `
        --exact `
        --id $package.Id `
        --source winget `
        --accept-source-agreements `
        --accept-package-agreements
    $installExitCode = $LASTEXITCODE

    if ($installExitCode -ne 0) {
        & $winget.Source list `
            --exact `
            --id $package.Id `
            --source winget `
            --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            throw ("Failed to install {0}; WinGet exit code: {1}" -f `
                $package.Name, $installExitCode)
        }
        Write-Warning ("WinGet returned {0}, but {1} is already installed." -f `
            $installExitCode, $package.Name)
    }
}

$machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$env:Path = @($machinePath, $userPath) -join ";"

$repoRoot = $PSScriptRoot
$mainScript = Join-Path $repoRoot "Script\Script_DAISY_MAIN.py"
$pythonLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
$pythonRuntime = Get-Command "python.exe" -ErrorAction SilentlyContinue

Write-Host ""
if ($null -ne $pythonLauncher) {
    Write-Host "Running DAISY environment check with Python 3.14..."
    & $pythonLauncher.Source -3.14 $mainScript env-check
    $checkExitCode = $LASTEXITCODE
}
elseif ($null -ne $pythonRuntime) {
    Write-Host "Running DAISY environment check..."
    & $pythonRuntime.Source $mainScript env-check
    $checkExitCode = $LASTEXITCODE
}
else {
    $checkExitCode = 2
    Write-Warning "Python is installed but is not visible in this PowerShell session."
}

if ($checkExitCode -ne 0) {
    Write-Warning "Automatic verification did not complete."
    Write-Host "Close this window, open a new PowerShell window, and run:"
    Write-Host "  python .\Script\Script_DAISY_MAIN.py env-check"
    exit $checkExitCode
}

Write-Host ""
Write-Host "DAISY dependencies are ready."
Write-Host "Close and reopen DAISY before starting the GUI."
