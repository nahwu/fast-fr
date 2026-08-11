from __future__ import annotations

import logging
import os
import threading
import time
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler

import uvicorn

from .config import settings


def _configure_file_logging() -> None:
    settings.ensure_directories()
    handler = RotatingFileHandler(
        settings.log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def _open_browser() -> None:
    time.sleep(1.5)
    webbrowser.open(f"http://{settings.host}:{settings.port}")


def _already_running() -> bool:
    try:
        with urllib.request.urlopen(
            f"http://{settings.host}:{settings.port}/api/system/health", timeout=0.75
        ) as response:
            return response.status == 200
    except Exception:
        return False


def main() -> None:
    _configure_file_logging()
    if _already_running():
        if os.getenv("FASTFR_NO_BROWSER") != "1":
            webbrowser.open(f"http://{settings.host}:{settings.port}")
        return
    if os.getenv("FASTFR_NO_BROWSER") != "1":
        threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run(
        "fastfr.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
