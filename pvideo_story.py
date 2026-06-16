from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from pipeline.asset_resolver import asset_path_for_ambience, asset_path_for_sfx, ensure_asset_dirs, resolve_archival_insert
from pipeline.config import load_config, load_environment
from pipeline.image_gen import generate_image
from pipeline.media import create_silent_audio, ffprobe_duration, media_executable, run_ffmpeg
from pipeline.paths import ensure_run_dirs, run_dir
from pipeline.tts import generate_audio
from pipeline.video_gen import estimate_pvideo_cost, generate_pvideo_clip


DEFAULT_TOPIC = "The Fake Town That Exposed a Map Thief"

BAD_OVERLAY_END_WORDS = {
    "A",
    "AN",
    "AND",
    "ARE",
    "AS",
    "AT",
    "BECAUSE",
    "BE",
    "BEFORE",
    "BUT",
    "BY",
    "FOR",
    "FROM",
    "HOW",
    "IN",
    "INTO",
    "IS",
    "OF",
    "ON",
    "OR",
    "OVER",
    "THAN",
    "THAT",
    "THE",
    "THIS",
    "TO",
    "UNDER",
    "WHAT",
    "WHEN",
    "WHERE",
    "WHO",
    "WHY",
    "WITH",
}

BAD_OVERLAY_START_WORDS = {
    "ABOUT",
    "ARE",
    "BE",
    "BEEN",
    "BECAUSE",
    "BEFORE",
    "DO",
    "DID",
    "DOES",
    "HAD",
    "HAS",
    "HAVE",
    "HOW",
    "IS",
    "THAT",
    "THIS",
    "WAS",
    "WERE",
    "WHAT",
    "WHEN",
    "WHERE",
    "WHO",
    "WHY",
}

DISALLOWED_OVERLAY_WORDS = {"RE"}

STYLE_BIBLE_PATH = Path("style_bibles") / "fern_blackfiles.md"

FONT_REGISTRY = {
    "impact": "Impact",
    "arial_black": "Arial Black",
    "bahnschrift_condensed": "Bahnschrift Condensed",
    "franklin_gothic_heavy": "Franklin Gothic Heavy",
    "georgia": "Georgia",
    "palatino_linotype": "Palatino Linotype",
    "book_antiqua": "Book Antiqua",
    "cambria": "Cambria",
    "times_new_roman": "Times New Roman",
    "trebuchet_ms": "Trebuchet MS",
}

LOCATION_FONT = "Georgia"
SERIF_TITLE_FONTS = {"Georgia", "Palatino Linotype", "Book Antiqua", "Cambria", "Times New Roman"}

SAFE_POSITIONS = {
    "center",
    "top",
    "bottom",
    "left",
    "right",
    "upper_left",
    "upper_right",
    "lower_left",
    "lower_right",
}

TRANSITION_TYPES = {"hard_cut", "dip_black", "flash_click", "glitch_cut", "crossfade"}
AMBIENT_BEDS = {"none", "low_drone", "room_tone", "industrial_hum", "surveillance_noise", "distant_wind", "courthouse_room"}
AUDIO_ANALYSIS_CACHE: dict[str, dict[str, float]] = {}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Fern-style p-video documentary test.")
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    parser.add_argument("--run-id", default="pvideo_eiffel_scam_1min")
    parser.add_argument("--duration", type=int, default=60, help="Target final duration in seconds.")
    parser.add_argument("--resolution", choices=["720p", "1080p"], default="720p")
    parser.add_argument("--fps", type=int, choices=[24, 48], default=24)
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--prompt-upsampling", dest="prompt_upsampling", action="store_true", default=True)
    parser.add_argument("--no-prompt-upsampling", dest="prompt_upsampling", action="store_false")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--tts-voice", default="onyx", help="OpenAI TTS voice. Default is a deeper male voice.")
    parser.add_argument("--image-first", dest="image_first", action="store_true", default=True)
    parser.add_argument("--text-to-video", dest="image_first", action="store_false", help="Skip keyframes and use text-to-video.")
    parser.add_argument("--image-model", default="google/nano-banana", help="Replicate still image model for keyframes.")
    parser.add_argument("--no-enhance", action="store_true", help="Skip final sharpen/contrast enhancement pass.")
    parser.add_argument("--mock-plan", action="store_true", help="Do not call Claude for the beat JSON.")
    parser.add_argument("--force-plan", action="store_true", help="Regenerate the Claude plan even if output/<run_id>/pvideo_plan.json exists.")
    parser.add_argument("--planner-model", default="", help="Optional Anthropic planning model override. Default is Claude Opus 4.7.")
    parser.add_argument("--no-tts", action="store_true", help="Skip narration and use silence.")
    parser.add_argument("--chapter-demo-title", default="", help="Render one local Blackfiles-style chapter card and exit. No Claude, Replicate, or TTS calls.")
    parser.add_argument("--chapter-demo-prefix", default="Chapter II:", help="Prefix for --chapter-demo-title.")
    parser.add_argument(
        "--download-assets",
        action="store_true",
        help="Download safe archival images from allowlisted sources, currently Wikimedia Commons.",
    )
    parser.add_argument("--yes", action="store_true", help="Skip the cost confirmation prompt.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    load_environment()
    args = parse_args(argv)
    ensure_run_dirs(args.run_id)
    ensure_asset_dirs()

    if str(args.chapter_demo_title).strip():
        output_path = run_dir(args.run_id) / "clips" / "chapter_demo_blackfiles_card.mp4"
        _make_blackfiles_chapter_card(
            output_path,
            title=str(args.chapter_demo_title).strip(),
            prefix=str(args.chapter_demo_prefix).strip() or "Chapter II:",
            duration=2.6,
            fps=args.fps,
        )
        print(f"Saved chapter demo: {output_path}")
        return

    plan = _load_or_create_plan(args)
    _resolve_plan_assets(plan, args)
    beats = plan["beats"]
    total_seconds = sum(int(beat["duration_seconds"]) for beat in beats)
    estimate = estimate_pvideo_cost(seconds=total_seconds, resolution=args.resolution, draft=args.draft)
    image_estimate = len(beats) * 0.039 if args.image_first else 0.0

    plan_path = run_dir(args.run_id) / "pvideo_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Topic: {args.topic}")
    print(f"Beats: {len(beats)}")
    print(f"Generated video seconds: {total_seconds}")
    print(f"Estimated p-video cost: ${estimate:.2f}")
    if args.image_first:
        print(f"Estimated keyframe image cost: ~${image_estimate:.2f}")
    print("Model audio: OFF. Narration/SFX are added separately.")
    print(f"Plan: {plan_path}")
    if not args.yes:
        answer = input("Generate now? Type YES to continue: ").strip()
        if answer != "YES":
            print("Cancelled.")
            return

    config = load_config()
    config.setdefault("popups", {})["enabled"] = True
    config.setdefault("popups", {})["fallback_to_estimated_timing"] = False
    config.setdefault("tts", {})["output_format"] = "wav"
    config["tts"]["voice"] = str(args.tts_voice)
    config["tts"]["instructions"] = (
        "Male investigative documentary narrator. Low, calm, serious, cinematic delivery. "
        "Sound like a premium mystery channel voiceover: controlled tension, natural pauses, "
        "not cheerful, not robotic, not announcer-like, not rushed."
    )
    image_config = load_config()
    image_config["image"]["model"] = str(args.image_model)
    image_config["image"]["style"] = "Photorealistic cinematic anonymous Fern-style doll documentary keyframe"
    image_config["image"]["add_title_banner"] = False
    image_config["image"]["add_intro_title_banner"] = False
    image_config["image"]["safety_rewrite"] = False
    image_config["image"]["request_spacing_seconds"] = 2

    beat_segments = []
    for index, beat in enumerate(beats, start=1):
        chapter_segment = _maybe_make_chapter_transition_segment(beat, args.run_id, index, fps=args.fps)
        if chapter_segment:
            beat_segments.append(chapter_segment)
            if _is_chapter_only_beat(beat):
                print(f"[{args.run_id}] Rendered chapter card {index}/{len(beats)}: {beat.get('chapter_title') or beat.get('title')}")
                continue

        beat_audio_path = run_dir(args.run_id) / "audio" / f"beat_{index:02d}.wav"
        if args.no_tts:
            create_silent_audio(beat_audio_path, int(beat["duration_seconds"]))
            beat_audio_duration = float(beat["duration_seconds"])
            word_timestamps_path = None
        else:
            print(f"[{args.run_id}] Generating narration for beat {index}/{len(beats)}: {beat['title']}")
            narration = str(beat.get("narration") or "").strip()
            if not narration:
                create_silent_audio(beat_audio_path, int(beat["duration_seconds"]))
                beat_audio_duration = float(beat["duration_seconds"])
                word_timestamps_path = None
            elif _audio_file_valid(beat_audio_path):
                print(f"[{args.run_id}] Reusing narration audio for beat {index}/{len(beats)}")
                beat_audio_duration = ffprobe_duration(beat_audio_path)
                word_timestamps_path = _existing_word_timestamps(args.run_id, index)
            else:
                audio_result = generate_audio(narration, index, args.run_id, config=config, mock=False)
                audio_result.path.replace(beat_audio_path)
                beat_audio_duration = audio_result.duration_seconds
                word_timestamps_path = audio_result.word_timestamps_path

        segment_seconds = max(1.0, beat_audio_duration + 0.18)
        clip_seconds = max(5, min(20, int(math.ceil(segment_seconds))))
        beat["duration_seconds"] = clip_seconds
        _align_beat_overlay_to_word_timestamps(beat, word_timestamps_path)

        archival = beat.get("archival_insert") if isinstance(beat.get("archival_insert"), dict) else None
        archival_asset = Path(str(archival.get("asset_path"))) if archival and archival.get("available") and archival.get("asset_path") else None
        keyframe_path = None
        if archival_asset and archival_asset.exists():
            print(f"[{args.run_id}] Preparing archival insert {index}/{len(beats)}: {archival_asset}")
        if args.image_first:
            print(f"[{args.run_id}] Generating keyframe {index}/{len(beats)}: {beat['title']}")
            keyframe_path = generate_image(
                _keyframe_prompt(str(beat["video_prompt"])),
                f"pvideo_keyframe_{index:02d}_glossy_white_head",
                args.run_id,
                config=image_config,
                mock=False,
                add_banner=False,
            )

        clip_key = f"pvideo_{index:02d}_raw_glossy_white_head_image_first" if args.image_first else f"pvideo_{index:02d}_raw_glossy_white_head_text"
        print(f"[{args.run_id}] Generating clip {index}/{len(beats)} ({clip_seconds}s): {beat['title']}")
        raw_path = generate_pvideo_clip(
            prompt=_no_text_video_prompt(str(beat["video_prompt"]), args.topic),
            run_id=args.run_id,
            clip_key=clip_key,
            image_path=keyframe_path,
            duration=clip_seconds,
            resolution=args.resolution,
            fps=args.fps,
            draft=bool(args.draft),
            save_audio=False,
            prompt_upsampling=bool(args.prompt_upsampling),
            seed=(args.seed + index if args.seed is not None else None),
        )
        styled_path = run_dir(args.run_id) / "clips" / f"pvideo_{index:02d}_styled_perbeat.mp4"
        _burn_overlays(raw_path, styled_path, beat, fps=args.fps)
        if archival_asset and archival_asset.exists():
            archival_styled_path = run_dir(args.run_id) / "clips" / f"pvideo_{index:02d}_styled_archival_insert.mp4"
            _apply_archival_insert(
                styled_path,
                archival_asset,
                archival_styled_path,
                start_seconds=_archival_start_seconds(archival, segment_seconds),
                duration_seconds=_archival_duration_seconds(archival, segment_seconds),
                fps=args.fps,
                motion=str(archival.get("motion") or "slow_zoom"),
            )
            styled_path = archival_styled_path

        sfx_path = run_dir(args.run_id) / "audio" / f"beat_{index:02d}_overlay_sfx.wav"
        _make_overlay_sfx(sfx_path, [beat], download_assets=bool(args.download_assets))
        ambient_path = run_dir(args.run_id) / "audio" / f"beat_{index:02d}_ambient.wav"
        _make_ambient_bed(
            ambient_path,
            str(beat.get("ambient_bed") or "none"),
            int(math.ceil(segment_seconds)),
            download_assets=bool(args.download_assets),
        )

        if args.no_enhance:
            mux_video_path = styled_path
        else:
            mux_video_path = run_dir(args.run_id) / "clips" / f"pvideo_{index:02d}_enhanced_perbeat.mp4"
            _enhance_video(styled_path, mux_video_path)

        beat_segment_path = run_dir(args.run_id) / "clips" / f"beat_{index:02d}_final.mp4"
        _mux_video_audio(
            mux_video_path,
            beat_audio_path,
            sfx_path,
            ambient_path,
            beat_segment_path,
            total_seconds=segment_seconds,
        )
        transitioned_segment_path = run_dir(args.run_id) / "clips" / f"beat_{index:02d}_transitioned.mp4"
        transition_out = str(beat.get("transition_out") or "hard_cut")
        _add_segment_transition_fades(
            beat_segment_path,
            transitioned_segment_path,
            fade_in=index > 1,
            fade_out=index < len(beats),
            transition=transition_out,
        )
        beat_segments.append(transitioned_segment_path)

    plan["beats"] = beats
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    final_path = run_dir(args.run_id) / "final.mp4"
    _concat_av_segments(beat_segments, final_path, fps=args.fps)

    prompt_path = run_dir(args.run_id) / "pvideo_prompts.txt"
    prompt_path.write_text(
        "\n\n".join(f"{i + 1:02d}. {_no_text_video_prompt(str(beat['video_prompt']), args.topic)}" for i, beat in enumerate(beats)),
        encoding="utf-8",
    )
    print(f"Saved: {final_path}")
    print(f"Beat audio: {run_dir(args.run_id) / 'audio'}")
    print(f"Prompts: {prompt_path}")


def _load_or_create_plan(args: argparse.Namespace) -> dict[str, Any]:
    plan_path = run_dir(args.run_id) / "pvideo_plan.json"
    if plan_path.exists() and not args.force_plan:
        return _normalize_plan(json.loads(plan_path.read_text(encoding="utf-8")), args)
    if args.mock_plan:
        return _normalize_plan(_mock_plan(args.topic, int(args.duration)), args)
    if not args.mock_plan and os.getenv("ANTHROPIC_API_KEY"):
        retry_note = ""
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                return _normalize_plan(_claude_plan(args, retry_note=retry_note), args)
            except RuntimeError as exc:
                last_exc = exc
                message = str(exc)
                if attempt == 0 and _should_retry_claude_plan(message):
                    print(f"Claude plan too short/invalid; retrying once with stricter duration instructions: {message}")
                    retry_note = (
                        "Your previous plan was rejected because it was too short or missed chapter requirements. "
                        f"For this {int(args.duration)} second request, write a real long-form plan that lands around "
                        f"{int(args.duration)} seconds after narration. Use enough beats and enough spoken narration. "
                        "Do not summarize. Do not compress chapters. Return only valid JSON."
                    )
                    continue
                raise RuntimeError(f"Claude planning failed; refusing to use generic fallback content: {type(exc).__name__}: {exc}") from exc
        try:
            raise last_exc or RuntimeError("unknown planning error")
        except Exception as exc:
            raise RuntimeError(f"Claude planning failed; refusing to use generic fallback content: {type(exc).__name__}: {exc}") from exc
    raise RuntimeError("ANTHROPIC_API_KEY is required for real planning. Use --mock-plan only for UI/plumbing tests.")


def _should_retry_claude_plan(message: str) -> bool:
    retry_markers = (
        "returned only",
        "wrote only",
        "without any chapter_card",
        "narration repeats",
        "too short",
    )
    lowered = message.lower()
    return any(marker in lowered for marker in retry_markers)


def _resolve_plan_assets(plan: dict[str, Any], args: argparse.Namespace) -> None:
    beats = plan.get("beats") if isinstance(plan.get("beats"), list) else []
    for index, beat in enumerate(beats, start=1):
        if not isinstance(beat, dict):
            continue
        archival = resolve_archival_insert(
            beat.get("archival_insert") if isinstance(beat.get("archival_insert"), dict) else None,
            run_id=args.run_id,
            beat_index=index,
            download=bool(args.download_assets),
        )
        beat["archival_insert"] = archival
        if archival and not archival.get("available"):
            _disable_archival_linked_sfx(beat)


def _disable_archival_linked_sfx(beat: dict[str, Any]) -> None:
    archival = beat.get("archival_insert") if isinstance(beat.get("archival_insert"), dict) else {}
    if archival:
        archival["sfx"] = "none"
    overlays = beat.get("overlays") if isinstance(beat.get("overlays"), list) else []
    for overlay in overlays:
        if isinstance(overlay, dict) and str(overlay.get("kind") or "").lower() in {"archival", "photo", "image"}:
            overlay["sfx"] = "none"
    overlay = beat.get("overlay")
    if isinstance(overlay, dict) and str(overlay.get("kind") or "").lower() in {"archival", "photo", "image"}:
        overlay["sfx"] = "none"


def _load_style_bible() -> str:
    try:
        return STYLE_BIBLE_PATH.read_text(encoding="utf-8")
    except OSError:
        return "Use cinematic investigative documentary style with short glowing overlays, dates, locations, and faceless glossy white doll characters."


def _maybe_make_chapter_transition_segment(beat: dict[str, Any], run_id_value: str, beat_index: int, *, fps: int) -> Path | None:
    chapter_overlay = None
    for overlay in _beat_overlays(beat):
        if str(overlay.get("kind") or "") == "chapter_card":
            chapter_overlay = overlay
            break
    chapter_title = str(beat.get("chapter_title") or "").strip()
    chapter_index = beat.get("chapter_index")
    if not chapter_overlay and not chapter_title:
        return None
    title = _clean_chapter_text(str(chapter_overlay.get("text") if chapter_overlay else chapter_title))
    if not title:
        return None
    prefix = _chapter_prefix(chapter_index)
    output_path = run_dir(run_id_value) / "clips" / f"chapter_{beat_index:02d}_blackfiles_card.mp4"
    _make_blackfiles_chapter_card(output_path, title=title, prefix=prefix, duration=2.6, fps=fps)
    return output_path


def _is_chapter_only_beat(beat: dict[str, Any]) -> bool:
    if str(beat.get("narration") or "").strip():
        return False
    if str(beat.get("chapter_title") or "").strip():
        return True
    return any(str(overlay.get("kind") or "") == "chapter_card" for overlay in _beat_overlays(beat))


def _chapter_prefix(value: Any) -> str:
    try:
        index = int(value)
    except (TypeError, ValueError):
        return "Chapter:"
    numerals = {
        1: "I",
        2: "II",
        3: "III",
        4: "IV",
        5: "V",
        6: "VI",
        7: "VII",
        8: "VIII",
        9: "IX",
        10: "X",
    }
    return f"Chapter {numerals.get(index, str(index))}:"


def _claude_plan(args: argparse.Namespace, *, retry_note: str = "") -> dict[str, Any]:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("Install anthropic first: python -m pip install -r requirements.txt") from exc

    client = anthropic.Anthropic()
    style_bible = _load_style_bible()
    planner_model = str(args.planner_model or os.getenv("ANTHROPIC_MODEL") or "claude-opus-4-7")
    target_beats = max(1, math.ceil(int(args.duration) / 20))
    target_words_low = int(int(args.duration) * 2.05)
    target_words_high = int(int(args.duration) * 2.45)
    request_kwargs = {
        "model": planner_model,
        "max_tokens": max(7000, min(24000, target_beats * 650)),
        "system": (
            "You are a meticulous Fern/Blackfiles-style documentary creative director and final-cut editor. "
            "You decide pacing, evidence inserts, motion, overlay restraint, dimming, SFX, ambience, and transitions. "
            "Return only valid JSON."
        ),
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Plan a {args.duration}-second investigative documentary video about: {args.topic!r}.\n"
                    f"{retry_note + chr(10) if retry_note else ''}"
                    f"Use this style bible:\n{style_bible}\n\n"
                    "Return JSON with this shape:\n"
                    '{"topic":"...","beats":[{"title":"...","chapter_index":null,"chapter_title":null,"duration_seconds":8,'
                    '"narration":"spoken narration for this beat",'
                    '"video_prompt":"visual prompt for one AI video clip",'
                    '"transition_out":"hard_cut|dip_black|flash_click|glitch_cut|crossfade",'
                    '"ambient_bed":"none|low_drone|room_tone|industrial_hum|surveillance_noise|distant_wind|courthouse_room|short custom ambience query",'
                    '"archival_insert":{"query":"real person/place/object image to source","reason":"why this helps","motion":"slow_zoom|parallax|handheld_drift|glitch_reveal","start_seconds":1.0,"duration_seconds":3.0,"sfx":"camera_click|glitch|none"} or null,'
                    '"overlays":[{"kind":"spoken|date|location|sequence|chapter_card","text":"short phrase from narration","start_seconds":1.0,"duration_seconds":1.2,'
                    '"position":"center|top|bottom|left|right|upper_left|upper_right|lower_left|lower_right",'
                    '"effect":"fade|pop|typewriter|glitch","style":"block|serif","font":"Impact|Arial Black|Bahnschrift Condensed|Franklin Gothic Heavy|Georgia|Palatino Linotype|Book Antiqua|Cambria|Times New Roman|Trebuchet MS",'
                    '"color":"yellow|green|red|white","size":"small|medium|large|huge","sfx":"camera_click|typewriter|glitch|boom|whoosh|short custom sfx query|none",'
                    '"opacity":0.85,"dim_background":true,"dim_opacity":0.42}]}]}\n'
                    "Rules:\n"
                    f"- Create enough beats to land around the target duration after TTS. For {args.duration} seconds, return about {target_beats} beats. A little under/over is fine, but do not compress a 4-minute story into a 2-minute intro.\n"
                    "- Each beat duration must be between 8 and 20 seconds. For long videos, prefer 16-20 second beats so the JSON stays compact.\n"
                    f"- The narration across all beats should fit the target duration at about 2.2 words/sec. For {args.duration} seconds, write roughly {target_words_low} to {target_words_high} spoken words total across all beats.\n"
                    "- When historically feasible, start the story with a clear date or year in the first sentence.\n"
                    "- Whenever the time period changes, explicitly say the new date, month, year, decade, or era in the narration so the viewer stays oriented.\n"
                    "- Date overlays are mandatory when dates are spoken. Dates are fixed top-left labels, no background dim, typewriter in, reverse typewriter out. Only one date label may be visible at a time. If saying 'January 2011', type JANUARY 2011 as one label when the date phrase begins. If saying 'Spring 1979', type SPRING 1979 as one label. Do not create separate month/year/season/year overlays.\n"
                    "- Location overlays are mandatory when locations are spoken. If saying 'Lisbon, Portugal', Lisbon must be one overlay, then Portugal must be a separate overlay when the narrator says Portugal. Locations must always be fixed bottom-right, small, serif, typewriter, with typewriter sfx.\n"
                    "- Include useful locations in narration when possible: city, country, border, prison, island, town, capital, or region.\n"
                    "- Use fully clothed Fern-style anonymous doll figures: glossy smooth white plastic full heads, completely blank oval faces, no facial features, no black eye holes, cinematic reenactments, dark documentary mood, realistic rooms, offices, hotels, streets.\n"
                    "- Every visible person must have explicit full wardrobe that fits the role: suits, coats, uniforms, prison clothing, workwear, gloves, boots, or optional role headwear such as police hats, helmets, peaked caps, or hoods when the scene calls for it.\n"
                    "- Shot-type budget for every 90 seconds: maximum 35% slow cinematic tableau, at least 25% action/process shots, at least 15% archival/evidence inserts for real topics, at least 10% object/detail/motion-graphic style shots. Climax beats must include visible motion/action.\n"
                    "- video_prompt must be hyper-specific: shot type, camera angle, camera movement, foreground action, background environment, wardrobe, props, lighting, lens/shot distance, mood, and what physically moves during the clip.\n"
                    "- Use action/process prompts p-video can execute: typing, plugging cables, walking into court, lawyers opening briefcases, hands removing a screw, police entering a hallway, a server rack blinking, a camera pushing past evidence, a figure running through rain.\n"
                    "- Never use the words mannequin, mask, masked, faceplate, eye holes, black eyes, mouth, nose, display figure, bare torso, nude body, underwear, skin-colored bodysuit, unclothed body, or anatomy-focused shot.\n"
                    "- The video_prompt must NEVER ask the model to render text, words, numbers, letters, signs, captions, subtitles, newspapers, readable documents, logos, screens with text, or title cards.\n"
                    "- Avoid text-bearing props entirely. Prefer closed folders, sealed envelopes, briefcases, plain walls, unmarked desks, curtains, lamps, hotel rooms, train platforms, and empty corridors.\n"
                    "- Never describe posters, notice boards, evidence boards, wall maps, blueprints, screens, newspapers, books, documents, forms, labels, receipts, passports, tickets, or diagrams.\n"
                    "- On-screen text belongs only in overlay.text, not video_prompt.\n"
                    "- Use very few overlays: usually 0 or 1 big punch overlay per beat, plus mandatory dates/locations. Do not make every line into text. Big punch overlays should emphasize main characters, decisive evidence, consequences, impossible turns, or repeated story phrases only.\n"
                    "- overlays text must be exact buzz words from the narration for that same beat, 1 to 3 words maximum, except chapter cards and repeated sequence clauses.\n"
                    "- Big punch overlays must be self-contained hooks, not sentence fragments. Good examples: THE FLAW, NO WITNESSES, 32 DOLLARS, THE LOOPHOLE. Bad examples: WAS ABOUT TO, SENT IT TO, THE MAN WHO.\n"
                    "- For every overlay, decide dim_background and dim_opacity. Darken only when it improves readability or drama. Location overlays must not dim the screen.\n"
                    "- overlays text must not end on connector/filler words like to, of, the, a, an, and, or, but, with, for, from, by, in, on, at, into, over, under, before, after, because, that, who, what, when, where, why, how.\n"
                    "- overlays text must not cross a comma, period, question mark, exclamation mark, semicolon, colon, or any natural pause in the narration.\n"
                    "- If narration has repeated punch phrases in a row, like 'no guards, no witnesses, no inmates', each phrase must be its own separate popup.\n"
                    "- Choose overlay positions, but all text must be fully visible inside safe margins.\n"
                    "- Choose fonts only from the font list in the JSON shape. For glowing serif names like 'George Hotz', prefer Georgia, Palatino Linotype, Book Antiqua, or Cambria.\n"
                    "- For videos shorter than 120 seconds, write only the intro setup. Do not add chapter cards.\n"
                    "- For videos of 180 seconds or longer, you MUST include at least one chapter transition after the intro. Add chapter_index and chapter_title on the first beat of each chapter. Also add a chapter_card overlay there. Chapter titles are not read by narrator.\n"
                    "- chapter_title and chapter_card overlay text must be the title only, without the word Chapter and without chapter numbers. Good: THE KEYS TO THE KINGDOM. Bad: CHAPTER 1 THE KEYS TO THE KINGDOM.\n"
                    "- Choose transition_out and ambient_bed for every beat. Prefer available ambience names, but you may request a short custom ambience query if it is essential.\n"
                    "- Prefer available SFX names. Only request custom SFX if a generic local sound cannot carry the beat.\n"
                    "- When a real person, place, object, official photo, or artifact would strengthen credibility, set archival_insert with a sourcing query and motion. Do not invent a fake photo.\n"
                    "- Archival images must come from safe sources like Wikimedia Commons, public domain archives, official pages, or user-provided files. For real places or real people, include at least one archival_insert in a 60-120 second intro when a safe source likely exists. For videos 180 seconds or longer about real events, include at least two archival_insert recommendations: one for the main person/place and one for evidence, institution, device, building, artifact, or historical context. Decide start_seconds and duration_seconds for each archival insert; usually 2.0 to 4.5 seconds, only while the narrator introduces the real person/object/place. If no image is available, the image-specific sfx must be none.\n"
                    "- End with a hook/payoff, not a generic summary.\n"
                    "- Think like a final-cut director: each beat needs a clear emotional job, visual job, sound job, and evidence job. Avoid generic repeated cinematic shots."
                    "\n- Keep JSON compact. Do not write more than 35 beats for an 11-12 minute video. Keep each video_prompt specific but under 55 words."
                ),
            }
        ],
    }
    if "opus-4-7" not in planner_model:
        request_kwargs["temperature"] = 0.7
    raw_text = _anthropic_message_text(client, request_kwargs).strip()
    _save_planner_json_text(args.run_id, "pvideo_plan_raw.txt", raw_text)
    try:
        return _loads_json_object(raw_text)
    except json.JSONDecodeError as exc:
        repaired = _repair_json_with_anthropic(raw_text, error=str(exc), client=client, model=planner_model)
        _save_planner_json_text(args.run_id, "pvideo_plan_repaired.txt", repaired)
        try:
            return _loads_json_object(repaired)
        except json.JSONDecodeError as repair_exc:
            concise = _retry_compact_json_plan(args, client=client, model=planner_model, error=str(repair_exc))
            _save_planner_json_text(args.run_id, "pvideo_plan_compact_retry.txt", concise)
            return _loads_json_object(concise)


def _repair_json_with_anthropic(raw_text: str, *, error: str, client: Any, model: str) -> str:
    request_kwargs = {
        "model": model,
        "max_tokens": max(2000, min(12000, len(raw_text) // 2 + 2000)),
        "system": (
            "You repair malformed JSON. Return only one valid JSON object. "
            "Do not summarize, do not omit fields, do not use markdown."
        ),
        "messages": [
            {
                "role": "user",
                "content": (
                    "Fix this malformed JSON so Python json.loads can parse it. "
                    "Keep the same data and shape. Use double quotes, escaped inner quotes, "
                    "no trailing commas, and JSON null/true/false. "
                    f"The parser error was: {error}\n\n"
                    f"{raw_text}"
                ),
            }
        ],
    }
    if "opus-4-7" not in model:
        request_kwargs["temperature"] = 0
    return _anthropic_message_text(client, request_kwargs).strip()


def _retry_compact_json_plan(args: argparse.Namespace, *, client: Any, model: str, error: str) -> str:
    target = int(args.duration)
    target_beats = max(1, math.ceil(target / 20))
    target_words_low = int(target * 1.9)
    target_words_high = int(target * 2.35)
    request_kwargs = {
        "model": model,
        "max_tokens": 24000,
        "system": (
            "You are a concise documentary planner. Return one complete valid JSON object only. "
            "No markdown. No comments. No trailing text."
        ),
        "messages": [
            {
                "role": "user",
                "content": (
                    f"The previous JSON plan failed to parse and was probably too long: {error}\n"
                    f"Create a compact {target}-second Fern/Blackfiles-style investigative documentary plan about: {args.topic!r}.\n"
                    "Return only this JSON shape:\n"
                    '{"topic":"...","beats":[{"title":"...","chapter_index":null,"chapter_title":null,'
                    '"duration_seconds":18,"narration":"...","video_prompt":"...",'
                    '"transition_out":"hard_cut|dip_black|flash_click|glitch_cut|crossfade",'
                    '"ambient_bed":"none|low_drone|room_tone|industrial_hum|surveillance_noise|distant_wind|courthouse_room",'
                    '"archival_insert":null,'
                    '"overlays":[{"kind":"spoken|date|location|sequence|chapter_card","text":"...","start_seconds":1.0,'
                    '"duration_seconds":1.2,"position":"center|upper_left|lower_right","effect":"pop|typewriter|glitch",'
                    '"style":"block|serif","font":"Impact|Georgia|Times New Roman","color":"yellow|green|red|white",'
                    '"size":"small|medium|large|huge","sfx":"camera_click|typewriter|glitch|none",'
                    '"opacity":1.0,"dim_background":true,"dim_opacity":0.38}]}]}\n'
                    f"Rules: use about {target_beats} beats, never more than {target_beats + 2}. "
                    "Beat durations must be 16-20 seconds except chapter-card beats may be 10-14 seconds. "
                    f"Total narration should be roughly {target_words_low}-{target_words_high} words. "
                    "Use chapters after the intro. Chapter titles are not spoken and chapter_card text is title only. "
                    "Use very few overlays: one punch overlay max per beat plus dates/locations. "
                    "Overlay text must be complete phrases, never fragments ending in TO, OF, THE, A, SINGLE, or possessive words. "
                    "Use fully clothed glossy blank white doll figures in cinematic reenactments. "
                    "video_prompt must be specific but under 55 words and must not request rendered text/signs/documents/screens. "
                    "For real people/places, include 2-4 archival_insert objects with query, reason, motion, start_seconds, duration_seconds, sfx. "
                    "Return complete parseable JSON."
                ),
            }
        ],
    }
    if "opus-4-7" not in model:
        request_kwargs["temperature"] = 0.2
    return _anthropic_message_text(client, request_kwargs).strip()


def _anthropic_message_text(client: Any, request_kwargs: dict[str, Any]) -> str:
    should_stream = int(request_kwargs.get("max_tokens") or 0) >= 18000
    if not should_stream:
        try:
            response = client.messages.create(**request_kwargs)
            return response.content[0].text
        except ValueError as exc:
            if "Streaming is required" not in str(exc):
                raise

    chunks: list[str] = []
    with client.messages.stream(**request_kwargs) as stream:
        for text in stream.text_stream:
            chunks.append(text)
    return "".join(chunks)


def _save_planner_json_text(run_id_value: str, filename: str, text: str) -> None:
    try:
        ensure_run_dirs(run_id_value)
        (run_dir(run_id_value) / filename).write_text(text, encoding="utf-8")
    except OSError:
        pass


def _audio_file_valid(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 1024:
        return False
    try:
        return ffprobe_duration(path) > 0.2
    except Exception:
        return False


def _existing_word_timestamps(run_id_value: str, beat_index: int) -> Path | None:
    audio_dir = run_dir(run_id_value) / "audio"
    candidates = [
        audio_dir / f"beat_{beat_index:02d}_words.json",
        audio_dir / f"scene_{beat_index:02d}_words.json",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 2:
            return candidate
    return None


def _normalize_plan(plan: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    raw_beats = plan.get("beats") if isinstance(plan.get("beats"), list) else []
    if not raw_beats:
        if args.mock_plan:
            raw_beats = _mock_plan(args.topic, int(args.duration))["beats"]
        else:
            raise RuntimeError("Plan has no beats; refusing to generate paid media from empty/generic content.")

    target = int(args.duration)
    beats = []
    for index, raw in enumerate(raw_beats, start=1):
        if not isinstance(raw, dict):
            continue
        duration = max(5, min(20, int(float(raw.get("duration_seconds") or 8))))
        overlay = raw.get("overlay") if isinstance(raw.get("overlay"), dict) else {}
        raw_overlays = raw.get("overlays") if isinstance(raw.get("overlays"), list) else []
        narration = str(raw.get("narration") or "").strip()
        text, spoken_start = _overlay_phrase_from_narration(narration, "", duration)
        start = max(0.0, min(spoken_start, duration - 0.5))
        overlay_duration = max(0.6, min(float(overlay.get("duration_seconds") or 1.8), duration - start))
        primary_kind = str(overlay.get("kind") or "")
        primary_overlay = {
            "text": text,
            "start_seconds": round(start, 2),
            "duration_seconds": round(overlay_duration, 2),
            "position": str(overlay.get("position") or "center").strip().lower(),
            "effect": _overlay_effect(str(overlay.get("effect") or "fade")),
            "style": _overlay_style(str(overlay.get("style") or "")),
            "font": _overlay_font(str(overlay.get("font") or "")),
            "color": _overlay_color_name(str(overlay.get("color") or "")),
            "size": _overlay_size_name(str(overlay.get("size") or "")),
            "sfx": _overlay_sfx(str(overlay.get("sfx") or "")),
            "opacity": _overlay_opacity(overlay.get("opacity")),
            "dim_background": _overlay_dim_background(overlay.get("dim_background"), primary_kind),
            "dim_opacity": _overlay_dim_opacity(overlay.get("dim_opacity")),
        }
        directed_overlays = [_normalize_directed_overlay(item, duration) for item in raw_overlays if isinstance(item, dict)]
        directed_overlays = [item for item in directed_overlays if item]
        overlays = _auto_overlays(narration, duration, primary_overlay, directed_overlays=directed_overlays)
        chapter_title = _clean_chapter_text(str(raw.get("chapter_title") or "")) or None
        beats.append(
            {
                "title": str(raw.get("title") or f"Beat {index}").strip()[:80],
                "chapter_index": raw.get("chapter_index") if raw.get("chapter_index") is not None else None,
                "chapter_title": chapter_title,
                "duration_seconds": duration,
                "narration": narration,
                "video_prompt": str(raw.get("video_prompt") or raw.get("visual") or "").strip(),
                "overlay": overlays[0] if overlays else primary_overlay,
                "overlays": overlays,
                "transition_out": _transition_type(str(raw.get("transition_out") or "")),
                "ambient_bed": _ambient_bed(str(raw.get("ambient_bed") or "")),
                "archival_insert": raw.get("archival_insert") if isinstance(raw.get("archival_insert"), dict) else None,
            }
        )

    beats = _sanitize_chapter_beats(beats)
    _ensure_archival_recommendations(beats, args.topic, target)
    if _has_repeated_narration(beats):
        if args.mock_plan:
            print("Mock plan narration repeated; using refreshed mock narration.")
            repaired = _mock_plan(args.topic, int(args.duration))
            return _normalize_plan(repaired, args)
        raise RuntimeError("Plan narration repeats across beats; refusing to generate. Rerun with --force-plan to get a fresh Claude plan.")
    minimum_beats = max(1, math.ceil((target * 0.85) / 20))
    if len(beats) < minimum_beats and not args.mock_plan:
        raise RuntimeError(
            f"Claude returned only {len(beats)} beats for an around-{target}s video. Need at least {minimum_beats} beats with the 20s generation cap. "
            "Rerun with --force-plan; no paid media was generated."
        )
    if target >= 180 and not any(str(overlay.get("kind") or "") == "chapter_card" for beat in beats for overlay in _beat_overlays(beat)) and not args.mock_plan:
        raise RuntimeError("Claude returned a long-form plan without any chapter_card overlay. Rerun with --force-plan; no paid media was generated.")

    beats = _fit_duration(beats, target)
    if not args.mock_plan:
        _validate_narration_length(beats, target)
    _vary_overlay_styles(beats)
    return {"topic": str(plan.get("topic") or args.topic), "target_seconds": target, "beats": beats}


def _validate_narration_length(beats: list[dict[str, Any]], target: int) -> None:
    if target < 180:
        return
    words = [
        word
        for beat in beats
        for word in re.findall(r"[A-Za-z0-9']+", str(beat.get("narration") or ""))
    ]
    word_count = len(words)
    min_words = int(target * 1.75)
    max_words = int(target * 2.85)
    if word_count < min_words:
        raise RuntimeError(
            f"Claude wrote only {word_count} narration words for an around-{target}s video. Need at least {min_words}. "
            "Rerun with --force-plan; no paid media was generated."
        )
    if word_count > max_words:
        raise RuntimeError(
            f"Claude wrote {word_count} narration words for {target}s, likely too long. Keep it under {max_words}. "
            "Rerun with --force-plan; no paid media was generated."
        )


def _sanitize_chapter_beats(beats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chapter_number = 0
    for beat in beats:
        overlays = [dict(overlay) for overlay in beat.get("overlays", []) if isinstance(overlay, dict)]
        card_overlay = next((overlay for overlay in overlays if str(overlay.get("kind") or "") == "chapter_card"), None)
        declared_title = _clean_chapter_text(str(beat.get("chapter_title") or ""))
        if not card_overlay and not declared_title:
            continue

        chapter_number += 1
        title = declared_title or _clean_chapter_text(str(card_overlay.get("text") if card_overlay else ""))
        if not title:
            title = _clean_chapter_text(str(beat.get("title") or "")) or f"PART {chapter_number}"

        beat["chapter_index"] = chapter_number
        beat["chapter_title"] = title

        cleaned_overlays: list[dict[str, Any]] = []
        wrote_card = False
        for overlay in overlays:
            kind = str(overlay.get("kind") or "")
            if kind == "chapter_card":
                if not wrote_card:
                    overlay["text"] = title
                    overlay["start_seconds"] = 0.0
                    overlay["duration_seconds"] = max(1.8, float(overlay.get("duration_seconds") or 2.4))
                    cleaned_overlays.append(overlay)
                    wrote_card = True
                continue
            if _is_duplicate_chapter_overlay(overlay, title):
                continue
            cleaned_overlays.append(overlay)

        if not wrote_card:
            cleaned_overlays.insert(
                0,
                {
                    "kind": "chapter_card",
                    "text": title,
                    "start_seconds": 0.0,
                    "duration_seconds": 2.4,
                    "position": "center",
                    "effect": "glitch",
                    "style": "serif",
                    "font": "Georgia",
                    "color": "green",
                    "size": "huge",
                    "sfx": "glitch",
                    "opacity": 1.0,
                    "dim_background": True,
                    "dim_opacity": 0.0,
                },
            )

        beat["overlays"] = _limit_overlays(cleaned_overlays)
        visible = _visible_beat_overlays(beat)
        beat["overlay"] = visible[0] if visible else (beat["overlays"][0] if beat["overlays"] else {})
    return beats


def _ensure_archival_recommendations(beats: list[dict[str, Any]], topic: str, target: int) -> None:
    if not beats or any(isinstance(beat.get("archival_insert"), dict) for beat in beats):
        return
    if target < 120:
        return

    queries = _archival_queries_from_topic(topic)
    if not queries:
        return

    candidate_indexes = [0]
    if target >= 180 and len(beats) >= 4 and len(queries) > 1:
        candidate_indexes.append(min(len(beats) - 1, max(1, len(beats) // 2)))

    for beat_index, query in zip(candidate_indexes, queries):
        beats[beat_index]["archival_insert"] = {
            "query": query,
            "reason": "safe archival credibility insert for the real subject",
            "motion": "slow_zoom",
            "sfx": "camera_click",
        }


def _archival_queries_from_topic(topic: str) -> list[str]:
    cleaned = re.sub(r"[^A-Za-z0-9 .,:;'-]+", " ", str(topic or ""))
    chunks = re.split(r"\bvs\b|:|,|;|\band\b|\bwho\b|\bthat\b|\bwith\b", cleaned, flags=re.IGNORECASE)
    stop = {
        "The",
        "A",
        "An",
        "Sony",
        "PlayStation",
        "Court",
        "Gaming",
        "History",
        "Las",
        "Vegas",
        "North",
        "Korea",
    }
    queries: list[str] = []
    for chunk in chunks:
        matches = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b", chunk)
        for match in matches:
            words = match.split()
            if all(word in stop for word in words):
                continue
            if match not in queries:
                queries.append(match)
    title_case = cleaned.strip()
    if title_case and len(queries) < 2:
        queries.append(title_case[:90])
    return [f"{query} portrait" if len(query.split()) <= 3 else query for query in queries[:2]]


def _is_duplicate_chapter_overlay(overlay: dict[str, Any], title: str) -> bool:
    text = str(overlay.get("text") or "")
    if re.search(r"\bchapter\b", text, flags=re.IGNORECASE):
        return True
    overlay_key = _chapter_compare_key(text)
    title_key = _chapter_compare_key(title)
    return bool(overlay_key and title_key and (overlay_key == title_key or overlay_key in title_key or title_key in overlay_key))


def _chapter_compare_key(text: str) -> str:
    stripped = _strip_chapter_prefix(text)
    words = re.findall(r"[A-Za-z0-9']+", stripped.upper())
    return " ".join(word for word in words if word not in {"CHAPTER", "PART", "ACT"})


def _has_repeated_narration(beats: list[dict[str, Any]]) -> bool:
    if len(beats) < 3:
        return False
    openings: list[str] = []
    full_texts: list[str] = []
    for beat in beats:
        narration = " ".join(str(beat.get("narration") or "").lower().split())
        words = re.findall(r"[a-z0-9']+", narration)
        if len(words) < 6:
            continue
        openings.append(" ".join(words[:12]))
        full_texts.append(" ".join(words))
    if len(openings) < 3:
        return False
    most_common_opening = max(openings.count(opening) for opening in set(openings))
    unique_full_texts = len(set(full_texts))
    return most_common_opening >= max(3, math.ceil(len(openings) * 0.45)) or unique_full_texts <= max(1, len(full_texts) // 3)


def _fit_duration(beats: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
    if not beats:
        return beats
    while sum(beat["duration_seconds"] for beat in beats) < target - 2:
        candidate = min(beats, key=lambda beat: beat["duration_seconds"])
        if candidate["duration_seconds"] < 20:
            candidate["duration_seconds"] += 1
        else:
            break
    while sum(beat["duration_seconds"] for beat in beats) > target + 2 and len(beats) > 1:
        candidate = max(beats, key=lambda beat: beat["duration_seconds"])
        if candidate["duration_seconds"] > 5:
            candidate["duration_seconds"] -= 1
        else:
            beats.pop()
    return beats


def _normalize_directed_overlay(raw: dict[str, Any], duration: int) -> dict[str, Any] | None:
    kind = str(raw.get("kind") or "directed").strip().lower()
    text = _clean_overlay_text_for_kind(str(raw.get("text") or ""), kind)
    if not text:
        return None
    if kind == "sequence" and not text.upper().startswith("NO "):
        return None
    start = max(0.0, min(float(raw.get("start_seconds") or 0.8), max(0.0, duration - 0.3)))
    overlay_duration = max(0.35, min(float(raw.get("duration_seconds") or 1.2), duration - start))
    return {
        "text": text,
        "start_seconds": round(start, 2),
        "duration_seconds": round(overlay_duration, 2),
        "position": _overlay_position(str(raw.get("position") or "center")),
        "effect": _overlay_effect(str(raw.get("effect") or "pop")),
        "style": _overlay_style(str(raw.get("style") or "")),
        "font": _overlay_font(str(raw.get("font") or "")),
        "color": _overlay_color_name(str(raw.get("color") or "")),
        "size": _overlay_size_name(str(raw.get("size") or "")),
        "sfx": _overlay_sfx(str(raw.get("sfx") or "")),
        "opacity": _overlay_opacity(raw.get("opacity")),
        "dim_background": _overlay_dim_background(raw.get("dim_background"), kind),
        "dim_opacity": _overlay_dim_opacity(raw.get("dim_opacity")),
        "kind": kind,
    }


def _auto_overlays(
    narration: str,
    duration: int,
    primary_overlay: dict[str, Any],
    *,
    directed_overlays: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    overlays = []
    used_texts: set[str] = set()
    for overlay in directed_overlays or []:
        text = _clean_overlay_text_for_kind(str(overlay.get("text") or ""), str(overlay.get("kind") or ""))
        if text and text not in used_texts:
            used_texts.add(text)
            overlays.append(overlay)
    for overlay in _date_overlays(narration, duration):
        text = _clean_overlay_text_for_kind(str(overlay.get("text") or ""), "date")
        if text and text not in used_texts:
            used_texts.add(text)
            overlays.append(overlay)
    for overlay in _location_overlays(narration, duration):
        text = _clean_overlay_text_for_kind(str(overlay.get("text") or ""), "location")
        if text and text not in used_texts:
            used_texts.add(text)
            overlays.append(overlay)
    for overlay in _repeated_phrase_overlays(narration, duration):
        text = _clean_overlay_text_for_kind(str(overlay.get("text") or ""), "sequence")
        if text and text not in used_texts:
            used_texts.add(text)
            overlays.append(overlay)
    primary_text = _clean_overlay_text(str(primary_overlay.get("text") or ""))
    primary_overlaps_date = bool(re.search(r"\d", primary_text) and any(str(item.get("kind") or "") == "date" for item in overlays))
    if primary_text and primary_text not in used_texts and not primary_overlaps_date:
        primary_overlay["text"] = primary_text
        overlays.append(primary_overlay)
    overlays.sort(key=lambda item: float(item.get("start_seconds") or 0.0))
    return _limit_overlays(overlays)


def _limit_overlays(overlays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    locations = [_force_location_overlay_style(item) for item in overlays if str(item.get("kind") or "") == "location"]
    dates = _dedupe_date_overlays([_force_date_overlay_style(item) for item in overlays if str(item.get("kind") or "") == "date"])[:3]
    sequence = [item for item in overlays if str(item.get("kind") or "") == "sequence"][:2]
    chapter_cards = [item for item in overlays if str(item.get("kind") or "") == "chapter_card"][:1]
    spoken = [item for item in overlays if str(item.get("kind") or "") in {"spoken", "directed"}]
    other = [item for item in overlays if str(item.get("kind") or "") not in {"location", "date", "sequence", "chapter_card", "spoken", "directed"}]
    punch = []
    if chapter_cards:
        punch.extend(chapter_cards)
    elif sequence:
        punch.extend(sequence)
    elif spoken:
        punch.append(spoken[0])
    elif other:
        punch.append(other[0])
    limited = [*dates, *locations[:2], *(sequence[:2] if sequence else punch[:1])]
    limited.sort(key=lambda item: float(item.get("start_seconds") or 0.0))
    return limited[:5]


def _dedupe_date_overlays(dates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned_dates = []
    for date in dates:
        text = _clean_overlay_text_for_kind(str(date.get("text") or ""), "date")
        if not text:
            continue
        date = dict(date)
        date["text"] = text
        cleaned_dates.append(date)

    to_remove: set[int] = set()
    token_sets = [
        set(_normalize_word(word) for word in re.findall(r"[A-Za-z0-9']+", str(date.get("text") or "")) if _normalize_word(word))
        for date in cleaned_dates
    ]
    for index, tokens in enumerate(token_sets):
        if not tokens:
            to_remove.add(index)
            continue
        for other_index, other_tokens in enumerate(token_sets):
            if index == other_index or not other_tokens:
                continue
            if tokens < other_tokens:
                to_remove.add(index)
                break

    deduped = []
    seen_texts: set[str] = set()
    for index, date in enumerate(cleaned_dates):
        if index in to_remove:
            continue
        text = str(date.get("text") or "")
        if text in seen_texts:
            continue
        seen_texts.add(text)
        deduped.append(date)
    deduped.sort(key=lambda item: float(item.get("start_seconds") or 0.0))
    return deduped


def _date_overlays(narration: str, duration: int) -> list[dict[str, Any]]:
    words = re.findall(r"[A-Za-z0-9']+", narration)
    if not words:
        return []
    overlays = []
    covered_years: set[str] = set()
    month_pattern = (
        "january|february|march|april|may|june|july|august|september|october|november|december"
    )
    season_pattern = "spring|summer|autumn|fall|winter"
    patterns = [
        re.compile(rf"\b(\d{{1,2}}(?:st|nd|rd|th)?)\s+({month_pattern})\s*,?\s+(\d{{4}})\b", re.IGNORECASE),
        re.compile(rf"\b({month_pattern})\s+(\d{{1,2}}(?:st|nd|rd|th)?)\s*,?\s+(\d{{4}})\b", re.IGNORECASE),
    ]
    full_date_spans = [
        match.span()
        for pattern in patterns
        for match in pattern.finditer(narration)
    ]
    month_year_pattern = re.compile(rf"\b({month_pattern})\s+(\d{{4}})\b", re.IGNORECASE)
    for match in month_year_pattern.finditer(narration):
        if any(match.start() >= span_start and match.end() <= span_end for span_start, span_end in full_date_spans):
            continue
        month = match.group(1)
        year = match.group(2)
        month_index = _find_phrase(words, [month])
        if month_index is not None:
            overlays.append(_date_overlay(f"{month} {year}", _spoken_time(month_index, len(words), duration), "small"))
            covered_years.add(year)
    season_year_patterns = [
        re.compile(rf"\b({season_pattern})\s+(\d{{4}})\b", re.IGNORECASE),
        re.compile(rf"\b({season_pattern})\s+of\s+(\d{{4}})\b", re.IGNORECASE),
    ]
    for pattern in season_year_patterns:
        for match in pattern.finditer(narration):
            season = match.group(1)
            year = match.group(2)
            season_index = _find_phrase(words, [season])
            if season_index is not None:
                overlays.append(_date_overlay(f"{season} {year}", _spoken_time(season_index, len(words), duration), "small"))
                covered_years.add(year)
    for pattern in patterns:
        for match in pattern.finditer(narration):
            parts = [part for part in match.groups() if part]
            if len(parts) != 3:
                continue
            if parts[0].lower() in month_pattern.split("|"):
                day_month = f"{parts[0]} {parts[1]}"
                year = parts[2]
            else:
                day_month = f"{parts[0]} {parts[1]}"
                year = parts[2]
            start_index = _find_phrase(words, re.findall(r"[A-Za-z0-9']+", day_month))
            if start_index is not None:
                overlays.append(_date_overlay(f"{day_month} {year}", _spoken_time(start_index, len(words), duration), "small"))
                covered_years.add(year)
    for index, word in enumerate(words):
        if re.fullmatch(r"(18|19|20)\d{2}s?", word) and word not in covered_years and not any(str(overlay.get("text")) == word for overlay in overlays):
            overlays.append(_date_overlay(word, _spoken_time(index, len(words), duration), "small"))
    return overlays


def _date_overlay(
    text: str,
    start: float,
    size: str,
    *,
    align_to: str = "first_word",
) -> dict[str, Any]:
    return _force_date_overlay_style({
        "text": _clean_overlay_text_for_kind(text, "date"),
        "start_seconds": round(max(0.0, start), 2),
        "duration_seconds": 2.8,
        "align_to": align_to,
        "position": "upper_left",
        "effect": "typewriter",
        "style": "serif",
        "font": "Georgia",
        "color": "yellow",
        "size": size,
        "sfx": "typewriter",
        "opacity": 1.0,
        "dim_background": False,
        "dim_opacity": 0.0,
        "kind": "date",
    })


def _force_date_overlay_style(overlay: dict[str, Any]) -> dict[str, Any]:
    overlay = dict(overlay)
    overlay.pop("static_prefix", None)
    overlay.pop("append_text", None)
    overlay.pop("append_start_seconds", None)
    overlay["position"] = "upper_left"
    overlay["effect"] = "typewriter"
    overlay["style"] = "serif"
    overlay["font"] = "Georgia"
    overlay["color"] = "yellow"
    overlay["size"] = "small"
    overlay["sfx"] = "typewriter"
    overlay["dim_background"] = False
    overlay["dim_opacity"] = 0.0
    overlay["kind"] = "date"
    return overlay


def _location_overlays(narration: str, duration: int) -> list[dict[str, Any]]:
    words = re.findall(r"[A-Za-z0-9']+", narration)
    if not words:
        return []
    month_names = {
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    }
    pattern = re.compile(
        r"\b([A-Z][A-Za-z']+(?:\s+[A-Z][A-Za-z']+){0,2}),\s+([A-Z][A-Za-z']+(?:\s+[A-Z][A-Za-z']+){0,2})\b"
    )
    overlays = []
    for match in pattern.finditer(narration):
        left = match.group(1).strip()
        right = match.group(2).strip()
        if left.lower() in month_names or right.lower() in month_names:
            continue
        left_words = re.findall(r"[A-Za-z0-9']+", left)
        right_words = re.findall(r"[A-Za-z0-9']+", right)
        left_index = _find_phrase(words, left_words)
        right_index = _find_phrase(words, right_words)
        if left_index is not None:
            overlays.append(_location_overlay(left, _spoken_time(left_index, len(words), duration)))
        if right_index is not None:
            overlays.append(_location_overlay(right, _spoken_time(right_index, len(words), duration)))
    return overlays


def _location_overlay(text: str, start: float) -> dict[str, Any]:
    cleaned = _clean_overlay_text(text)
    return _force_location_overlay_style({
        "text": cleaned,
        "start_seconds": round(max(0.0, start), 2),
        "duration_seconds": round(max(2.4, min(3.9, 2.35 + len(cleaned.replace(" ", "")) * 0.065)), 2),
        "kind": "location",
    })


def _force_location_overlay_style(overlay: dict[str, Any]) -> dict[str, Any]:
    overlay = dict(overlay)
    overlay["position"] = "lower_right"
    overlay["effect"] = "typewriter"
    overlay["style"] = "serif"
    overlay["font"] = LOCATION_FONT
    overlay["color"] = "green"
    overlay["size"] = "small"
    overlay["sfx"] = "typewriter"
    overlay["opacity"] = 0.95
    overlay["dim_background"] = False
    overlay["dim_opacity"] = 0.0
    overlay["kind"] = "location"
    return overlay


def _repeated_phrase_overlays(narration: str, duration: int) -> list[dict[str, Any]]:
    words = re.findall(r"[A-Za-z0-9']+", narration)
    if not words:
        return []
    overlays = []
    clauses = []
    for match in re.finditer(r"\bno\b[^,.;:!?-]*", narration, re.IGNORECASE):
        clause = " ".join(re.findall(r"[A-Za-z0-9']+", match.group(0))[:3])
        clause_words = re.findall(r"[A-Za-z0-9']+", clause)
        if len(clause_words) < 2 or not _phrase_words_are_valid(clause_words):
            continue
        clauses.append((clause, clause_words))
    if len(clauses) < 2:
        return []
    for phrase, phrase_words in clauses[:3]:
        start_index = _find_phrase(words, phrase_words)
        if start_index is None:
            continue
        overlays.append(
            {
                "text": _clean_overlay_text_for_kind(phrase, "sequence"),
                "start_seconds": round(_spoken_time(start_index, len(words), duration), 2),
                "duration_seconds": 0.95,
                "position": "center",
                "effect": "pop",
                "style": "block",
                "font": "Impact",
                "color": "red",
                "size": "large",
                "sfx": "camera_click",
                "opacity": 1.0,
                "dim_background": True,
                "dim_opacity": 0.38,
                "kind": "sequence",
            }
        )
    return overlays


def _vary_overlay_styles(beats: list[dict[str, Any]]) -> None:
    colors = ["green", "yellow", "red", "yellow", "green", "red"]
    effects = ["pop", "fade", "typewriter", "pop", "fade"]
    styles = ["block", "serif", "block", "serif"]
    sizes = ["large", "large", "huge", "medium", "large"]
    for index, beat in enumerate(beats):
        for overlay_index, overlay in enumerate(_beat_overlays(beat)):
            if not _clean_overlay_text(str(overlay.get("text") or "")):
                continue
            if str(overlay.get("kind") or "") in {"date", "sequence", "location", "spoken", "directed", "chapter_card"}:
                continue
            offset = index + overlay_index
            overlay["color"] = colors[offset % len(colors)]
            overlay["effect"] = effects[offset % len(effects)]
            overlay["style"] = styles[offset % len(styles)]
            overlay["font"] = overlay.get("font") or ("Impact" if overlay["style"] == "block" else "Georgia")
            overlay["sfx"] = overlay.get("sfx") or ("typewriter" if overlay["effect"] == "typewriter" else "camera_click")
            overlay["size"] = sizes[offset % len(sizes)]
        overlays = _beat_overlays(beat)
        if overlays:
            beat["overlay"] = overlays[0]


def _mock_plan(topic: str, duration: int) -> dict[str, Any]:
    base = [
        (
            "The dossier opens",
            "A fully clothed adult Fern-style anonymous doll figure with a glossy smooth white plastic full head, completely blank oval face, no eye holes and no facial features, charcoal suit, white shirt, black tie, black leather gloves, and black overcoat opens a closed unmarked folder in a plain dark private room under a single desk lamp.",
            f"This case starts with a question that sounds made for a rumor: {topic}. But the first clue is not money. It is trust.",
            "TRUST",
        ),
        (
            "The first approach",
            "A fully clothed adult Fern-style anonymous doll figure with a glossy smooth white plastic full head, completely blank oval face, tailored navy suit, white shirt, tie, overcoat, dress shoes, black gloves, and optional dark fedora walks through an elegant hotel lobby carrying a leather briefcase.",
            "A stranger walks into the room looking exactly like someone important. Nobody checks the story, because the costume does the talking.",
            "THE COSTUME",
        ),
        (
            "The private meeting",
            "Fully clothed adult Fern-style anonymous doll figures with glossy smooth white plastic full heads, completely blank oval faces, dark suits, white shirts, ties, dress shoes, and black gloves sit around a polished table while a closed unmarked folder slides into view.",
            "The meeting is small, quiet, and expensive. Every person at the table thinks they are the only one close enough to see the flaw.",
            "THE FLAW",
        ),
        (
            "The fake paperwork",
            "Close shot of black gloved hands placing sealed blank envelopes, a closed unmarked folder, and a leather briefcase across a polished desk in a plain room with bare walls.",
            "Then the proof appears. Not enough to explain everything, just enough to make backing out feel more dangerous than staying in.",
            "THE PROOF",
        ),
        (
            "The money appears",
            "A briefcase opens on a table with bundled blank paper stacks resembling money, fully clothed Fern-style anonymous doll figures with glossy smooth white plastic full heads, completely blank oval faces, dark suits, white shirts, ties, and black gloves frozen in tense silence.",
            "By the time the money moves, the important part has already happened. The victims have convinced themselves the impossible is safe.",
            "THE MONEY",
        ),
        (
            "The escape",
            "Night train station exterior, the central figure is a Fern-style anonymous doll with a glossy smooth white plastic full head, completely blank oval face, dark overcoat, fedora, trousers, dress shoes, and black gloves, walking away with a suitcase through rain and reflected light.",
            "When suspicion finally arrives, it arrives late. The room is empty, the trail is cold, and every witness remembers a different version.",
            "TOO LATE",
        ),
        (
            "The second room",
            "Another luxury room with bare walls, another group of fully clothed Fern-style anonymous doll buyers with glossy smooth white plastic full heads, completely blank oval faces, dark suits, white shirts, ties, and black gloves, colder lighting.",
            "The strangest part is that the same pattern can work again. Change the room, change the names, and the weakness stays the same.",
            "AGAIN",
        ),
        (
            "The file closes",
            "Back in the plain dark private room, a fully clothed Fern-style anonymous doll figure with a glossy smooth white plastic full head, completely blank oval face, black suit, black overcoat, and black gloves closes an unmarked folder and disappears into darkness.",
            "So the real mystery is not how someone found the opening. It is why everyone in the room helped hold it open.",
            "THE OPENING",
        ),
    ]
    beats = []
    for title, visual, narration, overlay in base:
        beats.append(
            {
                "title": title,
                "duration_seconds": max(5, min(20, round(duration / len(base)))),
                "narration": narration,
                "video_prompt": visual,
                "overlay": {
                    "text": overlay,
                    "start_seconds": 1.2,
                    "duration_seconds": 1.8,
                    "position": "center",
                    "effect": "pop" if overlay else "fade",
                    "style": "block" if overlay else "serif",
                    "color": "green" if overlay else "yellow",
                    "size": "huge" if overlay else "large",
                },
            }
        )
    return {"topic": topic, "beats": beats}


def _no_text_video_prompt(prompt: str, topic: str) -> str:
    cleaned = _strip_text_requests(prompt)
    return (
        "Strict cinematic reenactment shot. "
        f"{cleaned} "
        "Every visible person is a fully clothed Fern-style anonymous doll figure with a glossy smooth white plastic full head and completely blank oval face, no eye holes, no black eyes, no mouth, no nose, no facial features. "
        "Wardrobe must fit the role: dark suits, coats, uniforms, prison clothing, workwear, gloves, boots, or optional role headwear such as police hats, helmets, peaked caps, fedoras, or hoods when appropriate. "
        "Characters resemble elegant glossy white-headed anonymous documentary dolls, not theatrical masks, not rubber masks, not shop mannequins, not plastic display bodies, not human faces under masks. "
        "No bare skin. No display mannequin. No plastic shop dummy. No nude body. No bare torso. No underwear. No skin-colored bodysuit. No anatomy-focused framing. "
        "Plain cinematic set with bare walls, curtains, lamps, polished desks, briefcases, closed unmarked folders, sealed blank envelopes, empty corridors, hotel lobbies, or rainy streets. "
        "Dark premium documentary mood, realistic lighting, shallow depth of field, subtle handheld camera movement, slow push-in or lateral dolly, 35mm film look. "
        "No posters, no wall art, no notice boards, no evidence boards, no wall maps, no blueprints, no documents, no paperwork, no books, no newspapers, no screens, no phones, no computer interfaces, no signs, no labels, no diagrams. "
        "No visible writing of any kind. No readable text. No unreadable fake text. No gibberish letters. No numbers. No symbols. No logos. No watermarks. No title cards."
    )


def _keyframe_prompt(prompt: str) -> str:
    cleaned = _strip_text_requests(prompt)
    return (
        "Photorealistic cinematic keyframe for an investigative documentary reenactment. "
        f"{cleaned} "
        "Every visible person is a fully clothed Fern-style anonymous doll figure with a glossy smooth white plastic full head and completely blank oval face, no eye holes, no black eyes, no mouth, no nose, no facial features. "
        "Wardrobe must fit the role: dark suits, coats, uniforms, prison clothing, workwear, gloves, boots, or optional role headwear such as police hats, helmets, peaked caps, fedoras, or hoods when appropriate. "
        "Characters look like elegant glossy white-headed anonymous documentary dolls, not theatrical masks, not rubber masks, not naked mannequins, not shop-window dummies, not plastic display bodies, not human faces under masks. "
        "No bare skin, no bare torso, no nude body, no underwear, no skin-colored bodysuit, no anatomy-focused framing. "
        "Dark premium documentary lighting, realistic set, 35mm film still, shallow depth of field, crisp detailed environment, cinematic composition. "
        "Plain walls, curtains, lamps, polished desks, leather briefcases, closed unmarked folders, sealed blank envelopes, hotel lobbies, train platforms, rainy streets. "
        "No posters, no wall art, no notice boards, no evidence boards, no wall maps, no blueprints, no documents, no paperwork, no books, no newspapers, no screens, no phones, no signs, no labels, no diagrams. "
        "No visible writing of any kind, no readable text, no fake text, no gibberish letters, no numbers, no symbols, no logos, no watermark."
    )


def _strip_text_requests(prompt: str) -> str:
    replacements = {
        r"\bnewspaper[s]?\b": "closed unmarked folder",
        r"\bheadline[s]?\b": "closed unmarked folder",
        r"\btitle card[s]?\b": "dark scene",
        r"\bcaption[s]?\b": "visual detail",
        r"\bsubtitle[s]?\b": "visual detail",
        r"\btext\b": "blank surface",
        r"\bwords\b": "blank surface",
        r"\bletters\b": "blank surface",
        r"\bnumbers\b": "blank surface",
        r"\bsign[s]?\b": "blank surface",
        r"\bmap labels?\b": "plain wall",
        r"\bmap[s]?\b": "plain wall with shadow",
        r"\bdocument[s]?\b": "closed unmarked folder",
        r"\bpaper[s]?\b": "sealed blank envelope",
        r"\bbook[s]?\b": "closed unmarked folder",
        r"\bscreen[s]?\b": "blank dark surface",
        r"\bcomputer[s]?\b": "closed laptop with blank lid",
        r"\bphone[s]?\b": "small dark object with no screen visible",
        r"\bmannequin[s]?\b": "fully clothed Fern-style anonymous doll figure with a glossy smooth white plastic full head and completely blank oval face",
        r"\bmask(?:ed|es|s)?\b": "glossy smooth white plastic full head with a completely blank oval face",
        r"\bdossier[s]?\b": "closed unmarked folder",
        r"\bevidence board[s]?\b": "plain bare wall",
        r"\bnotice board[s]?\b": "plain bare wall",
        r"\bposter[s]?\b": "plain bare wall",
        r"\bblueprint[s]?\b": "closed unmarked folder",
        r"\breceipt[s]?\b": "sealed blank envelope",
        r"\bpassport[s]?\b": "closed unmarked folder",
        r"\bticket[s]?\b": "small blank card",
        r"\bdiagram[s]?\b": "plain object",
        r"\bEiffel Tower\b": "distant iron landmark silhouette",
        r"\bEiffel\b": "iron landmark",
    }
    cleaned = prompt
    for pattern, replacement in replacements.items():
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    return cleaned


def _beat_overlays(beat: dict[str, Any]) -> list[dict[str, Any]]:
    raw_overlays = beat.get("overlays")
    if isinstance(raw_overlays, list):
        candidates = [overlay for overlay in raw_overlays if isinstance(overlay, dict)]
    else:
        overlay = beat.get("overlay")
        candidates = [overlay] if isinstance(overlay, dict) else []
    overlays = []
    for overlay in candidates:
        if str(overlay.get("kind") or "") == "location":
            overlay = _force_location_overlay_style(overlay)
        kind = str(overlay.get("kind") or "")
        text = _clean_overlay_text_for_kind(str(overlay.get("text") or ""), kind)
        if not text:
            continue
        overlay["text"] = text
        overlay["position"] = _overlay_position(str(overlay.get("position") or "center"))
        overlay["effect"] = _overlay_effect(str(overlay.get("effect") or "pop"))
        overlay["font"] = _overlay_font(str(overlay.get("font") or ""))
        overlay["sfx"] = _overlay_sfx(str(overlay.get("sfx") or ""))
        overlay["opacity"] = _overlay_opacity(overlay.get("opacity"))
        overlay["dim_background"] = _overlay_dim_background(overlay.get("dim_background"), str(overlay.get("kind") or ""))
        overlay["dim_opacity"] = _overlay_dim_opacity(overlay.get("dim_opacity"))
        overlays.append(overlay)
    return _limit_overlays(overlays)


def _visible_beat_overlays(beat: dict[str, Any]) -> list[dict[str, Any]]:
    return [overlay for overlay in _beat_overlays(beat) if str(overlay.get("kind") or "") != "chapter_card"]


def _burn_overlays(input_path: Path, output_path: Path, beat: dict[str, Any], *, fps: int) -> None:
    overlays = _visible_beat_overlays(beat)
    if not overlays:
        run_ffmpeg(
            [
                "-i",
                str(input_path),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "16",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ]
        )
        return

    ass_path = output_path.with_suffix(".ass")
    _write_overlay_ass(ass_path, beat)
    ass_filter_path = ass_path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")
    dim_filters = ",".join(
        "drawbox=x=0:y=0:w=iw:h=ih:"
        f"color=black@{_overlay_dim_opacity(overlay.get('dim_opacity')):.2f}:t=fill:"
        f"enable='between(t,{float(overlay.get('start_seconds') or 0.0):.3f},"
        f"{float(overlay.get('start_seconds') or 0.0) + float(overlay.get('duration_seconds') or 1.2):.3f})'"
        for overlay in overlays
        if _overlay_dim_background(overlay.get("dim_background"), str(overlay.get("kind") or ""))
    )
    if dim_filters:
        vf = (
            "format=yuv420p,"
            f"{dim_filters},"
            f"ass='{ass_filter_path}'"
        )
    else:
        vf = f"format=yuv420p,ass='{ass_filter_path}'"
    run_ffmpeg(
        [
            "-i",
            str(input_path),
            "-an",
            "-vf",
            vf,
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )


def _make_archival_motion_clip(image_path: Path, output_path: Path, *, duration: int, fps: int, motion: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(duration * fps))
    motion_key = motion.strip().lower()
    if motion_key == "handheld_drift":
        x_expr = "iw/2-(iw/zoom/2)+sin(on/16)*4"
        y_expr = "ih/2-(ih/zoom/2)+cos(on/19)*3"
        z_expr = "min(zoom+0.00022,1.024)"
    elif motion_key == "glitch_reveal":
        x_expr = "iw/2-(iw/zoom/2)+sin(on/6)*3"
        y_expr = "ih/2-(ih/zoom/2)+cos(on/7)*2"
        z_expr = "min(zoom+0.00025,1.026)"
    else:
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
        z_expr = "min(zoom+0.00020,1.022)"
    vf = (
        "split=2[bg][fg];"
        "[bg]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
        "gblur=sigma=22,eq=contrast=1.10:brightness=-0.16:saturation=0.35[bg];"
        "[fg]scale=1120:650:force_original_aspect_ratio=decrease,"
        "eq=contrast=1.12:brightness=-0.025:saturation=0.72:gamma=0.94,"
        "unsharp=5:5:0.35:3:3:0.10,format=rgba[fg];"
        "[bg][fg]overlay=x=(W-w)/2:y=min((H-h)/2+24\\,H-h),"
        "noise=alls=5:allf=t+u,vignette=PI/4,drawgrid=w=1280:h=4:t=1:c=black@0.12,"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s=1280x720:fps={fps},"
        "format=yuv420p"
    )
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-t",
            f"{float(duration):.3f}",
            "-vf",
            vf,
            "-r",
            str(fps),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )


def _apply_archival_insert(
    input_path: Path,
    image_path: Path,
    output_path: Path,
    *,
    start_seconds: float,
    duration_seconds: float,
    fps: int,
    motion: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    video_duration = ffprobe_duration(input_path)
    start = max(0.0, min(float(start_seconds), max(0.0, video_duration - 0.5)))
    insert_duration = max(0.8, min(float(duration_seconds), max(0.8, video_duration - start)))
    insert_path = output_path.with_name(output_path.stem + "_source.mp4")
    _make_archival_motion_clip(image_path, insert_path, duration=max(1, int(math.ceil(insert_duration))), fps=fps, motion=motion)
    end = min(video_duration, start + insert_duration)
    run_ffmpeg(
        [
            "-i",
            str(input_path),
            "-i",
            str(insert_path),
            "-filter_complex",
            (
                "[0:v]setpts=PTS-STARTPTS[base];"
                f"[1:v]setpts=PTS-STARTPTS+{start:.3f}/TB[insert];"
                f"[base][insert]overlay=0:0:eof_action=pass:enable='between(t,{start:.3f},{end:.3f})',"
                "format=yuv420p[vout]"
            ),
            "-map",
            "[vout]",
            "-an",
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )


def _archival_start_seconds(archival: dict[str, Any], segment_seconds: float) -> float:
    try:
        value = float(archival.get("start_seconds"))
    except (TypeError, ValueError):
        value = min(1.4, max(0.25, segment_seconds * 0.12))
    return max(0.0, min(value, max(0.0, segment_seconds - 0.8)))


def _archival_duration_seconds(archival: dict[str, Any], segment_seconds: float) -> float:
    try:
        value = float(archival.get("duration_seconds"))
    except (TypeError, ValueError):
        value = 3.4
    return max(1.2, min(value, 4.6, max(1.2, segment_seconds - 0.2)))


def _write_overlay_ass(path: Path, beat: dict[str, Any]) -> None:
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1280\n"
        "PlayResY: 720\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
    )
    style_lines = []
    event_lines = []
    for index, overlay in enumerate(_visible_beat_overlays(beat)):
        text = _clean_overlay_text(str(overlay.get("text") or ""))
        if not text:
            continue
        style = _overlay_style(str(overlay.get("style") or "serif"))
        color = _ass_color(_overlay_color_name(str(overlay.get("color") or "yellow")))
        size = _font_size(text, style=style, size=_overlay_size_name(str(overlay.get("size") or "large")))
        font = _overlay_font(str(overlay.get("font") or ""))
        if not font:
            font = "Impact" if style == "block" else "Georgia"
        alignment = _ass_alignment(str(overlay.get("position") or "center"))
        margin_v = _ass_margin_v(str(overlay.get("position") or "center"))
        margin_l, margin_r = _ass_margins_lr(str(overlay.get("position") or "center"))
        style_lines.append(
            f"Style: Glow{index},{font},{size},{color},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,{alignment},{margin_l},{margin_r},{margin_v},1"
        )
        style_lines.append(
            f"Style: Main{index},{font},{size},{color},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,{alignment},{margin_l},{margin_r},{margin_v},1"
        )
        event_lines.extend(_overlay_ass_events(overlay, index))
    header += (
        "\n".join(style_lines)
        + "\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    path.write_text(header + "\n".join(event_lines) + "\n", encoding="utf-8")


def _overlay_ass_events(overlay: dict[str, Any], overlay_index: int) -> list[str]:
    text = _clean_overlay_text(str(overlay.get("text") or ""))
    start = float(overlay.get("start_seconds") or 1.0)
    duration = float(overlay.get("duration_seconds") or 1.2)
    end = start + duration
    effect = _overlay_effect(str(overlay.get("effect") or "fade"))
    lines = []
    if effect == "typewriter":
        reveal_duration = min(0.95, max(0.28, duration * 0.30))
        exit_duration = 0.0 if bool(overlay.get("no_exit")) else min(0.70, max(0.22, duration * 0.22))
        entry_prefix = ""
        entry_joiner = ""
        entry_chars = list(text)
        chars = list(text)
        entry_reveal_duration = reveal_duration
        entry_base_start = start
        step = entry_reveal_duration / max(1, len(entry_chars))
        for char_index in range(1, len(entry_chars) + 1):
            partial = f"{entry_prefix}{entry_joiner}{''.join(entry_chars[:char_index])}"
            part_start = entry_base_start + (char_index - 1) * step
            part_end = min(end, entry_base_start + char_index * step)
            glow_tag = r"{\blur10\alpha&H92&}"
            main_tag = r"{\blur1}"
            lines.append(_ass_dialogue(part_start, part_end, f"Glow{overlay_index}", f"{glow_tag}{_ass_text(partial)}"))
            lines.append(_ass_dialogue(part_start, part_end, f"Main{overlay_index}", f"{main_tag}{_ass_text(partial)}"))
        hold_start = min(end, entry_base_start + entry_reveal_duration)
        exit_start = max(hold_start, end - exit_duration) if exit_duration > 0 else end
        if hold_start < exit_start:
            lines.append(_ass_dialogue(hold_start, exit_start, f"Glow{overlay_index}", rf"{{\blur10\alpha&H92&}}{_ass_text(text)}"))
            lines.append(_ass_dialogue(hold_start, exit_start, f"Main{overlay_index}", rf"{{\blur1}}{_ass_text(text)}"))
        if exit_duration <= 0:
            return lines
        exit_step = exit_duration / max(1, len(chars))
        for remaining in range(len(chars) - 1, -1, -1):
            partial = "".join(chars[:remaining])
            if not partial:
                continue
            part_start = exit_start + (len(chars) - 1 - remaining) * exit_step
            part_end = min(end, part_start + exit_step)
            lines.append(_ass_dialogue(part_start, part_end, f"Glow{overlay_index}", rf"{{\blur10\alpha&H92&}}{_ass_text(partial)}"))
            lines.append(_ass_dialogue(part_start, part_end, f"Main{overlay_index}", rf"{{\blur1}}{_ass_text(partial)}"))
    elif effect == "glitch":
        slices = 5
        slice_duration = max(0.055, min(0.12, duration / slices))
        for glitch_index in range(slices):
            part_start = start + glitch_index * slice_duration
            part_end = min(end, part_start + slice_duration * 0.75)
            x_shift = (-1) ** glitch_index * (2 + glitch_index % 3)
            main_tag = rf"{{\fad(15,45)\blur1\pos({640 + x_shift},{360 + (glitch_index % 2) * 3})}}"
            glow_tag = rf"{{\fad(15,45)\blur12\alpha&H8E&\pos({640 - x_shift},{360})}}"
            lines.append(_ass_dialogue(part_start, part_end, f"Glow{overlay_index}", f"{glow_tag}{_ass_text(text)}"))
            lines.append(_ass_dialogue(part_start, part_end, f"Main{overlay_index}", f"{main_tag}{_ass_text(text)}"))
        stable_start = min(end, start + slices * slice_duration)
        if stable_start < end:
            lines.append(_ass_dialogue(stable_start, end, f"Glow{overlay_index}", rf"{{\fad(30,120)\blur11\alpha&H92&}}{_ass_text(text)}"))
            lines.append(_ass_dialogue(stable_start, end, f"Main{overlay_index}", rf"{{\fad(30,120)\blur1}}{_ass_text(text)}"))
    else:
        if effect == "pop":
            main_tag = r"{\fad(45,140)\fscx118\fscy118\t(0,130,\fscx100\fscy100)\blur1}"
            glow_tag = r"{\fad(45,140)\fscx121\fscy121\t(0,150,\fscx103\fscy103)\blur12\alpha&H8E&}"
        else:
            main_tag = r"{\fad(180,180)\blur1}"
            glow_tag = r"{\fad(180,180)\blur11\alpha&H92&}"
        lines.append(_ass_dialogue(start, end, f"Glow{overlay_index}", f"{glow_tag}{_ass_text(text)}"))
        lines.append(_ass_dialogue(start, end, f"Main{overlay_index}", f"{main_tag}{_ass_text(text)}"))
    return lines


def _ass_dialogue(start: float, end: float, style: str, text: str) -> str:
    return f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{text}"


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    centiseconds = int(round(seconds * 100))
    cs = centiseconds % 100
    total_seconds = centiseconds // 100
    sec = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours}:{minutes:02d}:{sec:02d}.{cs:02d}"


def _ass_color(color: str) -> str:
    return {
        "yellow": "&H002BFFFF",
        "green": "&H001AFF1A",
        "red": "&H002020FF",
        "white": "&H00FFFFFF",
    }.get(color, "&H002BFFFF")


def _ass_alignment(position: str) -> int:
    return {
        "top": 8,
        "bottom": 2,
        "left": 4,
        "right": 6,
        "upper_left": 7,
        "upper_right": 9,
        "lower_left": 1,
        "lower_right": 3,
    }.get(position, 5)


def _ass_margin_v(position: str) -> int:
    if position in {"top", "upper_left", "upper_right"}:
        return 92
    if position in {"bottom", "lower_left", "lower_right"}:
        return 92
    return 0


def _ass_margins_lr(position: str) -> tuple[int, int]:
    if position in {"left", "upper_left", "lower_left"}:
        return 92, 40
    if position in {"right", "upper_right", "lower_right"}:
        return 40, 92
    return 60, 60


def _ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "").replace("}", "")


def _make_overlay_sfx(output_path: Path, beats: list[dict[str, Any]], *, download_assets: bool = False) -> None:
    total = sum(int(beat["duration_seconds"]) for beat in beats)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    events = []
    elapsed = 0.0
    for beat in beats:
        for overlay in _visible_beat_overlays(beat):
            overlay_text = _clean_overlay_text(str(overlay.get("text") or ""))
            overlay_kind = str(overlay.get("kind") or "")
            if overlay_kind in {"date", "location", "sequence", "chapter_card"}:
                overlay_text = _clean_overlay_text_for_kind(str(overlay.get("text") or ""), overlay_kind)
            if not overlay_text:
                continue
            events.append(
                (
                    elapsed + float(overlay.get("start_seconds") or 1.0),
                    _overlay_effect(str(overlay.get("effect") or "fade")),
                    float(overlay.get("duration_seconds") or 1.8),
                    len(overlay_text.replace(" ", "")),
                    overlay_kind,
                    _overlay_sfx(str(overlay.get("sfx") or "")),
                )
            )
        elapsed += int(beat["duration_seconds"])
    if not events:
        create_silent_audio(output_path, total)
        return

    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    asset_input_index = 0
    tone_index = 0

    def add_asset(asset_name: str, delay_ms: int, volume: float, trim_seconds: float) -> bool:
        nonlocal asset_input_index, tone_index
        asset_path = asset_path_for_sfx(asset_name, download=download_assets)
        if not asset_path:
            return False
        analysis = _analyze_audio_asset(asset_path)
        lead_silence = analysis.get("lead_silence", 0.0)
        useful_duration = max(0.025, analysis.get("duration", trim_seconds) - lead_silence)
        cut_seconds = max(0.025, min(trim_seconds, useful_duration))
        start_offset = max(0.0, min(lead_silence, max(0.0, analysis.get("duration", cut_seconds) - cut_seconds)))
        adaptive_volume = volume * _asset_volume_multiplier(analysis)
        inputs.extend(["-i", str(asset_path)])
        label = f"a{tone_index}"
        filters.append(
            f"[{asset_input_index}:a]aresample=44100,"
            f"atrim=start={start_offset:.4f}:duration={cut_seconds:.3f},"
            f"silenceremove=start_periods=1:start_duration=0.003:start_threshold=-48dB,"
            f"asetpts=PTS-STARTPTS,"
            f"afade=t=out:st={max(0.0, cut_seconds - 0.018):.3f}:d=0.018,"
            f"afftdn=nr=7:nf=-35,"
            f"aformat=sample_rates=44100:channel_layouts=stereo,"
            f"loudnorm=I=-19:LRA=7:TP=-2,volume={adaptive_volume:.3f},"
            f"adelay={delay_ms}|{delay_ms}[{label}]"
        )
        labels.append(f"[{label}]")
        asset_input_index += 1
        tone_index += 1
        return True

    def add_asset_bed(asset_name: str, delay_ms: int, volume: float, bed_seconds: float) -> bool:
        nonlocal asset_input_index, tone_index
        asset_path = asset_path_for_sfx(asset_name, download=download_assets)
        if not asset_path:
            return False
        analysis = _analyze_audio_asset(asset_path)
        lead_silence = analysis.get("lead_silence", 0.0)
        useful_duration = max(0.04, analysis.get("duration", bed_seconds) - lead_silence)
        cut_seconds = max(0.04, min(bed_seconds, useful_duration))
        start_offset = max(0.0, min(lead_silence, max(0.0, analysis.get("duration", cut_seconds) - cut_seconds)))
        adaptive_volume = volume * _asset_volume_multiplier(analysis)
        inputs.extend(["-i", str(asset_path)])
        label = f"a{tone_index}"
        filters.append(
            f"[{asset_input_index}:a]aresample=44100,"
            f"atrim=start={start_offset:.4f}:duration={cut_seconds:.3f},"
            f"silenceremove=start_periods=1:start_duration=0.003:start_threshold=-48dB,"
            f"asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d=0.012,"
            f"afade=t=out:st={max(0.0, cut_seconds - 0.040):.3f}:d=0.040,"
            f"afftdn=nr=7:nf=-35,"
            f"aformat=sample_rates=44100:channel_layouts=stereo,"
            f"loudnorm=I=-20:LRA=7:TP=-2,volume={adaptive_volume:.3f},"
            f"adelay={delay_ms}|{delay_ms}[{label}]"
        )
        labels.append(f"[{label}]")
        asset_input_index += 1
        tone_index += 1
        return True

    def add_procedural_click(delay_ms: int) -> None:
        nonlocal tone_index
        filters.append(
            f"anoisesrc=color=white:duration=0.035:sample_rate=44100,highpass=f=1400,lowpass=f=5200,volume=0.50,adelay={delay_ms}|{delay_ms}[c{tone_index}]"
        )
        filters.append(
            f"sine=frequency=95:duration=0.055,volume=0.18,adelay={delay_ms + 18}|{delay_ms + 18}[b{tone_index}]"
        )
        labels.extend([f"[c{tone_index}]", f"[b{tone_index}]"])
        tone_index += 1

    def add_procedural_tick(delay_ms: int) -> None:
        nonlocal tone_index
        filters.append(
            f"anoisesrc=color=white:duration=0.018:sample_rate=44100,highpass=f=1700,lowpass=f=6000,volume=0.16,adelay={delay_ms}|{delay_ms}[tc{tone_index}]"
        )
        labels.append(f"[tc{tone_index}]")
        tone_index += 1

    for event_time, effect, duration, char_count, kind, sfx in events:
        if sfx == "none":
            continue
        delay_ms = max(0, int(event_time * 1000))
        if effect == "typewriter" or sfx == "typewriter":
            reveal_ms = int(min(950, max(280, duration * 420)))
            if kind in {"date", "location"}:
                if not add_asset_bed("typewriter_tick", delay_ms, 0.62, reveal_ms / 1000.0):
                    filters.append(
                        f"anoisesrc=color=white:duration={reveal_ms / 1000.0:.3f}:sample_rate=44100,"
                        f"highpass=f=1900,lowpass=f=6200,volume=0.11,adelay={delay_ms}|{delay_ms}[tw{tone_index}]"
                    )
                    labels.append(f"[tw{tone_index}]")
                    tone_index += 1
                continue
            repeats = max(1, char_count) if kind == "location" else min(14, max(3, char_count))
            step_ms = max(28, min(75, int(reveal_ms / max(1, repeats))))
            for repeat in range(repeats):
                repeat_delay = delay_ms + repeat * step_ms
                if not add_asset("typewriter_tick", repeat_delay, 0.70 if kind in {"location", "date"} else 0.25, 0.070):
                    add_procedural_tick(repeat_delay)
            continue
        if sfx in {"", "camera_click"}:
            if not add_asset("camera_click", delay_ms, 0.50, 0.22):
                add_procedural_click(delay_ms)
        elif sfx == "glitch":
            if not add_asset("glitch_burst", delay_ms, 0.36, 0.32):
                filters.append(
                    f"anoisesrc=color=pink:duration=0.12:sample_rate=44100,highpass=f=900,lowpass=f=7000,volume=0.22,adelay={delay_ms}|{delay_ms}[g{tone_index}]"
                )
                labels.append(f"[g{tone_index}]")
                tone_index += 1
        elif sfx == "boom":
            if not add_asset("boom", delay_ms, 0.32, 0.62):
                filters.append(
                    f"sine=frequency=58:duration=0.28,volume=0.32,adelay={delay_ms}|{delay_ms}[bo{tone_index}]"
                )
                labels.append(f"[bo{tone_index}]")
                tone_index += 1
        else:
            if not add_asset(sfx, delay_ms, 0.36, 0.45):
                print(f"Missing SFX asset for '{sfx}'; continuing without that sound.")
    if not labels:
        create_silent_audio(output_path, total)
        return
    filter_complex = ";".join(filters + [f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest,apad=pad_dur={total}[out]"])
    run_ffmpeg([*inputs, "-filter_complex", filter_complex, "-map", "[out]", "-t", f"{total:.3f}", "-c:a", "pcm_s16le", str(output_path)])


def _make_ambient_bed(output_path: Path, ambient: str, duration: int, *, download_assets: bool = False) -> None:
    ambient = _ambient_bed(ambient)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if ambient == "none":
        create_silent_audio(output_path, duration)
        return
    asset_path = _find_ambient_asset(ambient, download_assets=download_assets)
    if asset_path:
        fade_out = max(0.0, float(duration) - 0.45)
        run_ffmpeg(
            [
                "-stream_loop",
                "-1",
                "-i",
                str(asset_path),
                "-t",
                f"{float(duration):.3f}",
                "-af",
                f"volume=0.16,afade=t=in:st=0:d=0.35,afade=t=out:st={fade_out:.3f}:d=0.45",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ]
        )
        return
    print(f"Missing ambience asset for '{ambient}'.")
    generated_asset = _make_cached_ambient_asset(ambient)
    if generated_asset:
        _make_ambient_bed(output_path, ambient, duration, download_assets=False)
        return
    create_silent_audio(output_path, duration)


def _analyze_audio_asset(asset_path: Path) -> dict[str, float]:
    cache_key = str(asset_path.resolve())
    cached = AUDIO_ANALYSIS_CACHE.get(cache_key)
    if cached:
        return cached

    analysis = {
        "duration": 0.0,
        "lead_silence": 0.0,
        "mean_volume": -24.0,
        "max_volume": -6.0,
    }
    try:
        analysis["duration"] = max(0.0, float(ffprobe_duration(asset_path)))
    except Exception:
        pass

    ffmpeg = media_executable("ffmpeg")
    if ffmpeg:
        command = [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(asset_path),
            "-af",
            "silencedetect=noise=-48dB:d=0.003,volumedetect",
            "-f",
            "null",
            "-",
        ]
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=20)
            stderr = result.stderr or ""
            lead_silence = _first_float_match(stderr, r"silence_end:\s*([0-9.]+)")
            mean_volume = _first_float_match(stderr, r"mean_volume:\s*(-?[0-9.]+)\s*dB")
            max_volume = _first_float_match(stderr, r"max_volume:\s*(-?[0-9.]+)\s*dB")
            if lead_silence is not None:
                analysis["lead_silence"] = max(0.0, min(lead_silence, max(0.0, analysis["duration"] - 0.01)))
            if mean_volume is not None:
                analysis["mean_volume"] = mean_volume
            if max_volume is not None:
                analysis["max_volume"] = max_volume
        except Exception:
            pass

    AUDIO_ANALYSIS_CACHE[cache_key] = analysis
    return analysis


def _asset_volume_multiplier(analysis: dict[str, float]) -> float:
    mean_volume = analysis.get("mean_volume", -24.0)
    max_volume = analysis.get("max_volume", -6.0)
    if max_volume > -1.0:
        return 0.55
    if mean_volume > -14.0:
        return 0.70
    if mean_volume < -36.0:
        return 1.45
    if mean_volume < -30.0:
        return 1.20
    return 1.0


def _first_float_match(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _find_ambient_asset(ambient: str, *, download_assets: bool = False) -> Path | None:
    return asset_path_for_ambience(ambient, download=download_assets)


def _make_cached_ambient_asset(ambient: str) -> Path | None:
    ambient = _ambient_bed(ambient)
    if ambient == "none":
        return None
    path = Path("assets") / "ambience" / f"{ambient}.wav"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_ffmpeg(
            [
                "-filter_complex",
                _ambient_filter(ambient, 30),
                "-map",
                "[out]",
                "-t",
                "30.000",
                "-c:a",
                "pcm_s16le",
                str(path),
            ]
        )
    except Exception as exc:
        print(f"Could not create fallback ambience '{ambient}': {exc}")
        return None
    print(f"Created fallback ambience asset: {path}")
    return path


def _ambient_filter(ambient: str, duration: int) -> str:
    fade_out = max(0.0, float(duration) - 0.45)
    if ambient == "industrial_hum":
        source = (
            "sine=frequency=58:duration={d},volume=0.045[a0];"
            "sine=frequency=116:duration={d},volume=0.025[a1];"
            "anoisesrc=color=brown:duration={d}:sample_rate=44100,lowpass=f=420,volume=0.020[a2];"
            "[a0][a1][a2]amix=inputs=3"
        )
    elif ambient == "surveillance_noise":
        source = (
            "anoisesrc=color=pink:duration={d}:sample_rate=44100,highpass=f=1200,lowpass=f=5200,volume=0.026[a0];"
            "sine=frequency=1560:duration={d},volume=0.006[a1];"
            "[a0][a1]amix=inputs=2"
        )
    elif ambient == "distant_wind":
        source = "anoisesrc=color=brown:duration={d}:sample_rate=44100,highpass=f=90,lowpass=f=900,volume=0.040"
    elif ambient == "courthouse_room":
        source = (
            "anoisesrc=color=pink:duration={d}:sample_rate=44100,highpass=f=120,lowpass=f=1800,volume=0.018[a0];"
            "sine=frequency=72:duration={d},volume=0.012[a1];"
            "[a0][a1]amix=inputs=2"
        )
    elif ambient == "room_tone":
        source = "anoisesrc=color=pink:duration={d}:sample_rate=44100,highpass=f=80,lowpass=f=1200,volume=0.018"
    else:
        source = (
            "sine=frequency=46:duration={d},volume=0.040[a0];"
            "anoisesrc=color=brown:duration={d}:sample_rate=44100,lowpass=f=260,volume=0.018[a1];"
            "[a0][a1]amix=inputs=2"
        )
    return f"{source.format(d=float(duration))},afade=t=in:st=0:d=0.35,afade=t=out:st={fade_out:.3f}:d=0.45[out]"


def _align_overlays_to_word_timestamps(beats: list[dict[str, Any]], word_timestamps_path: Path | None) -> None:
    if not word_timestamps_path or not word_timestamps_path.exists():
        print("Word timing unavailable; keeping estimated overlay timing.")
        return
    try:
        payload = json.loads(word_timestamps_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("Word timing unreadable; keeping estimated overlay timing.")
        return

    transcript_words = []
    for item in payload.get("words", []) or []:
        word = _normalize_word(str(item.get("word") or ""))
        if not word:
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        transcript_words.append({"word": word, "start": start, "end": end})
    if not transcript_words:
        print("Word timing empty; keeping estimated overlay timing.")
        return

    elapsed = 0.0
    matched = 0
    for beat in beats:
        duration = float(beat.get("duration_seconds") or 1)
        overlay = beat.get("overlay", {})
        phrase = _clean_overlay_text(str(overlay.get("text") or ""))
        phrase_words = [_normalize_word(word) for word in re.findall(r"[A-Za-z0-9']+", phrase)]
        phrase_words = [word for word in phrase_words if word]
        if not phrase_words:
            elapsed += duration
            continue
        timing = _find_transcript_phrase_timing(
            transcript_words,
            phrase_words,
            window_start=max(0.0, elapsed - 1.5),
            window_end=elapsed + duration + 1.5,
        )
        if timing is not None:
            start, end = timing
            local_start = max(0.0, min(start - elapsed, max(0.0, duration - 0.35)))
            overlay["start_seconds"] = round(local_start, 2)
            overlay["duration_seconds"] = round(max(0.75, min(2.8, end - start + 0.42, duration - local_start)), 2)
            matched += 1
        elapsed += duration
    print(f"Aligned {matched} overlay(s) to actual narration word timing.")


def _align_beat_overlay_to_word_timestamps(beat: dict[str, Any], word_timestamps_path: Path | None) -> None:
    if not word_timestamps_path or not word_timestamps_path.exists():
        print(f"Word timing unavailable for beat: {beat.get('title', 'untitled')}")
        return
    try:
        payload = json.loads(word_timestamps_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"Word timing unreadable for beat: {beat.get('title', 'untitled')}")
        return

    transcript_words = []
    for item in payload.get("words", []) or []:
        word = _normalize_word(str(item.get("word") or ""))
        if not word:
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        transcript_words.append({"word": word, "start": start, "end": end})
    if not transcript_words:
        return

    duration = float(beat.get("duration_seconds") or 20)
    matched = 0
    aligned_overlays = []
    for overlay in _beat_overlays(beat):
        kind = str(overlay.get("kind") or "")
        if kind == "chapter_card":
            aligned_overlays.append(overlay)
            continue
        phrase = _clean_overlay_text_for_kind(str(overlay.get("text") or ""), kind)
        phrase_words = [_normalize_word(word) for word in re.findall(r"[A-Za-z0-9']+", phrase)]
        phrase_words = [word for word in phrase_words if word]
        if not phrase_words:
            continue
        timing = _find_transcript_phrase_timing(
            transcript_words,
            phrase_words,
            window_start=0.0,
            window_end=duration,
        )
        if timing is not None and str(overlay.get("align_to") or "") == "last_word" and len(phrase_words) > 1:
            last_word_timing = _find_transcript_phrase_timing(
                transcript_words,
                [phrase_words[-1]],
                window_start=timing[0],
                window_end=min(duration, timing[1] + 0.75),
            )
            if last_word_timing is not None:
                timing = last_word_timing
        if timing is None:
            print(f"Dropped mistimed overlay for beat '{beat.get('title', 'untitled')}': {phrase}")
            continue

        start, end = timing
        overlay["start_seconds"] = round(max(0.0, min(start - 0.03, max(0.0, duration - 0.35))), 2)
        if kind in {"date", "location"}:
            hold_bonus = 2.0
            min_duration = 2.3
            max_duration = 4.2
        else:
            hold_bonus = 0.32
            min_duration = 0.45
            max_duration = 1.65
        overlay["duration_seconds"] = round(
            max(min_duration, min(max_duration, end - start + hold_bonus, duration - overlay["start_seconds"])),
            2,
        )
        aligned_overlays.append(overlay)
        matched += 1
    _trim_corner_label_overlaps(aligned_overlays, "date")
    _trim_corner_label_overlaps(aligned_overlays, "location")
    beat["overlays"] = _limit_overlays(aligned_overlays)
    overlays = _beat_overlays(beat)
    if overlays:
        beat["overlay"] = overlays[0]


def _trim_corner_label_overlaps(overlays: list[dict[str, Any]], kind: str) -> None:
    labels = sorted(
        [overlay for overlay in overlays if str(overlay.get("kind") or "") == kind],
        key=lambda item: float(item.get("start_seconds") or 0.0),
    )
    to_remove: set[int] = set()
    for current, nxt in zip(labels, labels[1:]):
        current_start = float(current.get("start_seconds") or 0.0)
        next_start = float(nxt.get("start_seconds") or 0.0)
        if next_start <= current_start:
            continue
        new_duration = max(0.0, next_start - current_start - 0.03)
        if new_duration < 0.28:
            to_remove.add(id(current))
        else:
            current["duration_seconds"] = round(new_duration, 2)
            current["no_exit"] = True
    if to_remove:
        overlays[:] = [overlay for overlay in overlays if id(overlay) not in to_remove]


def _find_transcript_phrase_timing(
    transcript_words: list[dict[str, Any]],
    phrase_words: list[str],
    *,
    window_start: float,
    window_end: float,
) -> tuple[float, float] | None:
    best: tuple[float, float, float] | None = None
    words = [item["word"] for item in transcript_words]
    for index in range(0, len(words) - len(phrase_words) + 1):
        if words[index : index + len(phrase_words)] != phrase_words:
            continue
        start = float(transcript_words[index]["start"])
        end = float(transcript_words[index + len(phrase_words) - 1]["end"])
        center = (start + end) / 2
        window_center = (window_start + window_end) / 2
        outside_penalty = 0 if window_start <= center <= window_end else 100
        score = abs(center - window_center) + outside_penalty
        if best is None or score < best[0]:
            best = (score, start, end)
    if best is None:
        return None
    return best[1], best[2]


def _normalize_word(word: str) -> str:
    cleaned = re.findall(r"[A-Za-z0-9']+", word.lower())
    return cleaned[0].strip("'") if cleaned else ""


def _concat_video_only(clips: list[Path], output_path: Path) -> None:
    list_path = output_path.with_suffix(".concat.txt")
    lines = [f"file '{clip.resolve().as_posix()}'" for clip in clips]
    list_path.write_text("\n".join(lines), encoding="utf-8")
    run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )


def _concat_av_segments(clips: list[Path], output_path: Path, *, fps: int = 24) -> None:
    list_path = output_path.with_suffix(".concat.txt")
    lines = [f"file '{clip.resolve().as_posix()}'" for clip in clips]
    list_path.write_text("\n".join(lines), encoding="utf-8")
    if not clips:
        raise RuntimeError("No clips were provided for final assembly.")

    inputs: list[str] = []
    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    for index, clip in enumerate(clips):
        inputs.extend(["-i", str(clip)])
        filter_parts.append(
            f"[{index}:v]setpts=PTS-STARTPTS,scale=1280:720:force_original_aspect_ratio=increase,"
            f"crop=1280:720,fps={fps},format=yuv420p[v{index}]"
        )
        filter_parts.append(f"[{index}:a]asetpts=PTS-STARTPTS,aresample=44100[a{index}]")
        concat_inputs.append(f"[v{index}][a{index}]")
    filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(clips)}:v=1:a=1[v][a]")

    run_ffmpeg(
        [
            *inputs,
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "16",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )


def _make_blackfiles_chapter_card(output_path: Path, *, title: str, prefix: str, duration: float, fps: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    title = _clean_chapter_text(title) or _clean_chapter_display_text(title)
    ass_path = output_path.with_suffix(".ass")
    audio_path = output_path.with_suffix(".wav")
    _write_blackfiles_chapter_ass(ass_path, title=title, prefix=prefix, duration=duration)
    _make_chapter_card_sfx(audio_path, duration)
    ass_filter_path = ass_path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")
    vf = (
        "format=yuv420p,"
        "geq=r='6+42/(1+((X-655)*(X-655)+(Y-350)*(Y-350))/13500)+22/(1+((X-760)*(X-760)+(Y-260)*(Y-260))/6500)+10*random(1)':"
        "g='8+50/(1+((X-650)*(X-650)+(Y-350)*(Y-350))/13000)+25/(1+((X-760)*(X-760)+(Y-260)*(Y-260))/6200)+10*random(2)':"
        "b='10+66/(1+((X-645)*(X-645)+(Y-350)*(Y-350))/12500)+31/(1+((X-760)*(X-760)+(Y-260)*(Y-260))/6000)+12*random(3)',"
        "gblur=sigma=1.4,"
        "eq=contrast=1.24:brightness=-0.16:saturation=0.50,"
        "drawgrid=w=1280:h=3:t=1:c=white@0.105,"
        "drawgrid=w=4:h=720:t=1:c=cyan@0.018,"
        "drawbox=x=0:y=0:w=iw:h=ih:color=black@0.22:t=fill:enable='lt(mod(t,0.31),0.026)',"
        "vignette=PI/2.55,"
        f"ass='{ass_filter_path}'"
    )
    run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=1280x720:r={fps}:d={duration:.3f}",
            "-i",
            str(audio_path),
            "-vf",
            vf,
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "16",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )


def _write_blackfiles_chapter_ass(path: Path, *, title: str, prefix: str, duration: float) -> None:
    title_text, title_size = _chapter_title_layout(_clean_chapter_display_text(title))
    prefix = _clean_chapter_display_text(prefix)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1280\n"
        "PlayResY: 720\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Prefix,Georgia,36,&H00F2F2F2,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
        f"Style: TitleGlow,Georgia,{title_size},&H001DFF1D,&H000000FF,&H00000000,&H00000000,-1,1,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n"
        f"Style: Title,Georgia,{title_size},&H0028FF28,&H000000FF,&H00000000,&H00000000,-1,1,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = [
        _ass_dialogue(0.30, duration - 0.25, "Prefix", rf"{{\fad(100,180)\blur1.2\an7\pos(348,252)}}{_ass_text(prefix)}"),
        _ass_dialogue(0.38, 0.49, "TitleGlow", rf"{{\blur20\alpha&H72&\an5\pos(640,374)\fscx104\fscy104}}{title_text}"),
        _ass_dialogue(0.49, 0.60, "TitleGlow", rf"{{\blur22\alpha&H78&\an5\pos(650,367)\fscx98\fscy98}}{title_text}"),
        _ass_dialogue(0.60, duration - 0.22, "TitleGlow", rf"{{\fad(70,260)\blur22\alpha&H78&\an5\pos(640,372)}}{title_text}"),
        _ass_dialogue(0.60, duration - 0.22, "Title", rf"{{\fad(70,260)\blur1\an5\pos(640,372)}}{title_text}"),
    ]
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def _make_chapter_card_sfx(output_path: Path, duration: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        f"anoisesrc=color=pink:duration={duration}:sample_rate=44100,highpass=f=650,lowpass=f=6000,volume=0.055[n];"
        "sine=frequency=62:duration=0.45,volume=0.23,afade=t=out:st=0.08:d=0.34,adelay=340|340[b];"
        "anoisesrc=color=white:duration=0.08:sample_rate=44100,highpass=f=1600,lowpass=f=7800,volume=0.22,adelay=470|470[g1];"
        "anoisesrc=color=white:duration=0.045:sample_rate=44100,highpass=f=2100,lowpass=f=8200,volume=0.18,adelay=580|580[g2];"
        f"[n][b][g1][g2]amix=inputs=4:duration=longest,afade=t=out:st={max(0.0, duration - 0.35):.3f}:d=0.30[out]"
    )
    run_ffmpeg(["-filter_complex", filter_complex, "-map", "[out]", "-t", f"{duration:.3f}", "-c:a", "pcm_s16le", str(output_path)])


def _clean_chapter_display_text(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 :,'-]+", "", value).strip()
    return " ".join(cleaned.split())[:90]


def _chapter_title_layout(title: str) -> tuple[str, int]:
    words = title.split()
    if not words:
        return "", 92

    lines = _wrap_chapter_title_words(words)
    longest = max(len(line) for line in lines)
    if longest <= 13 and len(lines) <= 2:
        size = 92
    elif longest <= 17 and len(lines) <= 2:
        size = 82
    elif longest <= 22:
        size = 72
    else:
        size = 62
    return "\\N".join(_ass_text(line) for line in lines), size


def _wrap_chapter_title_words(words: list[str]) -> list[str]:
    if len(words) == 1:
        return words
    total_chars = sum(len(word) for word in words) + len(words) - 1
    if total_chars <= 14:
        return [" ".join(words)]

    best_lines: list[str] | None = None
    best_score: float | None = None
    max_lines = 2 if total_chars <= 32 else 3

    def visit(start: int, lines: list[str]) -> None:
        nonlocal best_lines, best_score
        remaining = len(words) - start
        remaining_slots = max_lines - len(lines)
        if remaining == 0:
            lengths = [len(line) for line in lines]
            overflow = max(0, max(lengths) - 22)
            balance = max(lengths) - min(lengths)
            score = overflow * 100 + balance + len(lines) * 1.5
            if best_score is None or score < best_score:
                best_score = score
                best_lines = list(lines)
            return
        if remaining_slots <= 0 or remaining < remaining_slots:
            return
        for end in range(start + 1, len(words) + 1):
            line = " ".join(words[start:end])
            if len(line) > 24 and end < len(words):
                break
            visit(end, [*lines, line])

    visit(0, [])
    return best_lines or [" ".join(words)]


def _trim_to_duration(input_path: Path, output_path: Path, target_seconds: int) -> None:
    actual_duration = ffprobe_duration(input_path)
    if actual_duration <= target_seconds + 1.0:
        if input_path.resolve() != output_path.resolve():
            output_path.write_bytes(input_path.read_bytes())
        return
    run_ffmpeg(
        [
            "-i",
            str(input_path),
            "-t",
            f"{float(target_seconds):.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "16",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )


def _add_segment_transition_fades(input_path: Path, output_path: Path, *, fade_in: bool, fade_out: bool, transition: str) -> None:
    duration = ffprobe_duration(input_path)
    transition = _transition_type(transition)
    if transition == "hard_cut":
        output_path.write_bytes(input_path.read_bytes())
        return
    fade = 0.18 if transition in {"flash_click", "glitch_cut"} else min(0.36, max(0.16, duration * 0.045))
    video_filters = []
    if fade_in:
        video_filters.append(f"fade=t=in:st=0:d={fade:.3f}")
    if fade_out and duration > fade + 0.35:
        start = max(0.0, duration - fade)
        video_filters.append(f"fade=t=out:st={start:.3f}:d={fade:.3f}")
    if not video_filters:
        output_path.write_bytes(input_path.read_bytes())
        return
    command = ["-i", str(input_path)]
    if video_filters:
        command.extend(["-vf", ",".join(video_filters)])
    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "16",
            "-c:a",
            "copy",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )
    run_ffmpeg(command)


def _enhance_video(input_path: Path, output_path: Path) -> None:
    run_ffmpeg(
        [
            "-i",
            str(input_path),
            "-vf",
            "scale=1920:1080:flags=lanczos,unsharp=7:7:0.85:3:3:0.28,eq=contrast=1.06:saturation=1.05:brightness=0.005",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output_path),
        ]
    )


def _mux_video_audio(
    video_path: Path,
    narration_path: Path,
    sfx_path: Path,
    ambient_path: Path,
    output_path: Path,
    *,
    total_seconds: float,
) -> None:
    video_duration = ffprobe_duration(video_path)
    duration = max(1.0, float(total_seconds))
    stretch = max(1.0, duration / max(0.001, video_duration))
    run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-i",
            str(narration_path),
            "-i",
            str(sfx_path),
            "-i",
            str(ambient_path),
            "-filter_complex",
            (
                f"[0:v]setpts=({stretch:.8f})*(PTS-STARTPTS),"
                f"trim=duration={duration:.3f},setpts=PTS-STARTPTS[vout];"
                "[1:a]volume=1.0[a1];[2:a]volume=0.72[a2];[3:a]volume=0.55[a3];"
                "[a1][a2][a3]amix=inputs=3:duration=longest:normalize=0[aout]"
            ),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "16",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )


def _narration_script(plan: dict[str, Any]) -> str:
    return " ".join(str(beat.get("narration") or "").strip() for beat in plan["beats"] if str(beat.get("narration") or "").strip())


def _loads_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    candidates = [cleaned]
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    candidates.extend(_clean_common_json_mistakes(candidate) for candidate in list(candidates))

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise json.JSONDecodeError("No JSON object found", cleaned, 0)


def _clean_common_json_mistakes(text: str) -> str:
    cleaned = text.strip().replace("\ufeff", "")
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    cleaned = re.sub(r"\bNone\b", "null", cleaned)
    cleaned = re.sub(r"\bTrue\b", "true", cleaned)
    cleaned = re.sub(r"\bFalse\b", "false", cleaned)
    cleaned = re.sub(r"(?<=[{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", r'"\1":', cleaned)
    return cleaned


def _overlay_phrase_from_narration(narration: str, requested: str, duration: int) -> tuple[str, float]:
    words = re.findall(r"[A-Za-z0-9']+", narration)
    if len(words) < 2:
        return "", 1.0

    requested_words = re.findall(r"[A-Za-z0-9']+", requested)
    requested_index = _find_phrase(words, requested_words)
    if requested_index is not None:
        raw_phrase_words = words[requested_index : requested_index + len(requested_words)]
        leading_trim = 0
        while leading_trim < len(raw_phrase_words) and raw_phrase_words[leading_trim].upper() in BAD_OVERLAY_END_WORDS:
            leading_trim += 1
        phrase_words = _trim_overlay_phrase(raw_phrase_words)
        if 1 <= len(phrase_words) <= 3 and not _ends_badly(phrase_words):
            return _clean_overlay_text(" ".join(phrase_words)), _spoken_time(requested_index + leading_trim, len(words), duration)

    candidates: list[tuple[int, list[str]]] = []
    offset = 0
    for segment in _narration_word_segments(narration):
        for segment_start, phrase_words in _phrase_candidates(segment):
            candidates.append((offset + segment_start, phrase_words))
        offset += len(segment)
    if not candidates:
        candidates = _phrase_candidates(words)
    phrase_start, phrase_words = candidates[0]
    return _clean_overlay_text(" ".join(phrase_words)), _spoken_time(phrase_start, len(words), duration)


def _narration_word_segments(narration: str) -> list[list[str]]:
    segments = []
    for part in re.split(r"[.!?;:]+", narration):
        words = re.findall(r"[A-Za-z0-9']+", part)
        if len(words) >= 2:
            segments.append(words)
    return segments


def _phrase_candidates(words: list[str]) -> list[tuple[int, list[str]]]:
    stop_words = {
        "THE",
        "A",
        "AN",
        "AND",
        "OR",
        "BUT",
        "BECAUSE",
        "WITH",
        "FROM",
        "THIS",
        "THAT",
        "THEY",
        "THEIR",
        "EVERYONE",
    }
    candidates = []
    for size in (3, 2, 1):
        for start in range(0, max(0, len(words) - size + 1)):
            phrase = _trim_overlay_phrase(words[start : start + size])
            if len(phrase) < 1 or _ends_badly(phrase) or not _phrase_words_are_valid(phrase):
                continue
            score = sum(1 for word in phrase if word.upper() not in stop_words and len(word) > 3)
            score += sum(1 for word in phrase if len(word) >= 7)
            score += 1 if len(phrase) <= 2 else 0
            if score >= 1:
                candidates.append((score, start, phrase))
    if not candidates:
        fallback = _trim_overlay_phrase(words[: min(3, len(words))])
        return [(0, fallback if fallback else words[:1])]
    candidates.sort(key=lambda item: (-item[0], abs(item[1] - len(words) * 0.45)))
    return [(start, phrase) for _, start, phrase in candidates]


def _trim_overlay_phrase(words: list[str]) -> list[str]:
    phrase = [word for word in words if word]
    while phrase and phrase[0].upper() in BAD_OVERLAY_START_WORDS:
        phrase = phrase[1:]
    while phrase and phrase[-1].upper() in BAD_OVERLAY_END_WORDS:
        phrase = phrase[:-1]
    return phrase[:3]


def _ends_badly(words: list[str]) -> bool:
    return bool(words and words[-1].upper() in BAD_OVERLAY_END_WORDS)


def _phrase_words_are_valid(words: list[str]) -> bool:
    if _overlay_phrase_is_fragment(words):
        return False
    for word in words:
        cleaned = _normalize_word(word).upper()
        if cleaned in DISALLOWED_OVERLAY_WORDS:
            return False
        if len(cleaned) <= 2 and cleaned not in {"NO"}:
            return False
    return any(len(_normalize_word(word)) >= 5 for word in words)


def _overlay_phrase_is_fragment(words: list[str]) -> bool:
    cleaned_words = [str(word or "").strip().upper() for word in words if str(word or "").strip()]
    if not cleaned_words:
        return True
    if cleaned_words[-1].endswith("'S") or cleaned_words[-1] in {"SINGLE", "EVERY", "ANY", "SOME"}:
        return True
    if cleaned_words[0] in {"NEVER", "WITHOUT"}:
        return True
    if len(cleaned_words) >= 2 and cleaned_words[-2:] in (["A", "SINGLE"], ["THE", "ONLY"]):
        return True
    return False


def _find_phrase(words: list[str], phrase: list[str]) -> int | None:
    if not phrase:
        return None
    lowered = [word.lower() for word in words]
    target = [word.lower() for word in phrase]
    for index in range(0, len(lowered) - len(target) + 1):
        if lowered[index : index + len(target)] == target:
            return index
    return None


def _spoken_time(word_index: int, word_count: int, duration: int) -> float:
    if word_count <= 0:
        return 1.0
    return max(0.25, min(duration - 0.75, (word_index / word_count) * duration - 0.05))


def _clean_overlay_text(text: str) -> str:
    cleaned = " ".join(text.upper().split())
    cleaned = re.sub(r"[^A-Z0-9 ?!,'-]", "", cleaned)
    words = cleaned.split()
    while words and words[0] in BAD_OVERLAY_START_WORDS:
        words.pop(0)
    while words and words[-1] in BAD_OVERLAY_END_WORDS:
        words.pop()
    words = [word for word in words if word not in DISALLOWED_OVERLAY_WORDS and (len(word) > 2 or word == "NO")]
    if _overlay_phrase_is_fragment(words):
        return ""
    return " ".join(words[:3])


def _clean_overlay_text_for_kind(text: str, kind: str) -> str:
    if kind == "chapter_card":
        return _clean_chapter_text(text)
    if kind == "sequence":
        return _clean_overlay_text_limited(text, 5, strip_bad_end=True, keep_short_words=True)
    if kind in {"location", "date"}:
        return _clean_overlay_text_limited(text, 4, strip_bad_end=False, keep_short_words=True)
    return _clean_overlay_text(text)


def _clean_overlay_text_limited(
    text: str,
    max_words: int,
    *,
    strip_bad_end: bool = True,
    keep_short_words: bool = False,
) -> str:
    cleaned = " ".join(text.upper().split())
    cleaned = re.sub(r"[^A-Z0-9 ?!,'-]", "", cleaned)
    words = cleaned.split()
    while strip_bad_end and words and words[0] in BAD_OVERLAY_START_WORDS:
        words.pop(0)
    while strip_bad_end and words and words[-1] in BAD_OVERLAY_END_WORDS:
        words.pop()
    words = [
        word
        for word in words
        if word not in DISALLOWED_OVERLAY_WORDS and (keep_short_words or len(word) > 2 or word == "NO")
    ]
    if strip_bad_end and _overlay_phrase_is_fragment(words):
        return ""
    return " ".join(words[:max_words])


def _clean_chapter_text(text: str) -> str:
    cleaned = " ".join(_strip_chapter_prefix(text).upper().split())
    cleaned = re.sub(r"[^A-Z0-9 ?!,'-]", "", cleaned)
    return " ".join(cleaned.split()[:8])


def _strip_chapter_prefix(text: str) -> str:
    cleaned = " ".join(str(text or "").replace("\\", " ").split())
    cleaned = re.sub(
        r"^\s*(?:chapter|part|act)\s+(?:[ivxlcdm]+|\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s*[:.\-–—]?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^\s*(?:chapter|part|act)\s*[:.\-–—]?\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _overlay_effect(effect: str) -> str:
    cleaned = effect.strip().lower()
    return cleaned if cleaned in {"fade", "pop", "typewriter", "glitch"} else "fade"


def _overlay_style(style: str) -> str:
    cleaned = style.strip().lower()
    return cleaned if cleaned in {"block", "serif"} else "serif"


def _overlay_color_name(color: str) -> str:
    cleaned = color.strip().lower()
    return cleaned if cleaned in {"yellow", "green", "red", "white"} else "yellow"


def _overlay_size_name(size: str) -> str:
    cleaned = size.strip().lower()
    return cleaned if cleaned in {"small", "medium", "large", "huge"} else "large"


def _overlay_position(position: str) -> str:
    cleaned = position.strip().lower()
    return cleaned if cleaned in SAFE_POSITIONS else "center"


def _overlay_font(font: str) -> str:
    cleaned = font.strip().lower().replace(" ", "_")
    return FONT_REGISTRY.get(cleaned, "")


def _overlay_sfx(sfx: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_ -]+", "", sfx.strip().lower())
    cleaned = re.sub(r"[\s-]+", "_", cleaned).strip("_")
    if not cleaned:
        return ""
    if cleaned in {"camera_click", "typewriter", "glitch", "boom", "whoosh", "none"}:
        return cleaned
    return cleaned[:48]


def _overlay_opacity(value: Any) -> float:
    try:
        opacity = float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.35, min(1.0, opacity))


def _overlay_dim_background(value: Any, kind: str) -> bool:
    if kind == "location":
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"true", "yes", "1", "on"}:
            return True
        if cleaned in {"false", "no", "0", "off"}:
            return False
    return kind in {"date", "sequence", "chapter_card", "spoken", "directed", ""}


def _overlay_dim_opacity(value: Any) -> float:
    try:
        opacity = float(value)
    except (TypeError, ValueError):
        return 0.42
    return max(0.0, min(0.62, opacity))


def _transition_type(value: str) -> str:
    cleaned = value.strip().lower()
    return cleaned if cleaned in TRANSITION_TYPES else "hard_cut"


def _ambient_bed(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_ -]+", "", value.strip().lower())
    cleaned = re.sub(r"[\s-]+", "_", cleaned).strip("_")
    if not cleaned:
        return "none"
    return cleaned[:48]


def _overlay_palette(color: str) -> tuple[str, str]:
    return {
        "yellow": ("#f9ec63", "#f3e85d"),
        "green": ("#29ff22", "#19f014"),
        "red": ("#ff3b36", "#ff1e1e"),
    }.get(color, ("#f9ec63", "#f3e85d"))


def _font_size(text: str, *, style: str, size: str) -> int:
    base = {
        "small": 28,
        "medium": 56,
        "large": 72,
        "huge": 104,
    }.get(size, 72)
    if style == "serif":
        base = int(base * 0.88)
    if len(text) > 32:
        base = int(base * 0.68)
    elif len(text) > 22:
        base = int(base * 0.8)
    return max(22, base)


def _overlay_y(position: str) -> str:
    if position == "top":
        return "h*0.18"
    if position == "bottom":
        return "h*0.72"
    return "(h-text_h)/2"


def _animated_overlay_y(base_y: str, start: float, effect: str) -> str:
    if effect == "pop":
        return f"({base_y})+if(lt(t,{start + 0.12:.3f}),18*(1-(t-{start:.3f})/0.12),0)"
    if effect == "typewriter":
        return f"({base_y})+if(lt(t,{start + 0.18:.3f}),10*(1-(t-{start:.3f})/0.18),0)"
    return base_y


def _font_file(style: str) -> str:
    if style == "block":
        candidates = [
            "C:/Windows/Fonts/impact.ttf",
            "C:/Windows/Fonts/ariblk.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ]
    else:
        candidates = [
            "C:/Windows/Fonts/georgiab.ttf",
            "C:/Windows/Fonts/Georgia.ttf",
            "C:/Windows/Fonts/timesbd.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate.replace(":", "\\:")
    return "C\\:/Windows/Fonts/arialbd.ttf"


def _ffmpeg_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


if __name__ == "__main__":
    main(sys.argv[1:])
