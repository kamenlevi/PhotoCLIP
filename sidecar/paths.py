"""App data directory resolution. Same convention as Tauri's app_data_dir."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "photoclip"


def app_data_dir() -> Path:
    override = os.environ.get("PHOTOCLIP_DATA_DIR")
    if override:
        p = Path(override).expanduser()
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / APP_NAME
    elif sys.platform.startswith("linux"):
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
        p = base / APP_NAME
    else:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        p = base / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return app_data_dir() / "photoclip.db"


def thumb_dir() -> Path:
    p = app_data_dir() / "thumbs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def model_cache_dir() -> Path:
    p = app_data_dir() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p
