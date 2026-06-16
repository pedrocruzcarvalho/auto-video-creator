from __future__ import annotations

from pathlib import Path
from typing import Any

from .media import ffprobe_duration, run_ffmpeg
from .paths import ensure_run_dirs, run_dir


def assemble(clips: list[Path], run_id: str, *, config: dict[str, Any]) -> Path:
    ensure_run_dirs(run_id)
    if not clips:
        raise ValueError("No clips were provided for final assembly")

    output_path = run_dir(run_id) / "final.mp4"
    crossfade = float(config["video"].get("crossfade_seconds", 0.5))
    if len(clips) == 1 or crossfade <= 0:
        _concat(clips, output_path)
        return output_path

    try:
        _xfade(clips, output_path, crossfade)
    except RuntimeError:
        # Fallback keeps the run usable if a platform ffmpeg build lacks xfade/acrossfade.
        _concat(clips, output_path)
    return output_path


def _xfade(clips: list[Path], output_path: Path, crossfade: float) -> None:
    args: list[str] = []
    for clip in clips:
        args.extend(["-i", str(clip)])

    filter_parts: list[str] = []
    durations = [ffprobe_duration(path) for path in clips]
    cumulative = durations[0]
    last_v = "0:v"
    last_a = "0:a"

    for index in range(1, len(clips)):
        offset = max(0.0, cumulative - crossfade)
        next_v = f"v{index}"
        next_a = f"a{index}"
        filter_parts.append(
            f"[{last_v}][{index}:v]xfade=transition=fade:duration={crossfade}:offset={offset:.3f},format=yuv420p[{next_v}]"
        )
        filter_parts.append(f"[{last_a}][{index}:a]acrossfade=d={crossfade}:c1=tri:c2=tri[{next_a}]")
        cumulative += durations[index] - crossfade
        last_v = next_v
        last_a = next_a

    run_ffmpeg(
        [
            *args,
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            f"[{last_v}]",
            "-map",
            f"[{last_a}]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_path),
        ]
    )


def _concat(clips: list[Path], output_path: Path) -> None:
    list_path = output_path.with_suffix(".concat.txt")
    lines = [f"file '{clip.resolve().as_posix()}'" for clip in clips]
    list_path.write_text("\n".join(lines), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output_path)])
