from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT_DIR / "output"
CACHE_DIR = ROOT_DIR / ".cache"


def run_dir(run_id: str) -> Path:
    return OUTPUT_DIR / run_id


def ensure_run_dirs(run_id: str) -> Path:
    base = run_dir(run_id)
    for child in ("assets", "images", "audio", "clips"):
        (base / child).mkdir(parents=True, exist_ok=True)
    return base


def ensure_cache_dirs() -> None:
    (CACHE_DIR / "images").mkdir(parents=True, exist_ok=True)
