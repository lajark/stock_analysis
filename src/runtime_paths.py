"""Runtime path helpers for source and packaged application modes."""

import os
import sys
from pathlib import Path

APP_DIRECTORY_NAME = "StockAnalysis"


def is_frozen() -> bool:
    """Return whether the application is running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Return the directory containing bundled read-only application resources."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root)
    return Path(__file__).resolve().parent.parent


def user_data_root() -> Path:
    """Return the writable application data directory.

    ``STOCK_ANALYSIS_HOME`` is primarily useful for portable runs and tests.
    Source mode keeps the historical project-local paths for compatibility.
    """
    override = os.getenv("STOCK_ANALYSIS_HOME")
    if override:
        return Path(override).expanduser().resolve()

    if is_frozen():
        local_app_data = os.getenv("LOCALAPPDATA")
        base_dir = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base_dir / APP_DIRECTORY_NAME

    return resource_root()


def settings_path() -> Path:
    """Return the user-editable environment settings file."""
    return user_data_root() / ".env"

