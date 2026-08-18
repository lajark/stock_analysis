"""Web GUI launcher — pywebview window over the local Liquid-Glass frontend.

Usage:
    python -m src.app.webgui.app

The legacy tkinter shell remains available via ``python -m src.app.gui``.
"""

from __future__ import annotations

import atexit

import webview
from loguru import logger

from src.app.webgui.server import WebGuiServer
from src.runtime_paths import user_data_root


def _configure_logging() -> None:
    log_dir = user_data_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        log_dir / "app_{time:YYYY-MM-DD}.log",
        level="INFO",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
    )


def main() -> None:
    """Launch the webview window bound to the local web server."""
    _configure_logging()
    server = WebGuiServer()
    server.start()
    atexit.register(server.stop)

    webview.create_window(
        "股票分析工具",
        url=server.url,
        width=1280,
        height=880,
        min_size=(1080, 720),
        background_color="#111318",
        text_select=True,
    )
    webview.start()


if __name__ == "__main__":
    main()
