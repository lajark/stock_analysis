# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

project_root = Path(SPECPATH).parent

akshare_datas, akshare_binaries, akshare_hiddenimports = collect_all("akshare")
tkweb_datas, tkweb_binaries, tkweb_hiddenimports = collect_all("tkinterweb")
tkhtml_datas, tkhtml_binaries, tkhtml_hiddenimports = collect_all("tkinterweb_tkhtml")
# pywebview (Web GUI shell): collect its platform modules + CLI bridge assets.
webview_datas, webview_binaries, webview_hiddenimports = collect_all("webview")

datas = [
    (str(project_root / "config"), "config"),
    # Version source: after packaging __file__ lives under _internal, so the
    # bundled copy of pyproject.toml keeps local_version() readable.
    (str(project_root / "pyproject.toml"), "."),
    (str(project_root / "knowledge_base"), "knowledge_base"),
    (str(project_root / "src" / "reports" / "prompts"), "src/reports/prompts"),
    (str(project_root / "src" / "app" / "webgui" / "static"), "src/app/webgui/static"),
] + akshare_datas + tkweb_datas + tkhtml_datas + webview_datas

a = Analysis(
    [str(project_root / "src" / "app" / "webgui" / "app.py")],
    pathex=[str(project_root)],
    binaries=akshare_binaries + tkweb_binaries + tkhtml_binaries + webview_binaries,
    datas=datas,
    hiddenimports=(
        akshare_hiddenimports
        + tkweb_hiddenimports
        + tkhtml_hiddenimports
        + webview_hiddenimports
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "duckdb",
        "fsspec",
        "jupyter",
        "matplotlib",
        "mypy",
        "notebook",
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
