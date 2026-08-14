param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$ProjectMetadata = Get-Content (Join-Path $ProjectDir "pyproject.toml") -Raw
if ($ProjectMetadata -notmatch '(?m)^version\s*=\s*"([^"]+)"') {
    throw "Unable to read project version from pyproject.toml."
}
$AppVersion = $Matches[1]
$InstallerPath = "installer\StockAnalysis-Setup-$AppVersion.exe"

Push-Location $ProjectDir
try {
    python -m PyInstaller --clean --noconfirm packaging\StockAnalysis.spec

    if ($SkipInstaller) {
        Write-Host "Application build completed: dist\StockAnalysis\StockAnalysis.exe"
        exit 0
    }

    $CompilerCandidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $InnoCompiler = $CompilerCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $InnoCompiler) {
        throw "Inno Setup 6 not found. Install it or rerun with -SkipInstaller."
    }

    & $InnoCompiler "packaging\StockAnalysis.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compiler failed with exit code $LASTEXITCODE."
    }
    if (-not (Test-Path $InstallerPath)) {
        throw "Installer compiler finished without creating the expected output file."
    }
    Write-Host "Installer build completed: $InstallerPath"
}
finally {
    Pop-Location
}
