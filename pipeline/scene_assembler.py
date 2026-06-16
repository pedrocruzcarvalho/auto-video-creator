from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .media import ffprobe_duration, run_ffmpeg
from .paths import ensure_run_dirs, run_dir


def assemble(
    *,
    image_path: Path | None = None,
    image_paths: list[Path] | None = None,
    shots: list[dict[str, Any]] | None = None,
    audio_path: Path,
    word_timestamps_path: Path | None = None,
    scene_id: int,
    run_id: str,
    config: dict[str, Any],
) -> Path:
    ensure_run_dirs(run_id)
    width = int(config["video"].get("width", 1280))
    height = int(config["video"].get("height", 720))
    fps = int(config["video"].get("fps", 30))
    padding = float(config["video"].get("scene_padding_seconds", 0.3))
    duration = ffprobe_duration(audio_path) + padding
    output_path = run_dir(run_id) / "clips" / f"scene_{scene_id:02d}.mp4"
    images = image_paths or ([image_path] if image_path else [])
    aligned_popups_enabled = bool(config.get("popups", {}).get("enabled", False))
    estimated_callouts_enabled = bool(config["video"].get("enable_callouts", False))
    fallback_to_estimated = bool(config.get("popups", {}).get("fallback_to_estimated_timing", False))
    enable_callouts = aligned_popups_enabled or estimated_callouts_enabled
    aligned_words = _load_word_timestamps(word_timestamps_path)
    if not images:
        raise ValueError(f"No images provided for scene {scene_id}")

    if len(images) == 1:
        _assemble_single_image(
            image_path=images[0],
            audio_path=audio_path,
            output_path=output_path,
            duration=duration,
            padding=padding,
            width=width,
            height=height,
            fps=fps,
        )
        return output_path

    segment_dir = run_dir(run_id) / "clips" / f"scene_{scene_id:02d}_segments"
    segment_dir.mkdir(parents=True, exist_ok=True)
    segment_paths = []
    shot_durations = _shot_durations(shots or [], len(images), duration)
    elapsed = 0.0
    for index, (path, shot_duration) in enumerate(zip(images, shot_durations), start=1):
        shot_start = elapsed
        shot_end = elapsed + shot_duration
        shot = shots[index - 1] if shots and index - 1 < len(shots) else {}
        overlays = shot.get("overlays") if isinstance(shot, dict) else None
        base_path = path
        if overlays:
            overlay_image = segment_dir / f"shot_{index:02d}_overlay.png"
            _add_overlays(path, overlay_image, overlays, width=width, height=height)
            base_path = overlay_image
        callout = shot.get("callout") if isinstance(shot, dict) else None
        if callout and enable_callouts:
            timing = None
            if aligned_popups_enabled and aligned_words:
                timing = _aligned_callout_timing(
                    callout=callout,
                    words=aligned_words,
                    shot_start=shot_start,
                    shot_end=shot_end,
                    shot_duration=shot_duration,
                    config=config,
                )
            if timing is None and (fallback_to_estimated or (estimated_callouts_enabled and not aligned_popups_enabled)):
                timing = _callout_timing(
                    shot_text=str(shot.get("text", "")),
                    callout_text=str(callout.get("text", "")),
                    shot_duration=shot_duration,
                )
            if timing is None:
                segment_path = segment_dir / f"shot_{index:02d}.mp4"
                _render_image_segment(base_path, segment_path, shot_duration, width=width, height=height, fps=fps)
                segment_paths.append(segment_path)
                elapsed = shot_end
                continue
            before_duration, callout_duration, after_duration = timing
            callout_segment = segment_dir / f"shot_{index:02d}_callout.mp4"
            callout_image = segment_dir / f"shot_{index:02d}_callout.png"
            _add_callout(base_path, callout_image, callout, width=width, height=height)
            if before_duration > 0.05:
                base_segment = segment_dir / f"shot_{index:02d}_before.mp4"
                _render_image_segment(base_path, base_segment, before_duration, width=width, height=height, fps=fps)
                segment_paths.append(base_segment)
            _render_image_segment(callout_image, callout_segment, callout_duration, width=width, height=height, fps=fps)
            segment_paths.append(callout_segment)
            if after_duration > 0.05:
                after_segment = segment_dir / f"shot_{index:02d}_after.mp4"
                _render_image_segment(base_path, after_segment, after_duration, width=width, height=height, fps=fps)
                segment_paths.append(after_segment)
        else:
            segment_path = segment_dir / f"shot_{index:02d}.mp4"
            _render_image_segment(base_path, segment_path, shot_duration, width=width, height=height, fps=fps)
            segment_paths.append(segment_path)
        elapsed = shot_end

    slideshow_path = segment_dir / "slideshow.mp4"
    _concat_video_segments(segment_paths, slideshow_path)
    run_ffmpeg(
        [
            "-i",
            str(slideshow_path),
            "-i",
            str(audio_path),
            "-t",
            f"{duration:.3f}",
            "-af",
            f"apad=pad_dur={padding:.3f}",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]
    )
    return output_path


def _assemble_single_image(
    *,
    image_path: Path,
    audio_path: Path,
    output_path: Path,
    duration: float,
    padding: float,
    width: int,
    height: int,
    fps: int,
) -> None:

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps},format=yuv420p"
    )
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(image_path),
            "-i",
            str(audio_path),
            "-t",
            f"{duration:.3f}",
            "-vf",
            vf,
            "-af",
            f"apad=pad_dur={padding:.3f}",
            "-c:v",
            "libx264",
            "-tune",
            "stillimage",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]
    )


def _render_image_segment(
    image_path: Path,
    output_path: Path,
    duration: float,
    *,
    width: int,
    height: int,
    fps: int,
) -> None:
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps},format=yuv420p"
    )
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(image_path),
            "-t",
            f"{duration:.3f}",
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-tune",
            "stillimage",
            str(output_path),
        ]
    )


def _concat_video_segments(segments: list[Path], output_path: Path) -> None:
    list_path = output_path.with_suffix(".txt")
    lines = [f"file '{segment.resolve().as_posix()}'" for segment in segments]
    list_path.write_text("\n".join(lines), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output_path)])


def _shot_durations(shots: list[dict[str, Any]], image_count: int, total_duration: float) -> list[float]:
    if image_count <= 0:
        return []
    weights = []
    for shot in shots[:image_count]:
        weights.append(max(1, len(str(shot.get("text", "")).split())))
    while len(weights) < image_count:
        weights.append(1)

    weight_total = sum(weights) or image_count
    durations = [max(0.5, total_duration * weight / weight_total) for weight in weights]
    drift = total_duration - sum(durations)
    durations[-1] += drift
    return durations


def _callout_timing(*, shot_text: str, callout_text: str, shot_duration: float) -> tuple[float, float, float]:
    words = _words(shot_text)
    callout_words = _words(callout_text)
    display_duration = min(max(0.9, 0.28 * max(1, len(callout_words)) + 0.7), max(0.9, shot_duration * 0.45))
    if not words or not callout_words:
        start = max(0.0, shot_duration * 0.5 - display_duration / 2)
    else:
        index = _find_phrase_index(words, callout_words)
        if index is None:
            start = max(0.0, shot_duration * 0.5 - display_duration / 2)
        else:
            start = (index / max(1, len(words))) * shot_duration
            start = max(0.0, start - 0.08)

    if start + display_duration > shot_duration:
        start = max(0.0, shot_duration - display_duration)
    before = start
    after = max(0.0, shot_duration - before - display_duration)
    return before, display_duration, after


def _words(text: str) -> list[str]:
    return [_normalize_word(word) for word in re.findall(r"[a-zA-Z0-9']+", text) if _normalize_word(word)]


def _find_phrase_index(words: list[str], phrase: list[str]) -> int | None:
    if not phrase:
        return None
    for index in range(0, len(words) - len(phrase) + 1):
        if words[index : index + len(phrase)] == phrase:
            return index
    return None


def _load_word_timestamps(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    words = payload.get("words", [])
    return words if isinstance(words, list) else []


def _aligned_callout_timing(
    *,
    callout: dict[str, Any],
    words: list[dict[str, Any]],
    shot_start: float,
    shot_end: float,
    shot_duration: float,
    config: dict[str, Any],
) -> tuple[float, float, float] | None:
    phrase_words = _words(str(callout.get("text", "")))
    audio_words = [_clean_word(str(item.get("word", ""))) for item in words]
    if not phrase_words or not audio_words:
        return None

    matches = []
    for index in range(0, len(audio_words) - len(phrase_words) + 1):
        if audio_words[index : index + len(phrase_words)] != phrase_words:
            continue
        try:
            start = float(words[index].get("start"))
            end = float(words[index + len(phrase_words) - 1].get("end"))
        except (TypeError, ValueError):
            continue
        matches.append((start, end))
    if not matches:
        return None

    max_drift = float(config.get("popups", {}).get("max_drift_seconds", 0.45))
    shot_center = (shot_start + shot_end) / 2
    nearby = [
        (start, end)
        for start, end in matches
        if start >= shot_start - max_drift and start <= shot_end + max_drift
    ]
    if not nearby:
        return None
    start, end = min(nearby, key=lambda match: abs(match[0] - shot_center))

    lead = float(config.get("popups", {}).get("lead_seconds", 0.04))
    display = max(float(config.get("popups", {}).get("display_seconds", 1.05)), (end - start) + 0.25)
    display = min(display, max(0.35, shot_duration))
    local_start = start - shot_start - lead
    if local_start < 0 and abs(local_start) <= max_drift:
        local_start = 0.0
    if local_start < 0 or local_start > shot_duration:
        return None
    if local_start + display > shot_duration:
        local_start = max(0.0, shot_duration - display)

    before = local_start
    after = max(0.0, shot_duration - before - display)
    return before, display, after


def _clean_word(word: str) -> str:
    cleaned = re.findall(r"[a-zA-Z0-9']+", word.lower())
    return _normalize_word(cleaned[0]) if cleaned else ""


def _normalize_word(word: str) -> str:
    cleaned = word.lower().strip("'")
    ordinal = re.fullmatch(r"(\d+)(?:st|nd|rd|th)", cleaned)
    if ordinal:
        return ordinal.group(1)
    return cleaned


def _add_callout(source_path: Path, output_path: Path, callout: dict[str, Any], *, width: int, height: int) -> None:
    with Image.open(source_path).convert("RGB") as source:
        canvas = source.resize((width, height), Image.Resampling.LANCZOS)

    draw = ImageDraw.Draw(canvas)
    text = " ".join(str(callout.get("text", "")).upper().split())[:40]
    if not text:
        canvas.save(output_path)
        return

    font = _title_font(56)
    while font.size > 28 and draw.textbbox((0, 0), text, font=font)[2] > width * 0.55:
        font = _title_font(font.size - 4)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x, y = _callout_position(str(callout.get("position", "right")), width, height, text_w, text_h)
    fill = _callout_color(str(callout.get("color", "red")))
    outline = "#ffe600" if fill == "#000000" else "#000000"

    for dx in range(-4, 5):
        for dy in range(-4, 5):
            if dx or dy:
                draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def _add_overlays(source_path: Path, output_path: Path, overlays: list[dict[str, Any]], *, width: int, height: int) -> None:
    with Image.open(source_path).convert("RGB") as source:
        canvas = source.resize((width, height), Image.Resampling.LANCZOS)

    draw = ImageDraw.Draw(canvas)
    for overlay in overlays[:4]:
        if overlay.get("type") == "text":
            _draw_text_overlay(draw, overlay, width=width, height=height)
        elif overlay.get("type") == "symbol":
            _draw_symbol_overlay(draw, overlay, width=width, height=height)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path)


def _draw_text_overlay(draw: ImageDraw.ImageDraw, overlay: dict[str, Any], *, width: int, height: int) -> None:
    text = " ".join(str(overlay.get("text", "")).split())[:60]
    if not text:
        return
    style = str(overlay.get("style", "normal"))
    size = 38 if style == "small" else 64 if style == "large" else 48
    font = _title_font(size)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x, y = _overlay_position(str(overlay.get("position", "center")), width, height, text_w, text_h)
    fill = _callout_color(str(overlay.get("color", "black")))
    outline = "black" if fill != "black" else "white"
    _outlined_text(draw, (x, y), text, font, fill, outline, thickness=3 if style == "outline" else 2)
    if style == "crossed_out":
        draw.line((x - 10, y + text_h // 2, x + text_w + 10, y + 8), fill="black", width=7)


def _draw_symbol_overlay(draw: ImageDraw.ImageDraw, overlay: dict[str, Any], *, width: int, height: int) -> None:
    symbol = str(overlay.get("symbol", "warning"))
    style = str(overlay.get("style", "normal"))
    size = 90 if style == "small" else 190 if style == "large" else 130
    x, y = _overlay_position(str(overlay.get("position", "center")), width, height, size, size)
    fill = _callout_color(str(overlay.get("color", "red")))

    if symbol == "x":
        draw.line((x, y, x + size, y + size), fill=fill, width=18)
        draw.line((x + size, y, x, y + size), fill=fill, width=18)
    elif symbol == "circle":
        draw.ellipse((x, y, x + size, y + size), outline=fill, width=12)
    elif symbol == "box":
        draw.rectangle((x, y, x + size, y + size), outline=fill, width=12)
    elif symbol == "arrow":
        draw.line((x, y + size // 2, x + size, y + size // 2), fill=fill, width=14)
        draw.polygon(
            [(x + size, y + size // 2), (x + size - 34, y + size // 2 - 28), (x + size - 34, y + size // 2 + 28)],
            fill=fill,
        )
    elif symbol == "nuclear":
        _draw_nuclear(draw, x, y, size)
    elif symbol.startswith("flag_"):
        _draw_flag(draw, symbol, x, y, size)
    elif symbol == "missile":
        _draw_missile(draw, x, y, size, fill)
    elif symbol == "explosion":
        _draw_explosion(draw, x, y, size, fill)
    else:
        _draw_warning(draw, x, y, size, fill)


def _draw_warning(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, fill: str) -> None:
    draw.polygon([(x + size // 2, y), (x + size, y + size), (x, y + size)], fill=fill, outline="black")
    _outlined_text(draw, (x + size * 0.39, y + size * 0.2), "!", _title_font(int(size * 0.65)), "black", "white", thickness=1)


def _draw_nuclear(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    draw.ellipse((x, y, x + size, y + size), fill="white", outline="black", width=8)
    cx, cy = x + size / 2, y + size / 2
    r = size / 2 - 10
    for angle in (90, 210, 330):
        a1 = math.radians(angle - 32)
        a2 = math.radians(angle + 32)
        draw.polygon(
            [(cx, cy), (cx + r * math.cos(a1), cy - r * math.sin(a1)), (cx + r * math.cos(a2), cy - r * math.sin(a2))],
            fill="black",
        )
    draw.ellipse((cx - size * 0.08, cy - size * 0.08, cx + size * 0.08, cy + size * 0.08), fill="black")


def _draw_flag(draw: ImageDraw.ImageDraw, symbol: str, x: int, y: int, size: int) -> None:
    w = int(size * 1.45)
    h = int(size * 0.75)
    draw.rectangle((x, y, x + w, y + h), fill="white", outline="black", width=4)
    if symbol == "flag_canada":
        draw.rectangle((x, y, x + w * 0.25, y + h), fill="red")
        draw.rectangle((x + w * 0.75, y, x + w, y + h), fill="red")
        _outlined_text(draw, (x + w * 0.41, y + h * 0.08), "*", _title_font(int(h * 0.8)), "red", "red", thickness=1)
    elif symbol == "flag_india":
        draw.rectangle((x, y, x + w, y + h / 3), fill="#ff8c00")
        draw.rectangle((x, y + h * 2 / 3, x + w, y + h), fill="#138808")
    elif symbol == "flag_pakistan":
        draw.rectangle((x + w * 0.25, y, x + w, y + h), fill="#0b6b3a")
        _outlined_text(draw, (x + w * 0.44, y + h * 0.06), "C", _title_font(int(h * 0.72)), "white", "white", thickness=1)


def _draw_missile(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, fill: str) -> None:
    draw.polygon(
        [(x, y + size // 2), (x + size * 0.75, y + size * 0.25), (x + size, y + size // 2), (x + size * 0.75, y + size * 0.75)],
        fill="white",
        outline="black",
    )
    draw.line((x + size * 0.15, y + size * 0.5, x + size * 0.75, y + size * 0.5), fill=fill, width=8)


def _draw_explosion(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, fill: str) -> None:
    points = []
    cx, cy = x + size / 2, y + size / 2
    for i in range(16):
        radius = size / 2 if i % 2 == 0 else size / 4
        angle = math.radians(i * 22.5)
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(points, fill=fill, outline="black")


def _outlined_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
    outline: str,
    *,
    thickness: int,
) -> None:
    x, y = xy
    for dx in range(-thickness, thickness + 1):
        for dy in range(-thickness, thickness + 1):
            if dx or dy:
                draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def _callout_position(position: str, width: int, height: int, text_w: int, text_h: int) -> tuple[int, int]:
    margin_x = 80
    margin_y = 130
    match position:
        case "left":
            return margin_x, height // 2 - text_h // 2
        case "right":
            return width - margin_x - text_w, height // 2 - text_h // 2
        case "top":
            return width // 2 - text_w // 2, margin_y
        case "bottom":
            return width // 2 - text_w // 2, height - margin_y - text_h
        case _:
            return width // 2 - text_w // 2, height // 2 - text_h // 2


def _overlay_position(position: str, width: int, height: int, item_w: int, item_h: int) -> tuple[int, int]:
    margin_x = 70
    margin_y = 120
    return {
        "top_left": (margin_x, margin_y),
        "top": (width // 2 - item_w // 2, margin_y),
        "top_right": (width - margin_x - item_w, margin_y),
        "left": (margin_x, height // 2 - item_h // 2),
        "center": (width // 2 - item_w // 2, height // 2 - item_h // 2),
        "right": (width - margin_x - item_w, height // 2 - item_h // 2),
        "bottom_left": (margin_x, height - margin_y - item_h),
        "bottom": (width // 2 - item_w // 2, height - margin_y - item_h),
        "bottom_right": (width - margin_x - item_w, height - margin_y - item_h),
    }.get(position, (width // 2 - item_w // 2, height // 2 - item_h // 2))


def _callout_color(color: str) -> str:
    return {
        "red": "#ff1a1a",
        "yellow": "#ffe600",
        "blue": "#1485ff",
        "green": "#21a33a",
        "black": "#000000",
        "white": "#ffffff",
        "gray": "#777777",
        "orange": "#ff8c00",
    }.get(color.lower(), "#ff1a1a")


def _title_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/comicbd.ttf",
        "C:/Windows/Fonts/comic.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()
