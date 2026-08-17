<#
Local CI mirror - equivalent to .github/workflows/ci.yml (parity: the
Ruff/Mypy target lists here MUST stay in sync with the workflow steps).
Runs locally for real; the CI first run is triggered by a push.

Usage:
    .\scripts\ci.ps1               # ruff + mypy + pytest + pre_push_scan
    .\scripts\ci.ps1 -BuildSmoke   # also run the PyInstaller onedir build smoke
#>
param(
    [switch]$BuildSmoke
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectDir
try {
    # Must match the Ruff step targets in .github/workflows/ci.yml.
    $RuffTargets = @(
        "src/data/cache.py",
        "src/data/rate_limit.py",
        "src/data/providers/tushare.py",
        "src/config.py",
        "src/reports/llm_client.py",
        "src/app/run_records.py",
        "src/reports/renderer.py",
        "src/app/service.py",
        "src/app/cli.py",
        "src/app/gui.py",
        "src/analysis/backtest.py",
        "tests/test_backtest_budget.py",
        "tests/test_llm_stream.py",
        "tests/test_app_cli.py",
        "tests/test_gui_worker.py",
        "tests/test_providers_retry.py",
        "tests/test_cache_corrupt.py",
        "tests/test_provider_contracts.py",
        "tests/test_concurrency.py",
        "tests/test_app_cancel.py"
    )
    # Only source is type-checked (test doubles need not pass mypy);
    # matches the yml Mypy step.
    $MypyTargets = $RuffTargets | Where-Object { $_ -like "src/*" }

    Write-Host "[1/5] Ruff (maintained set)"
    python -m ruff check $RuffTargets

    Write-Host "[2/5] Mypy (maintained set)"
    python -m mypy $MypyTargets --follow-imports=skip --ignore-missing-imports

    Write-Host "[3/5] Pytest (offline suite; integration gated out)"
    $Basetemp = Join-Path $ProjectDir ".workspace\tmp\t79-ci"
    python -m pytest -m "not integration" -p no:cacheprovider --basetemp $Basetemp

    Write-Host "[4/5] Pre-push scan (local boundary equivalence)"
    python scripts/pre_push_scan.py

    if ($BuildSmoke) {
        Write-Host "[5/5] Build smoke (PyInstaller onedir, no installer)"
        .\scripts\build_windows.ps1 -SkipInstaller
        if (-not (Test-Path "dist\StockAnalysis\StockAnalysis.exe")) {
            throw "dist\StockAnalysis\StockAnalysis.exe was not produced"
        }
        Write-Host "Build smoke OK: dist\StockAnalysis\StockAnalysis.exe"
    }
    else {
        Write-Host "[5/5] Build smoke skipped (pass -BuildSmoke to run)"
    }

    Write-Host "CI mirror passed."
}
finally {
    Pop-Location
}