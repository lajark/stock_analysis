"""Regression checks for the user-facing Windows launcher shortcuts."""

from pathlib import Path


def test_inno_setup_creates_start_menu_and_default_desktop_shortcuts() -> None:
    project_root = Path(__file__).resolve().parents[1]
    script = (project_root / "packaging" / "StockAnalysis.iss").read_text(encoding="utf-8")

    assert (
        r'Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"' in script
    )
    assert (
        r'Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; '
        "Tasks: desktopicon"
    ) in script
    assert 'Name: "desktopicon"; Description: "创建桌面快捷方式（默认）"' in script
    assert 'Flags: unchecked' not in script


def test_pyinstaller_bundles_akshare_fallback_provider() -> None:
    project_root = Path(__file__).resolve().parents[1]
    script = (project_root / "packaging" / "StockAnalysis.spec").read_text(
        encoding="utf-8"
    )
    excludes = script.split("excludes=[", maxsplit=1)[1].split("]", maxsplit=1)[0]

    assert 'collect_all("akshare")' in script
    assert '"akshare"' not in excludes
    assert '"openpyxl"' not in excludes
