from __future__ import annotations

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


ROOT_DIR = Path(__file__).resolve().parents[1]


def load_environment() -> None:
    if load_dotenv:
        load_dotenv(ROOT_DIR / ".env")
