from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import replicate

from .config import load_environment
from .media import ensure_media_tools, ffprobe_duration, run_ffmpeg
from .paths import ROOT_DIR, run_dir
from .progress import ProgressCallback, emit


WORKERS = ["Setup", "Clip 1", "Bridge", "Clip 2", "Captions", "Review"]

DEFAULT_SCRIPT_PART_1 = (
    "Your car is dropping into a water tank. Do not open the door yet. "
    "Outside water pressure is locking it shut. First, unclip the seat belt before the cabin fills. "
    "Keep one hand on the handle and wait."
)

DEFAULT_SCRIPT_PART_2 = (
    "If you pull early, the door feels welded shut. Do not fight the window. "
    "Stay calm and watch the water line. When the cabin is almost full, the pressure becomes equal. "
    "Now the door opens, and you move toward the bright bubbles."
)

STYLE = (
    "Vertical 9:16 glossy viral 3D safety simulation explainer, clearly fictional CGI. "
    "Stylized game-engine CGI, same brown-haired soft-featured adult male training avatar, realistic hands, simple navy shirt, "
    "bright readable blue water-tank lighting, clean simulation feel, macro object close-ups, snap zooms, fast push-ins. "
    "Camera always points to the exact narrated object. Not live-action, not real accident footage, "
    "not children's cartoon, not Pixar. No on-screen text, no captions, no letters, no numbers, no icons, no symbols, "
    "no markings, no emblems, no logos, no UI, no signs, no watermark."
)

DEFAULT_CLIP_1_VISUAL = (
    f"{STYLE}\n\n"
    "Create part 1 of a continuous fictional car-in-water training simulation. "
    "Only water pressure sound effects, bubbles, glass creaks, seatbelt click, and low bass hits.\n\n"
    "0-2s: compact CGI car lowers into a bright blue training water tank; snap zoom to the brown-haired driver avatar inside, face clearly visible.\n"
    "2-5s: macro push-in to the door handle as his hand pulls; the door barely moves because outside water presses it shut.\n"
    "5-8s: camera pushes to the waterline rising past the seat and dashboard, bubbles moving fast.\n"
    "8-11s: whip pan to the red seatbelt buckle; his thumb clicks it open.\n"
    "11-15s: show the same brown-haired face again, then end on a clean close-up of his right hand gripping the door handle with water at chest level."
)

DEFAULT_CLIP_2_VISUAL = (
    f"{STYLE}\n\n"
    "Continue exactly from the input first frame. Same car, same water level, same brown-haired avatar, same navy shirt, same hand on the same door handle. "
    "Only water pressure sound effects, bubbles, glass creaks, soft door pop, and smooth underwater movement.\n\n"
    "0-3s: begin on the same hand gripping the handle; water rises inside the car until pressure inside and outside looks equal.\n"
    "3-6s: camera snap zooms to the door seal flexing; bubbles collect along the edge.\n"
    "6-10s: the training avatar presses the handle; the door opens smoothly with a controlled burst of bubbles.\n"
    "10-13s: camera follows his hands moving through the open door into the water tank.\n"
    "13-15s: he moves upward toward bright bubbles and blue light, one clear calm survival payoff."
)


@dataclass(frozen=True)
class SeedanceOptions:
    run_id: str
    script_part_1: str = DEFAULT_SCRIPT_PART_1
    script_part_2: str = DEFAULT_SCRIPT_PART_2
    clip_1_visual: str = DEFAULT_CLIP_1_VISUAL
    clip_2_visual: str = DEFAULT_CLIP_2_VISUAL
    resolution: str = "720p"
    seed: int = 42420
    add_captions: bool = True
    use_voice_reference: bool = True
    resume: bool = True


def estimate_seedance_cost(resolution: str = "720p") -> dict[str, float]:
    first_rate, continuation_rate = _seedance_rates(resolution)
    clip_seconds = 15
    return {
        "clip_1_usd": round(clip_seconds * first_rate, 2),
        "clip_2_usd": round(clip_seconds * continuation_rate, 2),
        "total_usd": round((clip_seconds * first_rate) + (clip_seconds * continuation_rate), 2),
    }


def run_seedance_native_pipeline(options: SeedanceOptions, progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
    load_environment()
    ensure_media_tools()
    if not os.getenv("REPLICATE_API_TOKEN"):
        raise RuntimeError("REPLICATE_API_TOKEN is required.")
    if options.add_captions and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for captions.")

    base = run_dir(options.run_id)
    base.mkdir(parents=True, exist_ok=True)
    report_path = base / "run_report.json"
    expected_final = base / ("final.mp4" if options.add_captions else "final_seedance_native.mp4")
    if options.resume and _file_valid(expected_final) and report_path.exists():
        emit(progress_callback, "Setup", "done", f"Reusing existing final: {expected_final}", progress=1, artifact_path=expected_final)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["reused_existing_final"] = True
        return report
    archived_final = _archive_existing_final(base, options.run_id) if not options.resume else None

    clips_dir = base / "clips"
    review_dir = base / "review_frames"
    clips_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)

    client = replicate.Client()
    emit(progress_callback, "Setup", "done", f"Run folder ready: {base}", progress=1, artifact_path=base)

    clip1 = clips_dir / "part_01.mp4"
    emit(progress_callback, "Clip 1", "running", "Generating first Seedance clip", progress=0.1)
    _run_seedance(
        client,
        prompt=_native_prompt(options.clip_1_visual, options.script_part_1),
        output_path=clip1,
        resolution=options.resolution,
        seed=options.seed,
        generate_audio=True,
        resume=options.resume,
    )
    emit(progress_callback, "Clip 1", "done", "First clip ready", progress=1, artifact_path=clip1)

    emit(progress_callback, "Bridge", "running", "Extracting last frame", progress=0.2)
    last_frame = base / "part_01_last_frame.jpg"
    if not options.resume or not _file_valid(last_frame):
        run_ffmpeg(["-sseof", "-0.08", "-i", str(clip1), "-frames:v", "1", "-q:v", "2", str(last_frame)])
    last_frame_url = _file_url(client.files.create(last_frame))
    reference_audio_urls: list[str] = []
    reference_video_urls: list[str] = []
    emit(progress_callback, "Bridge", "done", "Last-frame continuation ready", progress=1, artifact_path=last_frame)

    clip2 = clips_dir / "part_02.mp4"
    emit(progress_callback, "Clip 2", "running", "Generating continuation from last frame", progress=0.1)
    _run_seedance(
        client,
        prompt=_native_prompt(options.clip_2_visual, options.script_part_2),
        output_path=clip2,
        resolution=options.resolution,
        seed=options.seed + 1,
        generate_audio=True,
        first_frame_url=last_frame_url,
        reference_audio_urls=reference_audio_urls,
        reference_video_urls=reference_video_urls,
        resume=options.resume,
    )
    emit(progress_callback, "Clip 2", "done", "Continuation clip ready", progress=1, artifact_path=clip2)

    original_final = _concat_native(base, [clip1, clip2])
    word_timestamps_path = None
    final_path = original_final
    if options.add_captions:
        captioned_path = base / "final.mp4"
        words_path = base / "native_words.json"
        if options.resume and _file_valid(captioned_path) and _file_valid(words_path):
            final_path = captioned_path
            word_timestamps_path = words_path
            emit(progress_callback, "Captions", "done", "Reusing existing captions", progress=1, artifact_path=final_path)
        else:
            emit(progress_callback, "Captions", "running", "Transcribing native audio and burning subtitles", progress=0.1)
            final_path, word_timestamps_path = _caption_native_video(original_final, base)
            emit(progress_callback, "Captions", "done", "Captioned final ready", progress=1, artifact_path=final_path)
    else:
        emit(progress_callback, "Captions", "done", "Captions skipped", progress=1)

    emit(progress_callback, "Review", "running", "Extracting review frames and report", progress=0.2)
    frames = _extract_review_frames(final_path, review_dir)
    contact = _contact_sheet(frames, base / "contact_sheet.jpg")
    report = {
        "run_id": options.run_id,
        "model": "bytedance/seedance-2.0",
        "resolution": options.resolution,
        "final_path": str(final_path),
        "original_final_path": str(original_final),
        "duration_seconds": round(ffprobe_duration(final_path), 2),
        "clip_paths": [str(clip1), str(clip2)],
        "last_frame_path": str(last_frame),
        "voice_reference_used_for_clip2": bool(reference_audio_urls or reference_video_urls),
        "word_timestamps_path": str(word_timestamps_path) if word_timestamps_path else None,
        "transcript_path": str(base / "native_transcript.txt") if (base / "native_transcript.txt").exists() else None,
        "contact_sheet": str(contact),
        "review_frames": [str(path) for path in frames],
        "estimate": estimate_seedance_cost(options.resolution),
        "reused_existing_final": False,
        "archived_previous_final": str(archived_final) if archived_final else None,
        "script": f"{options.script_part_1} {options.script_part_2}",
    }
    (base / "run_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (base / "run_report.md").write_text(_markdown_report(report), encoding="utf-8")
    emit(progress_callback, "Review", "done", "Review package ready", progress=1, artifact_path=base / "run_report.md", preview_path=contact)
    return report


def _native_prompt(visual_prompt: str, script: str) -> str:
    return (
        f"{visual_prompt}\n\n"
        "Seedance does all audio for this clip: natural serious male narrator, synchronized water sound effects, no external subtitles. "
        "Use the exact same narrator voice, tone, pacing, and mic distance as the previous reference audio if provided. "
        f"Narrator says exactly: \"{script}\""
    )


def _run_seedance(
    client: replicate.Client,
    *,
    prompt: str,
    output_path: Path,
    resolution: str,
    seed: int,
    generate_audio: bool,
    resume: bool,
    first_frame_url: str | None = None,
    reference_audio_urls: list[str] | None = None,
    reference_video_urls: list[str] | None = None,
) -> str:
    if resume and _file_valid(output_path):
        return str(output_path)

    created_after = datetime.now(timezone.utc)
    request_input: dict[str, Any] = {
        "prompt": prompt,
        "duration": 15,
        "resolution": resolution,
        "aspect_ratio": "9:16",
        "generate_audio": bool(generate_audio),
        "seed": int(seed),
    }
    if first_frame_url:
        request_input["image"] = first_frame_url
    if first_frame_url and (reference_audio_urls or reference_video_urls):
        reference_audio_urls = []
        reference_video_urls = []
    if reference_audio_urls:
        request_input["reference_audios"] = reference_audio_urls
    if reference_video_urls:
        request_input["reference_videos"] = reference_video_urls

    try:
        prediction = client.predictions.create(model="bytedance/seedance-2.0", input=request_input)
    except Exception as exc:
        if "ReadTimeout" not in type(exc).__name__ and "timed out" not in str(exc).lower():
            raise
        prediction = _latest_seedance_prediction(client, created_after)
        if prediction is None:
            raise

    prediction = _poll(client, prediction.id)
    if prediction.status != "succeeded":
        raise RuntimeError(f"Seedance failed: {prediction.status}: {prediction.error}")
    output = prediction.output[0] if isinstance(prediction.output, list) else prediction.output
    url = output.url() if hasattr(output, "url") else str(output)
    urllib.request.urlretrieve(url, output_path)
    return url


def _latest_seedance_prediction(client: replicate.Client, created_after: datetime) -> Any | None:
    for prediction in client.predictions.list():
        if prediction.model != "bytedance/seedance-2.0":
            continue
        try:
            created = datetime.fromisoformat(str(prediction.created_at).replace("Z", "+00:00"))
        except ValueError:
            return prediction
        if created >= created_after:
            return prediction
    return None


def _poll(client: replicate.Client, prediction_id: str) -> Any:
    while True:
        prediction = client.predictions.get(prediction_id)
        if prediction.status in {"succeeded", "failed", "canceled"}:
            return prediction
        time.sleep(5)


def _concat_native(base: Path, clips: list[Path]) -> Path:
    concat_path = base / "clips.concat.txt"
    concat_path.write_text("".join(f"file '{clip.as_posix()}'\n" for clip in clips), encoding="utf-8")
    final = base / "final_seedance_native.mp4"
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat_path), "-c", "copy", str(final)])
    concat_path.unlink(missing_ok=True)
    return final


def _caption_native_video(source_path: Path, base: Path) -> tuple[Path, Path | None]:
    word_timestamps_path = _native_word_timestamps(source_path, base)
    subtitles = _write_native_subtitles(base / "subtitles.ass", word_timestamps_path)
    captioned = base / "final.mp4"
    run_ffmpeg(
        [
            "-i",
            str(source_path),
            "-vf",
            f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,ass={subtitles.relative_to(ROOT_DIR).as_posix()}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(captioned),
        ]
    )
    return captioned, word_timestamps_path


def _native_word_timestamps(source_path: Path, base: Path) -> Path | None:
    try:
        from openai import OpenAI
    except ImportError:
        return None

    audio = base / "native_audio.wav"
    words_path = base / "native_words.json"
    transcript_path = base / "native_transcript.txt"
    run_ffmpeg(["-i", str(source_path), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(audio)])
    client = OpenAI()
    with audio.open("rb") as file:
        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=file,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
    payload = transcription.model_dump() if hasattr(transcription, "model_dump") else dict(transcription)
    words = []
    for item in payload.get("words", []) or []:
        word = str(item.get("word", "")).strip()
        if not word:
            continue
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (TypeError, ValueError, KeyError):
            continue
        words.append({"word": word, "start": start, "end": end})
    text = str(payload.get("text") or "")
    transcript_path.write_text(_clean_caption_text(text), encoding="utf-8")
    words_path.write_text(json.dumps({"words": words, "text": text}, indent=2), encoding="utf-8")
    return words_path if words else None


def _write_native_subtitles(path: Path, word_timestamps_path: Path | None) -> Path:
    events = _native_subtitle_events(word_timestamps_path)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,74,&H00FFFFFF,&H00FFFFFF,&H00111111,&H99000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,145,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for start, end, text in events:
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{{\\an2\\fad(25,25)}}{_ass_escape(text)}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _native_subtitle_events(word_timestamps_path: Path | None) -> list[tuple[float, float, str]]:
    if not word_timestamps_path or not word_timestamps_path.exists():
        return []
    payload = json.loads(word_timestamps_path.read_text(encoding="utf-8"))
    timed_words = []
    for item in payload.get("words", []) or []:
        word = str(item.get("word", "")).strip(".,!?;:")
        if not word:
            continue
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (TypeError, ValueError, KeyError):
            continue
        timed_words.append({"word": word, "start": start, "end": end})

    clean_text = _clean_caption_text(str(payload.get("text") or " ".join(item["word"] for item in timed_words)))
    script_tokens = re.findall(r"[A-Za-z0-9']+|[,.!?;:]", clean_text)
    chunks: list[list[str]] = []
    current: list[str] = []
    dangling = {"a", "an", "and", "the", "to", "toward", "into", "of", "for", "with", "you", "your", "water", "almost"}
    for token in script_tokens:
        if re.fullmatch(r"[,.!?;:]", token):
            if current and (token != "," or len(current) >= 3):
                chunks.append(current)
                current = []
            continue
        current.append(token)
        if len(current) >= 5 and current[-1].lower() not in dangling:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)

    events: list[tuple[float, float, str]] = []
    word_index = 0
    for chunk in chunks:
        if word_index >= len(timed_words):
            break
        timed = timed_words[word_index : word_index + len(chunk)]
        if not timed:
            break
        events.append((float(timed[0]["start"]), float(timed[-1]["end"]) + 0.08, " ".join(chunk)))
        word_index += len(timed)
    return events


def _extract_review_frames(final_path: Path, review_dir: Path) -> list[Path]:
    review_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for second in (3, 10, 16, 24):
        out = review_dir / f"frame_{second:02d}s.jpg"
        run_ffmpeg(["-ss", str(second), "-i", str(final_path), "-frames:v", "1", "-q:v", "2", str(out)])
        frames.append(out)
    return frames


def _contact_sheet(frames: list[Path], output_path: Path) -> Path:
    from PIL import Image, ImageDraw

    thumb_w, thumb_h = 270, 480
    sheet = Image.new("RGB", (thumb_w * len(frames), thumb_h + 44), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 12), "Seedance native: 3s / 10s / 16s / 24s", fill=(0, 0, 0))
    for index, frame in enumerate(frames):
        img = Image.open(frame).convert("RGB")
        img.thumbnail((thumb_w, thumb_h))
        x = index * thumb_w + (thumb_w - img.width) // 2
        y = 44 + (thumb_h - img.height) // 2
        sheet.paste(img, (x, y))
    sheet.save(output_path, quality=92)
    return output_path


def _file_url(file_obj: Any) -> str:
    urls = getattr(file_obj, "urls", {}) or {}
    return urls.get("get") or urls.get("download") or urls.get("content") or str(file_obj)


def _ass_time(seconds: float) -> str:
    centiseconds = int(round(max(0.0, seconds) * 100))
    cs = centiseconds % 100
    total = centiseconds // 100
    sec = total % 60
    minutes = (total // 60) % 60
    hours = total // 3600
    return f"{hours}:{minutes:02d}:{sec:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return " ".join(text.replace("{", "").replace("}", "").split())


def _clean_caption_text(text: str) -> str:
    replacements = {
        "welded dut": "welded shut",
        "welded Dutch": "welded shut",
    }
    cleaned = text
    for old, new in replacements.items():
        cleaned = re.sub(re.escape(old), new, cleaned, flags=re.IGNORECASE)
    return cleaned


def _seedance_rates(resolution: str) -> tuple[float, float]:
    if resolution == "480p":
        return 0.08, 0.10
    if resolution == "1080p":
        return 0.45, 0.55
    return 0.18, 0.22


def _file_valid(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 1024


def _archive_existing_final(base: Path, run_id: str) -> Path | None:
    current = base / "final.mp4"
    if not _file_valid(current):
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archived = base / f"SAVED_{run_id}_{stamp}.mp4"
    shutil.copy2(current, archived)
    return archived


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        f"# {report['run_id']}",
        "",
        f"Model: `{report['model']}`",
        f"Resolution: {report['resolution']}",
        f"Duration: {report['duration_seconds']}s",
        f"Estimate: ${report['estimate']['total_usd']:.2f}",
        "",
        f"Final: `{report['final_path']}`",
        f"Original native video: `{report['original_final_path']}`",
        f"Contact sheet: `{report['contact_sheet']}`",
        f"Transcript: `{report.get('transcript_path') or ''}`",
        f"Archived previous final: `{report.get('archived_previous_final') or ''}`",
        "",
        "## Clips",
        "",
    ]
    for clip in report["clip_paths"]:
        lines.append(f"- `{clip}`")
    lines.extend(["", "## Script", "", report["script"]])
    return "\n".join(lines)
