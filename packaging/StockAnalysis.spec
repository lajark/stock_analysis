# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_root = Path(SPECPATH).parent

datas = [
    (str(project_root / "config"), "config"),
    (str(project_root / "knowledge_base"), "knowledge_base"),
    (str(project_root / "src" / "reports" / "prompts"), "src/reports/prompts"),
]

a = Analysis(
    [str(project_root / "src" / "app" / "gui.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "akshare",
        "duckdb",
        "fsspec",
        "jupyter",
        "matplotlib",
        "mypy",
        "notebook",
        "openpyxl",
        "pyarrow",
        "pytest",
        "ruff",
        "scipy",
        "sklearn",
        "sqlalchemy",
        "torch",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StockAnalysis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="StockAnalysis",
)
