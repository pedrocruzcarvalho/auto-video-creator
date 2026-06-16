from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .media import create_silent_audio, ffprobe_duration
from .paths import ensure_run_dirs, run_dir


@dataclass(frozen=True)
class AudioResult:
    path: Path
    duration_seconds: float
    word_timestamps_path: Path | None = None


def generate_all(
    scenes: list[dict[str, Any]],
    run_id: str,
    *,
    config: dict[str, Any],
    mock: bool = False,
) -> list[AudioResult]:
    ensure_run_dirs(run_id)
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(generate_audio, scene["text"], int(scene["id"]), run_id, config=config, mock=mock)
            for scene in scenes
        ]
    return [future.result() for future in futures]


def generate_audio(
    text: str,
    scene_id: int,
    run_id: str,
    *,
    config: dict[str, Any],
    mock: bool = False,
) -> AudioResult:
    output_format = str(config["tts"].get("output_format", "mp3")).strip(".").lower()
    output_path = run_dir(run_id) / "audio" / f"scene_{scene_id:02d}.{output_format}"

    if mock:
        words = max(1, len(text.split()))
        duration = max(3.0, words / 2.6)
        create_silent_audio(output_path, duration)
    elif os.getenv("OPENAI_API_KEY"):
        _generate_openai_audio(text, output_path, config=config)
    else:
        raise RuntimeError("OPENAI_API_KEY is required for a real run. Use --mock for offline testing.")

    duration = ffprobe_duration(output_path)
    word_timestamps_path = None
    if _popups_enabled(config) and not mock:
        word_timestamps_path = _generate_word_timestamps(output_path, scene_id, run_id, config=config)

    return AudioResult(path=output_path, duration_seconds=duration, word_timestamps_path=word_timestamps_path)


def _generate_openai_audio(text: str, output_path: Path, *, config: dict[str, Any]) -> None:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install openai first: python -m pip install -r requirements.txt") from exc

    client = OpenAI()
    request: dict[str, Any] = {
        "model": str(config["tts"].get("model", "gpt-4o-mini-tts")),
        "voice": str(config["tts"].get("voice", "marin")),
        "input": text,
        "response_format": str(config["tts"].get("output_format", "wav")),
    }
    instructions = str(config["tts"].get("instructions", "")).strip()
    if instructions:
        request["instructions"] = instructions
    response = client.audio.speech.create(**request)
    response.stream_to_file(output_path)


def _generate_word_timestamps(audio_path: Path, scene_id: int, run_id: str, *, config: dict[str, Any]) -> Path | None:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Install openai first: python -m pip install -r requirements.txt") from exc

    output_path = run_dir(run_id) / "audio" / f"scene_{scene_id:02d}_words.json"
    client = OpenAI()
    try:
        with audio_path.open("rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=audio_file,
                model=str(config.get("popups", {}).get("alignment_model", "whisper-1")),
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )
    except Exception as exc:  # pragma: no cover - external service behavior
        print(f"[{run_id}] Word alignment skipped for scene {scene_id}: {type(exc).__name__}: {exc}")
        return None

    payload = transcription.model_dump() if hasattr(transcription, "model_dump") else dict(transcription)
    words = []
    for item in payload.get("words", []) or []:
        word = str(item.get("word", "")).strip()
        if not word:
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        words.append({"word": word, "start": start, "end": end})

    output_path.write_text(json.dumps({"words": words}, indent=2), encoding="utf-8")
    return output_path if words else None


def _popups_enabled(config: dict[str, Any]) -> bool:
    value = config.get("popups", {}).get("enabled", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
