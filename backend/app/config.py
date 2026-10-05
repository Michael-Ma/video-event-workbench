from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    max_upload_mb: int = 2048

    @classmethod
    def from_env(cls) -> "Settings":
        folder = Path(os.getenv("VEW_DATA_DIR", ".data"))
        if not folder.is_absolute():
            folder = ROOT / folder
        return cls(
            data_dir=folder.resolve(),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            max_upload_mb=int(os.getenv("VEW_MAX_UPLOAD_MB", "2048")),
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.sqlite3"

    def ensure_dirs(self) -> None:
        for name in ("media", "runs", "tmp"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)

