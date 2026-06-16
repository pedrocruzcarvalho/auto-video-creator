from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from . import final_assembler, grid_intro, image_gen, scene_assembler, script_gen, tts
from .config import load_config, load_environment
from .media import ensure_media_tools
from .paths import ensure_run_dirs, run_dir
from .progress import ProgressCallback, emit


WORKERS = [
    "Research / Topic Planner",
    "Script Writer",
    "Visual Director",
    "Asset Generator",
    "Video Clip Generator",
    "Voice Generator",
    "Motion Graphics / Editor",
    "Final Assembler",
]


@dataclass(frozen=True)
class FernRunOptions:
    topic: str
    target_minutes: float
    max_budget_usd: float
    run_id: str
    style_preset: str = "Fern-style AI documentary"
    quality_mode: str = "balanced"
    max_generated_video_clips: int = 3
    max_generated_stills: int = 12
    call_replicate: bool = False
    resume: bool = True


def run_fern_pipeline(options: FernRunOptions, *, progress_callback: ProgressCallback | None = None) -> dict[str, Any]:
    load_environment()
    config = load_config()
    _apply_fern_overrides(config, options)
    output_dir = ensure_run_dirs(options.run_id)

    for worker in WORKERS:
        emit(progress_callback, worker, "waiting", "Queued")

    try:
        emit(progress_callback, "Research / Topic Planner", "running", "Preparing the run folder and budget envelope", progress=0.08)
        if options.call_replicate:
            ensure_media_tools()
        emit(progress_callback, "Research / Topic Planner", "done", "Run setup complete", progress=1, artifact_path=output_dir)

        plan = _load_or_create_plan(options, config, progress_callback=progress_callback)
        plan_path = run_dir(options.run_id) / "fern_plan.json"
        plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

        if not options.call_replicate:
            _simulate_dry_run_outputs(options, plan, progress_callback=progress_callback)
            return {
                "run_id": options.run_id,
                "run_dir": str(output_dir),
                "plan_path": str(plan_path),
                "plan": plan,
                "final_path": None,
                "dry_run": True,
            }

        scenes, visual_assets = _plan_to_existing_scenes(plan, options, config)
        script_budget = _image_budget_for_plan(plan, config)
        script_gen.save_script(options.topic, scenes, options.run_id, visual_assets=visual_assets, image_budget=script_budget)

        emit(progress_callback, "Asset Generator", "running", "Generating reusable references and still plates with Replicate", progress=0.02)
        image_assets = image_gen.generate_assets(
            scenes,
            options.run_id,
            config=config,
            mock=False,
            visual_assets=visual_assets,
        )
        preview = _first_existing_path(image_assets.get("intro", []))
        emit(
            progress_callback,
            "Asset Generator",
            "done",
            "Reference images and still plates are ready",
            progress=1,
            artifact_path=run_dir(options.run_id) / "images",
            preview_path=preview,
        )

        _write_video_clip_manifest(options, plan, generated=False, progress_callback=progress_callback)

        emit(progress_callback, "Voice Generator", "running", "Generating narration audio scene by scene", progress=0.03)
        audio_results = []
        for index, scene in enumerate(scenes, start=1):
            audio_results.append(
                tts.generate_audio(str(scene["text"]), int(scene["id"]), options.run_id, config=config, mock=False)
            )
            emit(
                progress_callback,
                "Voice Generator",
                "running",
                f"Generated narration audio {index}/{len(scenes)}",
                progress=index / max(1, len(scenes)),
                artifact_path=audio_results[-1].path,
            )
        emit(progress_callback, "Voice Generator", "done", "Narration audio is ready", progress=1, artifact_path=run_dir(options.run_id) / "audio")

        emit(progress_callback, "Motion Graphics / Editor", "running", "Building the intro grid and animated still scenes", progress=0.05)
        intro_path = None
        if config.get("intro", {}).get("enabled", True):
            intro_path = grid_intro.build(
                image_assets["intro"],
                topic=options.topic,
                run_id=options.run_id,
                config=config,
                labels=[str(scene.get("box_title") or f"Scene {scene['id']}") for scene in scenes],
            )
            emit(
                progress_callback,
                "Motion Graphics / Editor",
                "running",
                "Intro grid rendered",
                progress=0.25,
                artifact_path=intro_path,
                preview_path=image_assets["intro"][0] if image_assets.get("intro") else None,
            )

        scene_clips = []
        for index, (scene, audio_result) in enumerate(zip(scenes, audio_results), start=1):
            scene_id = int(scene["id"])
            scene_clips.append(
                scene_assembler.assemble(
                    image_paths=image_assets["shots"][scene_id],
                    shots=scene.get("shots", []),
                    audio_path=audio_result.path,
                    word_timestamps_path=audio_result.word_timestamps_path,
                    scene_id=scene_id,
                    run_id=options.run_id,
                    config=config,
                )
            )
            emit(
                progress_callback,
                "Motion Graphics / Editor",
                "running",
                f"Rendered edited scene {index}/{len(scenes)}",
                progress=0.25 + 0.7 * (index / max(1, len(scenes))),
                artifact_path=scene_clips[-1],
            )
        emit(progress_callback, "Motion Graphics / Editor", "done", "Edited scene clips are ready", progress=1, artifact_path=run_dir(options.run_id) / "clips")

        emit(progress_callback, "Final Assembler", "running", "Combining intro and edited scenes into final MP4", progress=0.15)
        clips = [intro_path, *scene_clips] if intro_path else scene_clips
        final_path = final_assembler.assemble([clip for clip in clips if clip], options.run_id, config=config)
        emit(progress_callback, "Final Assembler", "done", "Final video assembled", progress=1, artifact_path=final_path, preview_path=final_path)

        return {
            "run_id": options.run_id,
            "run_dir": str(output_dir),
            "plan_path": str(plan_path),
            "plan": plan,
            "final_path": str(final_path),
            "dry_run": False,
        }
    except Exception as exc:
        emit(progress_callback, "Final Assembler", "failed", "Pipeline failed", progress=0, error=f"{type(exc).__name__}: {exc}")
        raise


def _apply_fern_overrides(config: dict[str, Any], options: FernRunOptions) -> None:
    fern = config.setdefault("fern", {})
    quality = str(options.quality_mode or "balanced").lower()
    quality_profiles = fern.get("quality_profiles", {})
    profile = quality_profiles.get(quality, quality_profiles.get("balanced", {}))

    config.setdefault("script", {})["format"] = "fern_documentary"
    config["script"]["target_scene_count"] = int(profile.get("scene_count", fern.get("default_scene_count", 3)))
    config["script"]["target_word_count"] = int(max(80, options.target_minutes * 150))
    config["script"]["target_seconds_per_scene"] = int(max(30, options.target_minutes * 60 / max(1, config["script"]["target_scene_count"])))
    config["script"]["max_final_images_per_scene"] = max(1, int(options.max_generated_stills / max(1, config["script"]["target_scene_count"])))
    config.setdefault("hybrid", {})["enabled"] = False
    config.setdefault("image", {})["max_run_image_cost_usd"] = max(0.01, min(options.max_budget_usd, _image_budget_share(config, options)))
    config["image"]["style"] = options.style_preset
    config.setdefault("intro", {})["enabled"] = True


def _image_budget_share(config: dict[str, Any], options: FernRunOptions) -> float:
    fern = config.get("fern", {})
    share = float(fern.get("image_budget_fraction", 0.35))
    return options.max_budget_usd * share


def _load_or_create_plan(
    options: FernRunOptions,
    config: dict[str, Any],
    *,
    progress_callback: ProgressCallback | None,
) -> dict[str, Any]:
    plan_path = run_dir(options.run_id) / "fern_plan.json"
    if options.resume and plan_path.exists():
        emit(progress_callback, "Script Writer", "done", "Reusing existing Fern plan JSON", progress=1, artifact_path=plan_path)
        emit(progress_callback, "Visual Director", "done", "Loaded existing beat and asset plan", progress=1, artifact_path=plan_path)
        return json.loads(plan_path.read_text(encoding="utf-8"))

    emit(progress_callback, "Script Writer", "running", "Writing narration, structure, and beat timeline", progress=0.15)
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            plan = _generate_claude_plan(options, config)
        except Exception:
            if options.call_replicate:
                raise
            emit(
                progress_callback,
                "Script Writer",
                "running",
                "Claude planning was unavailable; using the offline mock planner for this dry-run",
                progress=0.5,
            )
            plan = _mock_plan(options, config)
    else:
        plan = _mock_plan(options, config)
    plan = _normalize_plan(plan, options, config)
    emit(progress_callback, "Script Writer", "done", "Narration and documentary structure are ready", progress=1, artifact_path=plan_path)
    emit(progress_callback, "Visual Director", "done", "Visual treatment, clip choices, stills, and overlays are planned", progress=1, artifact_path=plan_path)
    return plan


def _generate_claude_plan(options: FernRunOptions, config: dict[str, Any]) -> dict[str, Any]:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("Install anthropic first: python -m pip install -r requirements.txt") from exc

    fern = config.get("fern", {})
    costs = _unit_costs(config, options)
    model = str(fern.get("planner_model") or config.get("script", {}).get("model") or "claude-sonnet-4-6")
    scene_count = int(config.get("script", {}).get("target_scene_count", 3))
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=int(fern.get("planner_max_tokens", 6000)),
        temperature=float(fern.get("planner_temperature", 0.65)),
        system=(
            "You are a documentary showrunner and AI production planner. "
            "Return only valid JSON. Do not use markdown."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Plan a {options.target_minutes:.1f}-minute {options.style_preset} video about {options.topic!r}.\n"
                    f"Budget cap: ${options.max_budget_usd:.2f}. Quality mode: {options.quality_mode}.\n"
                    f"Hard caps: at most {options.max_generated_video_clips} generated AI video clips and "
                    f"at most {options.max_generated_stills} generated still/reference images.\n"
                    f"Use about {scene_count} major acts/scenes.\n"
                    "Use a hybrid Fern/Blackfiles-style workflow: AI video clips only for key dramatic moments; "
                    "generated stills/reference images for recurring doll-like characters and environments; "
                    "code-made motion graphics for maps, timelines, documents, charts, dossiers, warnings, date cards, "
                    "parallax moves, zooms, subtitles, callouts, and archive-style panels.\n"
                    "Choose how many beats, clips, stills, and motion graphics are actually needed to stay under budget. "
                    "Do not generate AI video for every second.\n"
                    "Keep visual model/style consistent across the run unless an override is explicit.\n"
                    "Avoid real-person likenesses. Use anonymous editorial doll/cutout character references.\n"
                    "Return this exact JSON shape:\n"
                    "{"
                    '"topic":"...", "run_id":"...", "style_preset":"...", "quality_mode":"...", '
                    '"target_video_length_minutes":0, "narration_script":"...", '
                    '"model_consistency":{"image_model":"...","video_model":"...","style_lock":"..."}, '
                    '"budget_plan":{"max_budget_usd":0,"estimated_total_usd":0,"image_usd":0,"video_usd":0,"tts_usd":0,"motion_graphics_usd":0,"notes":["..."]}, '
                    '"visual_assets":{"backgrounds":[{"id":"bg_...","description":"...","image_prompt":"..."}],'
                    '"characters":[{"id":"char_...","description":"...","image_prompt":"..."}],'
                    '"props":[{"id":"prop_...","description":"...","image_prompt":"..."}]}, '
                    '"beats":[{"id":"beat_01","act":1,"title":"...","start_seconds":0,"end_seconds":10,'
                    '"narration":"spoken narration for this beat","visual_type":"generated_still|generated_video|motion_graphic",'
                    '"visual_direction":"...","image_prompt":"...","video_prompt":"...","motion_graphic":"...",'
                    '"overlay_text":["..."],"callouts":["..."],"reference_asset_ids":["bg_...","char_..."]}], '
                    '"generated_stills":[{"id":"still_01","beat_id":"beat_01","kind":"character|environment|document|stage","prompt":"..."}], '
                    '"generated_video_clips":[{"id":"clip_01","beat_id":"beat_02","duration_seconds":4,"prompt":"...","fallback_still_prompt":"..."}], '
                    '"motion_graphics":[{"id":"mg_01","beat_id":"beat_03","kind":"map|timeline|chart|document|warning|dossier","instruction":"..."}]'
                    "}\n"
                    f"Use these unit estimates: image request ${costs['image_request_usd']:.3f}, "
                    f"AI video second ${costs['video_second_usd']:.3f}, TTS minute ${costs['tts_minute_usd']:.3f}.\n"
                    "Every image/video prompt must include the style lock and must avoid readable text/logos/watermarks. "
                    "Overlay text belongs only in overlay_text/callouts, not inside image prompts."
                ),
            }
        ],
    )
    text = response.content[0].text.strip()
    return _loads_json_object(text)


def _normalize_plan(plan: dict[str, Any], options: FernRunOptions, config: dict[str, Any]) -> dict[str, Any]:
    plan["topic"] = str(plan.get("topic") or options.topic)
    plan["run_id"] = options.run_id
    plan["style_preset"] = str(plan.get("style_preset") or options.style_preset)
    plan["quality_mode"] = str(plan.get("quality_mode") or options.quality_mode)
    plan["target_video_length_minutes"] = float(plan.get("target_video_length_minutes") or options.target_minutes)
    plan["visual_assets"] = _normalize_visual_assets(plan.get("visual_assets"))
    plan["beats"] = _normalize_beats(plan.get("beats"), options)
    plan["generated_video_clips"] = _limit_list(plan.get("generated_video_clips"), options.max_generated_video_clips)
    plan["generated_stills"] = _limit_list(plan.get("generated_stills"), options.max_generated_stills)
    plan["motion_graphics"] = _list_of_dicts(plan.get("motion_graphics"))
    plan["budget_plan"] = _estimated_budget(plan, options, config)
    if not str(plan.get("narration_script") or "").strip():
        plan["narration_script"] = "\n\n".join(str(beat.get("narration") or "") for beat in plan["beats"])
    plan.setdefault(
        "model_consistency",
        {
            "image_model": config.get("image", {}).get("model", "replicate image model"),
            "video_model": config.get("fern", {}).get("video_model", "TODO: configure Replicate video model"),
            "style_lock": options.style_preset,
        },
    )
    return plan


def _normalize_visual_assets(raw: Any) -> dict[str, list[dict[str, str]]]:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "backgrounds": _asset_group(raw.get("backgrounds"), "bg"),
        "characters": _asset_group(raw.get("characters"), "char"),
        "props": _asset_group(raw.get("props"), "prop"),
    }


def _asset_group(raw: Any, prefix: str) -> list[dict[str, str]]:
    assets = []
    for index, item in enumerate(_list_of_dicts(raw), start=1):
        asset_id = _safe_id(str(item.get("id") or f"{prefix}_{index:02d}"), prefix=prefix)
        description = str(item.get("description") or asset_id).strip()
        prompt = str(item.get("image_prompt") or item.get("prompt") or description).strip()
        assets.append({"id": asset_id, "description": description, "image_prompt": prompt})
    return assets


def _normalize_beats(raw: Any, options: FernRunOptions) -> list[dict[str, Any]]:
    beats = _list_of_dicts(raw)
    if not beats:
        beats = _mock_plan(options, {})["beats"]
    normalized = []
    target_seconds = max(15, int(options.target_minutes * 60))
    beat_duration = target_seconds / max(1, len(beats))
    for index, beat in enumerate(beats, start=1):
        start = float(beat.get("start_seconds", (index - 1) * beat_duration))
        end = float(beat.get("end_seconds", index * beat_duration))
        visual_type = str(beat.get("visual_type") or "motion_graphic").strip().lower()
        if visual_type not in {"generated_still", "generated_video", "motion_graphic"}:
            visual_type = "motion_graphic"
        normalized.append(
            {
                "id": str(beat.get("id") or f"beat_{index:02d}"),
                "act": max(1, int(float(beat.get("act") or 1))),
                "title": str(beat.get("title") or f"Beat {index}").strip(),
                "start_seconds": start,
                "end_seconds": max(end, start + 1),
                "narration": str(beat.get("narration") or "").strip(),
                "visual_type": visual_type,
                "visual_direction": str(beat.get("visual_direction") or "").strip(),
                "image_prompt": str(beat.get("image_prompt") or beat.get("fallback_still_prompt") or beat.get("visual_direction") or "").strip(),
                "video_prompt": str(beat.get("video_prompt") or "").strip(),
                "motion_graphic": str(beat.get("motion_graphic") or "").strip(),
                "overlay_text": _string_list(beat.get("overlay_text"))[:3],
                "callouts": _string_list(beat.get("callouts"))[:3],
                "reference_asset_ids": _string_list(beat.get("reference_asset_ids"))[:6],
            }
        )
    return normalized


def _estimated_budget(plan: dict[str, Any], options: FernRunOptions, config: dict[str, Any]) -> dict[str, Any]:
    costs = _unit_costs(config, options)
    visual_assets = plan.get("visual_assets", {})
    asset_count = sum(len(visual_assets.get(group, [])) for group in ("backgrounds", "characters", "props"))
    still_count = min(options.max_generated_stills, max(len(plan.get("generated_stills", [])), _count_generated_still_beats(plan)))
    video_seconds = sum(float(clip.get("duration_seconds") or 4) for clip in _list_of_dicts(plan.get("generated_video_clips")))
    image_usd = (asset_count + still_count) * costs["image_request_usd"]
    video_usd = video_seconds * costs["video_second_usd"]
    tts_usd = options.target_minutes * costs["tts_minute_usd"]
    motion_usd = float(config.get("fern", {}).get("motion_graphics_estimate_usd", 0.0))
    total = image_usd + video_usd + tts_usd + motion_usd
    return {
        "max_budget_usd": round(options.max_budget_usd, 4),
        "estimated_total_usd": round(total, 4),
        "image_usd": round(image_usd, 4),
        "video_usd": round(video_usd, 4),
        "tts_usd": round(tts_usd, 4),
        "motion_graphics_usd": round(motion_usd, 4),
        "asset_count": asset_count,
        "generated_still_count": still_count,
        "generated_video_seconds": round(video_seconds, 2),
        "within_budget": total <= options.max_budget_usd,
        "notes": [
            "Replicate video generation is planned but not called by this first implementation.",
            "Generated video beats fall back to still plates during assembly.",
        ],
    }


def _unit_costs(config: dict[str, Any], options: FernRunOptions) -> dict[str, float]:
    fern = config.get("fern", {})
    quality_costs = fern.get("quality_costs", {})
    mode_costs = quality_costs.get(options.quality_mode, quality_costs.get("balanced", {}))
    return {
        "image_request_usd": float(mode_costs.get("image_request_usd", config.get("image", {}).get("estimated_request_cost_usd", 0.039))),
        "video_second_usd": float(mode_costs.get("video_second_usd", fern.get("estimated_video_second_usd", 0.12))),
        "tts_minute_usd": float(mode_costs.get("tts_minute_usd", fern.get("estimated_tts_minute_usd", 0.02))),
    }


def _plan_to_existing_scenes(
    plan: dict[str, Any],
    options: FernRunOptions,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, str]]]]:
    acts: dict[int, list[dict[str, Any]]] = {}
    for beat in plan["beats"]:
        acts.setdefault(int(beat.get("act") or 1), []).append(beat)
    scenes = []
    max_still_shots = max(1, options.max_generated_stills)
    used_shots = 0
    for scene_index, act in enumerate(sorted(acts), start=1):
        beats = acts[act]
        scene_text = " ".join(str(beat.get("narration") or beat.get("title") or "") for beat in beats).strip()
        if not scene_text:
            scene_text = f"{options.topic}. Act {scene_index}."
        shots = []
        for beat in beats:
            if used_shots >= max_still_shots:
                continue
            shot_id = len(shots) + 1
            image_prompt = _beat_image_prompt(beat, options)
            shots.append(
                {
                    "id": shot_id,
                    "type": "image",
                    "text": str(beat.get("narration") or beat.get("title") or ""),
                    "image_prompt": image_prompt,
                    "replicate_prompt": image_prompt,
                    "shot_type": _shot_type_for_beat(beat),
                    "visual_energy": "high" if beat.get("visual_type") == "generated_video" else "medium",
                    "color_palette": ["red", "yellow", "blue"] if beat.get("visual_type") == "generated_video" else ["blue", "yellow", "black"],
                    "composition_style": str(beat.get("visual_direction") or beat.get("motion_graphic") or "documentary evidence board")[:180],
                    "composition": str(beat.get("visual_direction") or beat.get("motion_graphic") or ""),
                    "reference_asset_ids": _valid_reference_ids(beat, plan),
                    "callout": _callout_from_beat(beat),
                    "overlays": [],
                }
            )
            used_shots += 1
        if not shots:
            first = beats[0]
            shots.append(
                {
                    "id": 1,
                    "type": "image",
                    "text": scene_text,
                    "image_prompt": _beat_image_prompt(first, options),
                    "replicate_prompt": _beat_image_prompt(first, options),
                    "shot_type": "establishing",
                    "visual_energy": "medium",
                    "color_palette": ["blue", "yellow", "black"],
                    "composition_style": "documentary evidence board",
                    "composition": str(first.get("visual_direction") or ""),
                    "reference_asset_ids": _valid_reference_ids(first, plan),
                    "callout": _callout_from_beat(first),
                    "overlays": [],
                }
            )
        intro_prompt = _with_style(
            f"Broad ambient documentary thumbnail for {options.topic}, act {scene_index}: {beats[0].get('title', '')}. "
            "No title, no readable text, no logo, no watermark.",
            options,
        )
        scenes.append(
            {
                "id": scene_index,
                "box_title": str(beats[0].get("title") or f"Act {scene_index}")[:80],
                "text": scene_text,
                "intro_image_prompt": intro_prompt,
                "shots": shots,
                "duration_estimate": max(4, int((beats[-1]["end_seconds"] - beats[0]["start_seconds"]))),
            }
        )
    return scenes, plan["visual_assets"]


def _beat_image_prompt(beat: dict[str, Any], options: FernRunOptions) -> str:
    prompt = str(beat.get("image_prompt") or beat.get("visual_direction") or beat.get("motion_graphic") or beat.get("title") or "")
    if beat.get("visual_type") == "generated_video" and beat.get("video_prompt"):
        prompt = f"Fallback still plate for this planned AI video moment: {beat['video_prompt']}. {prompt}"
    return _with_style(f"{prompt}. No readable text, no logo, no watermark.", options)


def _with_style(prompt: str, options: FernRunOptions) -> str:
    style = options.style_preset
    if style.lower() not in prompt.lower():
        prompt = f"{prompt} Style lock: {style}."
    return prompt


def _valid_reference_ids(beat: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    known = set()
    for group in ("backgrounds", "characters", "props"):
        known.update(asset["id"] for asset in plan.get("visual_assets", {}).get(group, []))
    return [asset_id for asset_id in _string_list(beat.get("reference_asset_ids")) if asset_id in known]


def _callout_from_beat(beat: dict[str, Any]) -> dict[str, str] | None:
    texts = _string_list(beat.get("callouts")) or _string_list(beat.get("overlay_text"))
    if not texts:
        return None
    return {"text": texts[0][:40], "color": "yellow", "position": "top"}


def _shot_type_for_beat(beat: dict[str, Any]) -> str:
    visual_type = str(beat.get("visual_type") or "")
    if visual_type == "generated_video":
        return "escalation"
    if visual_type == "motion_graphic":
        return "diagram"
    return "establishing"


def _write_video_clip_manifest(
    options: FernRunOptions,
    plan: dict[str, Any],
    *,
    generated: bool,
    progress_callback: ProgressCallback | None,
) -> Path:
    emit(progress_callback, "Video Clip Generator", "running", "Preparing Replicate video clip manifest", progress=0.2)
    path = run_dir(options.run_id) / "video_clip_manifest.json"
    clips = _list_of_dicts(plan.get("generated_video_clips"))
    payload = {
        "generated": generated,
        "todo": "Wire a configured Replicate video model here. Current assembly uses fallback still plates.",
        "clips": clips[: options.max_generated_video_clips],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    emit(
        progress_callback,
        "Video Clip Generator",
        "done",
        "AI video clips are planned; real Replicate video calls are TODO in this pass",
        progress=1,
        artifact_path=path,
    )
    return path


def _simulate_dry_run_outputs(
    options: FernRunOptions,
    plan: dict[str, Any],
    *,
    progress_callback: ProgressCallback | None,
) -> None:
    steps = [
        ("Asset Generator", "Listing reference stills and environment plates", "asset_manifest.json"),
        ("Video Clip Generator", "Listing dramatic video moments without calling Replicate", "video_clip_manifest.json"),
        ("Voice Generator", "Estimating narration length without calling TTS", "voice_manifest.json"),
        ("Motion Graphics / Editor", "Planning maps, charts, panels, warnings, subtitles, and camera moves", "motion_graphics_manifest.json"),
        ("Final Assembler", "Dry-run complete; no final MP4 was rendered", "dry_run_summary.json"),
    ]
    for worker, message, filename in steps:
        emit(progress_callback, worker, "running", message, progress=0.2)
        time.sleep(0.12)
        path = run_dir(options.run_id) / filename
        path.write_text(json.dumps(_manifest_payload(filename, plan, options), indent=2, ensure_ascii=False), encoding="utf-8")
        emit(progress_callback, worker, "done", message, progress=1, artifact_path=path)


def _manifest_payload(filename: str, plan: dict[str, Any], options: FernRunOptions) -> dict[str, Any]:
    if filename == "asset_manifest.json":
        return {"visual_assets": plan.get("visual_assets", {}), "generated_stills": plan.get("generated_stills", [])}
    if filename == "video_clip_manifest.json":
        return {
            "generated": False,
            "clips": plan.get("generated_video_clips", [])[: options.max_generated_video_clips],
            "todo": "No Replicate video calls in dry-run.",
        }
    if filename == "voice_manifest.json":
        return {"estimated_minutes": options.target_minutes, "narration_script": plan.get("narration_script", "")}
    if filename == "motion_graphics_manifest.json":
        return {"motion_graphics": plan.get("motion_graphics", [])}
    return {"run_id": options.run_id, "budget_plan": plan.get("budget_plan", {}), "final_path": None, "dry_run": True}


def _mock_plan(options: FernRunOptions, config: dict[str, Any]) -> dict[str, Any]:
    target_seconds = int(max(30, options.target_minutes * 60))
    beat_count = max(4, min(10, int(options.target_minutes * 4)))
    beat_seconds = target_seconds / beat_count
    video_clip_count = min(options.max_generated_video_clips, max(1, beat_count // 4))
    beats = []
    for index in range(beat_count):
        visual_type = "generated_video" if index in {1, beat_count - 2} and video_clip_count > 0 else "motion_graphic"
        if visual_type == "generated_video":
            video_clip_count -= 1
        elif index % 3 == 0:
            visual_type = "generated_still"
        beats.append(
            {
                "id": f"beat_{index + 1:02d}",
                "act": min(3, 1 + int(index * 3 / beat_count)),
                "title": ["The Hook", "The Setup", "The Hidden System", "The Turn", "The Consequence"][index % 5],
                "start_seconds": round(index * beat_seconds, 2),
                "end_seconds": round((index + 1) * beat_seconds, 2),
                "narration": (
                    f"{options.topic} looks simple from far away, but the documentary version starts when one small detail "
                    "changes how every later decision makes sense."
                ),
                "visual_type": visual_type,
                "visual_direction": "layered evidence board with anonymous cutout figures, documents, map fragments, and stark warning panels",
                "image_prompt": f"archival-inspired documentary collage about {options.topic}, evidence board, anonymous figures, dramatic lighting",
                "video_prompt": f"slow cinematic push through a layered evidence board about {options.topic}, papers and map fragments shifting",
                "motion_graphic": "animated dossier panel with date card, map line, and highlighted document stack",
                "overlay_text": ["THE DETAIL EVERYONE MISSED"] if index == 0 else [],
                "callouts": ["TURNING POINT"] if visual_type == "generated_video" else [],
                "reference_asset_ids": ["bg_evidence_room", "char_host_doll", "prop_case_file"],
            }
        )
    return {
        "topic": options.topic,
        "run_id": options.run_id,
        "style_preset": options.style_preset,
        "quality_mode": options.quality_mode,
        "target_video_length_minutes": options.target_minutes,
        "narration_script": "\n\n".join(beat["narration"] for beat in beats),
        "model_consistency": {
            "image_model": config.get("image", {}).get("model", "google/nano-banana"),
            "video_model": config.get("fern", {}).get("video_model", "TODO"),
            "style_lock": options.style_preset,
        },
        "visual_assets": {
            "backgrounds": [
                {
                    "id": "bg_evidence_room",
                    "description": "Reusable dark evidence room with map wall and document table",
                    "image_prompt": f"empty archival evidence room, map wall, document table, {options.style_preset}, no readable text",
                }
            ],
            "characters": [
                {
                    "id": "char_host_doll",
                    "description": "Anonymous recurring host doll/cutout reference",
                    "image_prompt": f"anonymous editorial doll character reference, neutral outfit, {options.style_preset}, no likeness of a real person",
                }
            ],
            "props": [
                {
                    "id": "prop_case_file",
                    "description": "Reusable case file and warning document stack",
                    "image_prompt": f"case file prop reference, warning document stack, {options.style_preset}, no readable text",
                }
            ],
        },
        "beats": beats,
        "generated_stills": [
            {"id": "still_01", "beat_id": "beat_01", "kind": "environment", "prompt": f"evidence room plate for {options.topic}"},
            {"id": "still_02", "beat_id": "beat_03", "kind": "document", "prompt": f"document close-up plate for {options.topic}"},
        ][: options.max_generated_stills],
        "generated_video_clips": [
            {
                "id": "clip_01",
                "beat_id": "beat_02",
                "duration_seconds": 4,
                "prompt": f"dramatic slow push across evidence board about {options.topic}",
                "fallback_still_prompt": f"dramatic evidence-board still for {options.topic}",
            }
        ][: options.max_generated_video_clips],
        "motion_graphics": [
            {"id": "mg_01", "beat_id": "beat_03", "kind": "timeline", "instruction": "Animated date card and timeline reveal"},
            {"id": "mg_02", "beat_id": "beat_04", "kind": "dossier", "instruction": "Stacked document panel with parallax camera move"},
        ],
    }


def _image_budget_for_plan(plan: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    scene_count = max(1, len({int(beat.get("act") or 1) for beat in plan.get("beats", [])}))
    shot_count = max(1, _count_generated_still_beats(plan))
    return script_gen._image_budget(config, scene_count=scene_count, requested_shots_max=max(1, shot_count))


def _count_generated_still_beats(plan: dict[str, Any]) -> int:
    return sum(1 for beat in plan.get("beats", []) if beat.get("visual_type") in {"generated_still", "generated_video", "motion_graphic"})


def _loads_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    candidate = match.group(0) if match else cleaned
    return json.loads(candidate)


def _first_existing_path(paths: Any) -> Path | None:
    for path in paths or []:
        path = Path(path)
        if path.exists():
            return path
    return None


def _limit_list(value: Any, limit: int) -> list[dict[str, Any]]:
    return _list_of_dicts(value)[: max(0, int(limit))]


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _safe_id(value: str, *, prefix: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_ -]+", "", value.strip().lower())
    cleaned = re.sub(r"[\s-]+", "_", cleaned).strip("_") or f"{prefix}_asset"
    if not cleaned.startswith(f"{prefix}_"):
        cleaned = f"{prefix}_{cleaned}"
    return cleaned[:64]
