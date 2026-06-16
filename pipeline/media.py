from __future__ import annotations

import shutil
import subprocess
import os
from pathlib import Path


def ensure_media_tools() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if _media_executable(tool) is None]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            f"Missing required media tool(s): {joined}. Install ffmpeg and make sure ffmpeg/ffprobe are on PATH."
        )


def run_ffmpeg(args: list[str]) -> None:
    ffmpeg = _media_executable("ffmpeg")
    if ffmpeg is None:
        ensure_media_tools()
        ffmpeg = "ffmpeg"
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else "no ffmpeg error output"
        raise RuntimeError(f"ffmpeg failed with exit code {exc.returncode}: {detail}") from exc


def ffprobe_duration(path: str | Path) -> float:
    ffprobe = _media_executable("ffprobe")
    if ffprobe is None:
        ensure_media_tools()
        ffprobe = "ffprobe"
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def create_silent_audio(path: str | Path, duration_seconds: float) -> Path:
    path = Path(path)
    codec_args = ["-c:a", "pcm_s16le"] if path.suffix.lower() == ".wav" else ["-c:a", "libmp3lame"]
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t",
            f"{duration_seconds:.3f}",
            *codec_args,
            str(path),
        ]
    )
    return path


def media_executable(tool: str) -> str | None:
    return _media_executable(tool)


def _media_executable(tool: str) -> str | None:
    found = shutil.which(tool)
    if found:
        return found

    local_app_data = os.getenv("LOCALAPPDATA")
    if not local_app_data:
        return None

    winget_packages = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if not winget_packages.exists():
        return None

    matches = sorted(winget_packages.glob(f"**/{tool}.exe"), key=lambda path: len(str(path)))
    if matches:
        return str(matches[0])
    return None
