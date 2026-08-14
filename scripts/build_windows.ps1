param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot

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
    if (-not (Test-Path "installer\StockAnalysis-Setup-1.1.0.exe")) {
        throw "Installer compiler finished without creating the expected output file."
    }
    Write-Host "Installer build completed: installer\StockAnalysis-Setup-1.1.0.exe"
}
finally {
    Pop-Location
}
