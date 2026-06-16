from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - setup-time fallback
    yaml = None

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - setup-time fallback
    load_dotenv = None


ROOT_DIR = Path(__file__).resolve().parents[1]

DEFAULT_CONFIG: dict[str, Any] = {
    "video": {
        "width": 1280,
        "height": 720,
        "fps": 30,
        "scene_padding_seconds": 0.3,
        "crossfade_seconds": 0.5,
        "callout_fraction": 0,
        "enable_callouts": False,
    },
    "popups": {
        "enabled": True,
        "alignment_model": "whisper-1",
        "display_seconds": 1.05,
        "lead_seconds": 0.04,
        "max_drift_seconds": 0.45,
        "fallback_to_estimated_timing": True,
    },
    "script": {
        "model": "claude-sonnet-4-6",
        "target_word_count": 150,
        "target_scene_count": 7,
        "target_seconds_per_scene": 120,
        "words_per_second": 2.35,
        "shot_interval_seconds_min": 4,
        "shot_interval_seconds_max": 5,
        "shots_per_scene_min": 24,
        "shots_per_scene_max": 28,
        "max_final_images_per_scene": 15,
        "timed_text_popups": False,
    },
    "hybrid": {
        "enabled": False,
        "max_stage_images_per_scene": 10,
        "effects_enabled": False,
    },
    "fern": {
        "default_style_preset": "Fern-style AI documentary",
        "planner_model": "claude-sonnet-4-6",
        "planner_max_tokens": 6000,
        "planner_temperature": 0.65,
        "default_scene_count": 3,
        "image_budget_fraction": 0.35,
        "video_model": "",
        "estimated_video_second_usd": 0.12,
        "estimated_tts_minute_usd": 0.02,
        "motion_graphics_estimate_usd": 0.0,
        "quality_profiles": {
            "cheap": {"scene_count": 2},
            "balanced": {"scene_count": 3},
            "high": {"scene_count": 4},
        },
        "quality_costs": {
            "cheap": {"image_request_usd": 0.025, "video_second_usd": 0.08, "tts_minute_usd": 0.02},
            "balanced": {"image_request_usd": 0.039, "video_second_usd": 0.12, "tts_minute_usd": 0.02},
            "high": {"image_request_usd": 0.07, "video_second_usd": 0.2, "tts_minute_usd": 0.03},
        },
    },
    "visual_assets": {
        "max_backgrounds": 2,
        "max_characters": 2,
        "max_props": 4,
    },
    "tts": {
        "provider": "openai",
        "model": "gpt-4o-mini-tts",
        "voice": "cedar",
        "output_format": "wav",
        "instructions": (
            "Male narrator, medium-deep but not extremely deep. Clear YouTube explainer delivery, "
            "conversational and lightly dramatic, crisp close-mic sound, steady pacing, no muffled delivery."
        ),
    },
    "image": {
        "provider": "replicate",
        "model": "google/nano-banana",
        "fallback_model": "",
        "aspect_ratio": "16:9",
        "resolution": "1 MP",
        "output_format": "png",
        "style": "Documentary collage",
        "safety_rewrite": True,
        "num_inference_steps": 28,
        "go_fast": False,
        "max_workers": 1,
        "request_spacing_seconds": 13,
        "rate_limit_sleep_seconds": 18,
        "retry_attempts": 6,
        "estimated_request_cost_usd": 0.039,
        "max_run_image_cost_usd": 1.0,
        "add_title_banner": False,
        "add_intro_title_banner": False,
    },
    "intro": {
        "enabled": True,
        "duration_seconds": 2,
        "pan_seconds": 0.7,
        "zoom_seconds": 0.55,
        "hold_seconds": 0.45,
        "highlight": False,
        "target_index": 0,
        "grid_columns": 4,
        "grid_rows": 3,
    },
}


def load_environment() -> None:
    if load_dotenv:
        load_dotenv(ROOT_DIR / ".env")

    # LangSmith has used both LANGSMITH_* and LANGCHAIN_* names across releases.
    # Set the older aliases when the current names are present so tracing works
    # with both LangGraph and LangSmith client versions.
    if os.getenv("LANGSMITH_TRACING", "").lower() == "true":
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    if os.getenv("LANGSMITH_PROJECT"):
        os.environ.setdefault("LANGCHAIN_PROJECT", os.getenv("LANGSMITH_PROJECT", ""))


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    load_environment()

    config = deepcopy(DEFAULT_CONFIG)
    config_path = Path(path) if path else ROOT_DIR / "config.yaml"
    if config_path.exists():
        if yaml is None:
            raise RuntimeError("PyYAML is required to read config.yaml. Run: python -m pip install -r requirements.txt")
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        _deep_update(config, loaded)
    return config


def _deep_update(base: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
