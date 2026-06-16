from __future__ import annotations

import hashlib
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

from .config import load_environment
from .paths import ensure_run_dirs, run_dir


def generate_pvideo_clip(
    *,
    prompt: str,
    run_id: str,
    clip_key: str,
    image_path: str | Path | None = None,
    duration: int = 5,
    resolution: str = "720p",
    fps: int = 24,
    draft: bool = False,
    save_audio: bool = False,
    prompt_upsampling: bool = False,
    seed: int | None = None,
) -> Path:
    load_environment()
    ensure_run_dirs(run_id)
    output_path = run_dir(run_id) / "clips" / f"{_safe_key(clip_key)}.mp4"
    if _video_file_valid(output_path):
        return output_path

    if not os.getenv("REPLICATE_API_TOKEN"):
        raise RuntimeError("REPLICATE_API_TOKEN is required for p-video generation.")

    try:
        import replicate
    except ImportError as exc:
        raise RuntimeError("Install replicate first: python -m pip install -r requirements.txt") from exc

    duration = max(1, min(20, int(duration)))
    resolution = resolution if resolution in {"720p", "1080p"} else "720p"
    fps = 48 if int(fps) == 48 else 24

    request_input: dict[str, Any] = {
        "prompt": prompt,
        "duration": duration,
        "resolution": resolution,
        "fps": fps,
        "draft": bool(draft),
        "save_audio": bool(save_audio),
        "prompt_upsampling": bool(prompt_upsampling),
        "disable_safety_filter": False,
        "aspect_ratio": "16:9",
    }
    file_handle = None
    try:
        if image_path:
            path = Path(image_path)
            file_handle = path.open("rb")
            request_input["image"] = file_handle
        if seed is not None:
            request_input["seed"] = int(seed)

        output = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                output = replicate.run("prunaai/p-video", input=request_input)
                break
            except Exception as exc:
                last_error = exc
                if attempt >= 2:
                    raise
                wait_seconds = 6 * (attempt + 1)
                print(f"Replicate p-video failed on attempt {attempt + 1}/3: {exc}. Retrying in {wait_seconds}s...")
                time.sleep(wait_seconds)
        if output is None and last_error:
            raise last_error
        _save_replicate_output(output, output_path)
    finally:
        if file_handle:
            file_handle.close()
    return output_path


def estimate_pvideo_cost(*, seconds: int | float, resolution: str = "720p", draft: bool = False) -> float:
    rates = {
        ("720p", False): 0.02,
        ("720p", True): 0.005,
        ("1080p", False): 0.04,
        ("1080p", True): 0.01,
    }
    rate = rates.get((resolution, bool(draft)), 0.02)
    return round(max(0.0, float(seconds)) * rate, 4)


def _save_replicate_output(output: Any, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(output, "read"):
        output_path.write_bytes(output.read())
        return
    url = output.url() if hasattr(output, "url") else str(output)
    urllib.request.urlretrieve(url, output_path)


def _video_file_valid(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 1024


def _safe_key(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("_", "-") else "_" for char in str(value))
    safe = safe.strip("_")
    if safe:
        return safe[:80]
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
