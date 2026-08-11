from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _default_data_dir() -> Path:
    override = os.getenv("FASTFR_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        root = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "FastFR"
    return Path.home() / ".local" / "share" / "fast-fr"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resource_root() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    return Path(bundled) if bundled else _project_root()


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path = _default_data_dir()
    host: str = "127.0.0.1"
    port: int = int(os.getenv("FASTFR_PORT", "8765"))
    session_hours: int = 12
    max_upload_bytes: int = 25 * 1024 * 1024
    max_pdf_pages: int = 25
    max_image_pixels: int = 40_000_000
    max_pdf_total_pixels: int = 60_000_000
    model_dir: Path = Path(os.getenv("FASTFR_MODEL_DIR", _resource_root() / "models"))
    ui_dist: Path = Path(os.getenv("FASTFR_UI_DIST", _resource_root() / "dist"))
    dev_mode: bool = os.getenv("FASTFR_DEV", "0") == "1"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "fastfr.sqlite3"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def master_key_path(self) -> Path:
        return self.data_dir / "master.key"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "fastfr.log"

    @property
    def yunet_path(self) -> Path:
        return self.model_dir / "face_detection_yunet_2023mar.onnx"

    @property
    def sface_path(self) -> Path:
        return self.model_dir / "face_recognition_sface_2021dec.onnx"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.blob_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
