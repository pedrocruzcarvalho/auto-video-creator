from __future__ import annotations

from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT_DIR / "output"


def run_dir(run_id: str) -> Path:
    return OUTPUT_DIR / run_id
