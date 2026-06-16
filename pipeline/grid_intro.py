from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .media import run_ffmpeg
from .paths import ensure_run_dirs, run_dir


def build(
    image_paths: list[Path],
    *,
    topic: str,
    run_id: str,
    config: dict[str, Any],
    topic_index: int = 0,
    labels: list[str] | None = None,
) -> Path:
    ensure_run_dirs(run_id)
    intro_config = config["intro"]
    video_config = config["video"]
    width = int(video_config.get("width", 1280))
    height = int(video_config.get("height", 720))
    fps = int(video_config.get("fps", 30))
    cols = int(intro_config.get("grid_columns", 4))
    rows = int(intro_config.get("grid_rows", 3))
    count = cols * rows
    labels = labels or []

    grid_path = run_dir(run_id) / "clips" / "intro_grid.png"
    output_path = run_dir(run_id) / "clips" / "intro.mp4"
    zoom_source_path = run_dir(run_id) / "clips" / "intro_grid_target.png"

    _make_grid(
        image_paths=_pad_to_count(image_paths, count),
        output_path=grid_path,
        topic=topic,
        width=width,
        height=height,
        cols=cols,
        rows=rows,
        highlight_index=None,
        show_highlight=False,
        labels=labels,
    )
    _make_grid(
        image_paths=_pad_to_count(image_paths, count),
        output_path=zoom_source_path,
        topic=topic,
        width=width,
        height=height,
        cols=cols,
        rows=rows,
        highlight_index=topic_index % count,
        show_highlight=bool(intro_config.get("highlight", False)),
        labels=labels,
    )

    pan_seconds = float(intro_config.get("pan_seconds", 6))
    zoom_seconds = float(intro_config.get("zoom_seconds", 1.2))
    hold_seconds = float(intro_config.get("hold_seconds", 1))
    target_index = topic_index % count
    target_col = target_index % cols
    target_row = target_index // cols
    cell_width = width / cols
    cell_height = height / rows
    target_cx = target_col * cell_width + cell_width / 2
    target_cy = target_row * cell_height + (cell_height * 0.38)
    zoom_frames = max(1, int(zoom_seconds * fps))

    zoom_expr = f"min(1+((on)/{zoom_frames})*3,4)"
    x_expr = f"max(0,min(iw-iw/zoom,{target_cx}-iw/(2*zoom)))"
    y_expr = f"max(0,min(ih-ih/zoom,{target_cy}-ih/(2*zoom)))"
    zoom_filter = f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d=1:s={width}x{height}:fps={fps},format=yuv420p"

    pan_clip = run_dir(run_id) / "clips" / "intro_pan.mp4"
    zoom_clip = run_dir(run_id) / "clips" / "intro_zoom.mp4"
    hold_clip = run_dir(run_id) / "clips" / "intro_hold.mp4"

    _render_still_video(grid_path, pan_clip, pan_seconds, fps=fps, width=width, height=height)
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-t",
            f"{zoom_seconds:.3f}",
            "-i",
            str(zoom_source_path),
            "-f",
            "lavfi",
            "-t",
            f"{zoom_seconds:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-vf",
            zoom_filter,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(zoom_clip),
        ]
    )

    clips = [pan_clip, zoom_clip]
    if hold_seconds > 0:
        _render_still_video(image_paths[target_index % len(image_paths)], hold_clip, hold_seconds, fps=fps, width=width, height=height)
        clips.append(hold_clip)

    _concat_without_crossfade(clips, output_path)
    return output_path


def _make_grid(
    *,
    image_paths: list[Path | None],
    output_path: Path,
    topic: str,
    width: int,
    height: int,
    cols: int,
    rows: int,
    highlight_index: int | None,
    show_highlight: bool,
    labels: list[str],
) -> None:
    grid = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(grid)
    label_font = _font(24)
    title_font = _font(30)
    cell_w = width // cols
    cell_h = height // rows
    thumb_h = int(cell_h * 0.75)

    for index, image_path in enumerate(image_paths[: cols * rows]):
        col = index % cols
        row = index // cols
        x = col * cell_w
        y = row * cell_h
        if image_path is None:
            draw.rectangle((x + 18, y + 18, x + cell_w - 18, y + thumb_h - 18), outline="black", width=3)
            _draw_centered_text(draw, "?", x + cell_w / 2, y + thumb_h / 2 - 18, _font(52))
        else:
            with Image.open(image_path).convert("RGB") as source:
                thumb = source.resize((cell_w, thumb_h), Image.Resampling.LANCZOS)
            grid.paste(thumb, (x, y))
        draw.rectangle((x, y, x + cell_w - 1, y + cell_h - 1), outline="black", width=2)
        if show_highlight and highlight_index == index:
            draw.rectangle((x + 5, y + 5, x + cell_w - 6, y + thumb_h - 6), outline="yellow", width=8)

        label = labels[index] if index < len(labels) else f"Box {index + 1}"
        if index == highlight_index and not label:
            label = topic
        _draw_centered_text(draw, label, x + cell_w / 2, y + thumb_h + 17, title_font if index == highlight_index else label_font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    grid.save(output_path)


def _render_still_video(image_path: Path, output_path: Path, duration: float, *, fps: int, width: int, height: int) -> None:
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-t",
            f"{duration:.3f}",
            "-i",
            str(image_path),
            "-f",
            "lavfi",
            "-t",
            f"{duration:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-vf",
            f"scale={width}:{height},format=yuv420p",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output_path),
        ]
    )


def _concat_without_crossfade(clips: list[Path], output_path: Path) -> None:
    list_path = output_path.with_suffix(".txt")
    lines = [f"file '{clip.resolve().as_posix()}'" for clip in clips]
    list_path.write_text("\n".join(lines), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output_path)])


def _pad_to_count(paths: list[Path], count: int) -> list[Path | None]:
    if not paths:
        raise ValueError("At least one image is required to build the intro")
    return [*paths[:count], *([None] * max(0, count - len(paths)))]


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _draw_centered_text(draw: ImageDraw.ImageDraw, text: str, center_x: float, y: float, font: ImageFont.ImageFont) -> None:
    clipped = text if len(text) <= 22 else f"{text[:19]}..."
    bbox = draw.textbbox((0, 0), clipped, font=font)
    draw.text((center_x - (bbox[2] - bbox[0]) / 2, y), clipped, fill="black", font=font)
