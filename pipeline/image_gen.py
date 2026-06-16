from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .paths import CACHE_DIR, ensure_cache_dirs, ensure_run_dirs, run_dir


def generate_all(
    scenes: list[dict[str, Any]],
    run_id: str,
    *,
    config: dict[str, Any],
    mock: bool = False,
) -> list[Path]:
    assets = generate_assets(scenes, run_id, config=config, mock=mock)
    return assets["intro"]


def generate_assets(
    scenes: list[dict[str, Any]],
    run_id: str,
    *,
    config: dict[str, Any],
    mock: bool = False,
    visual_assets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_run_dirs(run_id)
    max_workers = int(config["image"].get("max_workers", 3))
    hybrid_enabled = bool(config.get("hybrid", {}).get("enabled", False))
    if not mock:
        # Low-credit Replicate accounts can have a burst limit of 1 and 6 creates/min.
        # Sequential creation with spacing avoids losing the whole run to 429s.
        max_workers = min(max_workers, 1)
    visual_asset_paths = _generate_visual_asset_library(visual_assets or {}, run_id, config=config, mock=mock)
    jobs: list[tuple[str, str, str, bool, list[Path]]] = []
    for scene in scenes:
        scene_id = int(scene["id"])
        title = str(scene.get("box_title") or f"Box {scene_id}")
        add_intro_banner = bool(config["image"].get("add_intro_title_banner", False))
        jobs.append((f"intro_scene_{scene_id:02d}", scene["intro_image_prompt"], title, add_intro_banner, []))
        if hybrid_enabled and scene.get("stages"):
            for stage in scene.get("stages", []):
                stage_id = str(stage.get("id") or "stage")
                jobs.append(
                    (
                        _stage_image_key(scene_id, stage_id),
                        stage["image_prompt"],
                        title,
                        True,
                        _reference_paths(stage, visual_asset_paths),
                    )
                )
        else:
            for shot in scene.get("shots", []):
                shot_id = int(shot["id"])
                jobs.append(
                    (
                        f"scene_{scene_id:02d}_shot_{shot_id:02d}",
                        shot["image_prompt"],
                        title,
                        True,
                        _reference_paths(shot, visual_asset_paths),
                    )
                )

    if not mock:
        generated = _generate_assets_sequential(jobs, run_id, config=config)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    generate_image,
                    prompt,
                    image_key,
                    run_id,
                    config=config,
                    mock=mock,
                    title=title,
                    add_banner=add_banner,
                    reference_paths=reference_paths,
                )
                for image_key, prompt, title, add_banner, reference_paths in jobs
            ]
        generated = {image_key: future.result() for (image_key, _, _, _, _), future in zip(jobs, futures)}

    intro_paths = [generated[f"intro_scene_{int(scene['id']):02d}"] for scene in scenes]
    shot_paths: dict[int, list[Path]] = {}
    stage_paths: dict[int, dict[str, Path]] = {}
    for scene in scenes:
        scene_id = int(scene["id"])
        if hybrid_enabled and scene.get("stages"):
            stage_paths[scene_id] = {
                str(stage.get("id") or "stage"): generated[_stage_image_key(scene_id, str(stage.get("id") or "stage"))]
                for stage in scene.get("stages", [])
            }
            first_stage = next(iter(stage_paths[scene_id].values()))
            shot_paths[scene_id] = [
                stage_paths[scene_id].get(str(shot.get("stage_id") or ""), first_stage)
                for shot in scene.get("shots", [])
            ]
        else:
            shot_paths[scene_id] = [
                generated[f"scene_{scene_id:02d}_shot_{int(shot['id']):02d}"]
                for shot in scene.get("shots", [])
            ]
    return {"intro": intro_paths, "shots": shot_paths, "stages": stage_paths, "visual_assets": visual_asset_paths}


def _generate_visual_asset_library(
    visual_assets: dict[str, Any],
    run_id: str,
    *,
    config: dict[str, Any],
    mock: bool,
) -> dict[str, Path]:
    asset_paths: dict[str, Path] = {}
    if not visual_assets:
        return asset_paths

    asset_dir = run_dir(run_id) / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    groups = (
        ("backgrounds", "background"),
        ("characters", "character"),
        ("props", "prop"),
    )
    spacing = float(config["image"].get("request_spacing_seconds", 13))
    asset_jobs: list[tuple[str, str, str]] = []
    for group_name, key_prefix in groups:
        for asset in visual_assets.get(group_name, []) or []:
            if not isinstance(asset, dict):
                continue
            asset_id = str(asset.get("id") or "").strip()
            prompt = str(asset.get("image_prompt") or asset.get("prompt") or asset.get("description") or asset_id).strip()
            if not asset_id or not prompt:
                continue
            asset_jobs.append((asset_id, f"asset_{key_prefix}_{asset_id}", prompt))

    for index, (asset_id, image_key, prompt) in enumerate(asset_jobs, start=1):
        if not mock and index > 1:
            time.sleep(spacing)
        print(f"[{run_id}] Generating visual asset {index}/{len(asset_jobs)}: {asset_id}")
        generated_path = generate_image(
            prompt,
            image_key,
            run_id,
            config=config,
            mock=mock,
            title=asset_id,
            add_banner=False,
        )
        stored_path = asset_dir / f"{_safe_key(asset_id)}.png"
        shutil.copyfile(generated_path, stored_path)
        asset_paths[asset_id] = stored_path
    return asset_paths


def _reference_paths(item: dict[str, Any], asset_paths: dict[str, Path]) -> list[Path]:
    ids: list[str] = []
    prompt_text = " ".join(
        str(item.get(key) or "")
        for key in ("image_prompt", "replicate_prompt", "composition")
    ).lower()
    allow_characters = not re.search(r"\b(no characters?|no people|no person|empty background)\b", prompt_text)
    allow_props = not re.search(r"\b(no props?|without props?)\b", prompt_text)
    for key in ("background_id",):
        value = str(item.get(key) or "").strip()
        if value:
            ids.append(value)
    reference_keys = ["reference_asset_ids"]
    if allow_characters:
        reference_keys.append("character_ids")
    if allow_props:
        reference_keys.append("prop_ids")
    for key in reference_keys:
        values = item.get(key)
        if not isinstance(values, list):
            values = [values] if values else []
        ids.extend(str(value).strip() for value in values if str(value or "").strip())

    paths: list[Path] = []
    for asset_id in ids:
        path = asset_paths.get(asset_id)
        if path and path.exists() and path not in paths:
            paths.append(path)
    return paths


def _stage_image_key(scene_id: int, stage_id: str) -> str:
    safe_stage = "".join(char if char.isalnum() or char in ("_", "-") else "_" for char in stage_id)
    return f"scene_{scene_id:02d}_stage_{safe_stage.strip('_') or 'stage'}"


def _generate_assets_sequential(
    jobs: list[tuple[str, str, str, bool, list[Path]]],
    run_id: str,
    *,
    config: dict[str, Any],
) -> dict[str, Path]:
    generated = {}
    spacing = float(config["image"].get("request_spacing_seconds", 13))
    for index, (image_key, prompt, title, add_banner, reference_paths) in enumerate(jobs, start=1):
        if index > 1:
            time.sleep(spacing)
        print(f"[{run_id}] Generating image {index}/{len(jobs)}: {image_key}")
        generated[image_key] = generate_image(
            prompt,
            image_key,
            run_id,
            config=config,
            mock=False,
            title=title,
            add_banner=add_banner,
            reference_paths=reference_paths,
        )
    return generated


def generate_image(
    prompt: str,
    image_key: str | int,
    run_id: str,
    *,
    config: dict[str, Any],
    mock: bool = False,
    title: str | None = None,
    add_banner: bool | None = None,
    reference_paths: list[Path] | None = None,
) -> Path:
    ensure_cache_dirs()
    safe_key = _safe_key(image_key)
    allow_doodle = _allow_doodle_style(config)
    prompt = _safe_prompt(prompt, allow_doodle=allow_doodle) if config["image"].get("safety_rewrite", True) else prompt
    output_path = run_dir(run_id) / "images" / f"{safe_key}.png"
    image_config = config["image"]
    should_add_banner = bool(image_config.get("add_title_banner", True)) if add_banner is None else bool(add_banner)
    reference_paths = reference_paths or []
    if _image_file_valid(output_path):
        return output_path
    cache_key = (
        f"MODEL:{image_config.get('model', '')}\n"
        f"FALLBACK_MODEL:{image_config.get('fallback_model', '')}\n"
        f"ASPECT:{image_config.get('aspect_ratio', '')}\n"
        f"RESOLUTION:{image_config.get('resolution', '')}\n"
        f"FORMAT:{image_config.get('output_format', '')}\n"
        f"STYLE:{image_config.get('style', '')}\n"
        f"REFERENCES:{_reference_cache_key(reference_paths)}\n"
        f"{prompt}\nTITLE:{title or ''}\nBANNER:{should_add_banner}"
    )
    cache_path = CACHE_DIR / "images" / f"{_prompt_hash(cache_key)}.png"

    if cache_path.exists():
        shutil.copyfile(cache_path, output_path)
        return output_path

    if mock:
        _create_placeholder_image(output_path, prompt, config=config)
    elif os.getenv("REPLICATE_API_TOKEN"):
        _generate_replicate_image(prompt, output_path, config=config, reference_paths=reference_paths)
    else:
        raise RuntimeError("REPLICATE_API_TOKEN is required for a real run. Use --mock for offline testing.")

    if output_path.exists():
        if should_add_banner:
            _add_title_banner(output_path, title or _title_from_key(image_key), config=config)
        shutil.copyfile(output_path, cache_path)
    return output_path


def _generate_replicate_image(
    prompt: str,
    output_path: Path,
    *,
    config: dict[str, Any],
    reference_paths: list[Path] | None = None,
) -> None:
    try:
        import replicate
    except ImportError as exc:
        raise RuntimeError("Install replicate first: python -m pip install -r requirements.txt") from exc

    attempts = int(config["image"].get("retry_attempts", 3))
    model = str(config["image"].get("model", "black-forest-labs/flux-schnell"))
    fallback_model = str(config["image"].get("fallback_model", "")).strip()
    rate_limit_sleep = float(config["image"].get("rate_limit_sleep_seconds", 18))
    models_to_try = [model]
    if fallback_model and fallback_model != model:
        models_to_try.append(fallback_model)
    reference_paths = reference_paths or []

    last_error: Exception | None = None
    for candidate_index, candidate_model in enumerate(models_to_try, start=1):
        request_prompt = prompt
        for attempt in range(1, attempts + 1):
            request_input, file_handles = _replicate_input(
                request_prompt,
                model=candidate_model,
                config=config,
                reference_paths=reference_paths,
            )
            try:
                output = replicate.run(candidate_model, input=request_input)
                first = output[0] if isinstance(output, list) else output
                _save_replicate_output(first, output_path)
                return
            except Exception as exc:  # pragma: no cover - external service behavior
                last_error = exc
                if _is_rate_limit_error(exc):
                    sleep_seconds = max(rate_limit_sleep, _rate_limit_reset_seconds(exc) + 2)
                    print(
                        f"Replicate rate limit hit; waiting {sleep_seconds:.0f}s before retry "
                        f"{attempt + 1}/{attempts} on {candidate_model}"
                    )
                    time.sleep(sleep_seconds)
                    continue

                if _is_model_backend_error(exc) and candidate_index < len(models_to_try):
                    next_model = models_to_try[candidate_index]
                    print(
                        f"Replicate model error on {candidate_model}: {type(exc).__name__}: {exc}. "
                        f"Falling back to {next_model}."
                    )
                    break

                if _is_model_backend_error(exc) and attempt < attempts:
                    request_prompt = _recovery_prompt(prompt)
                    sleep_seconds = min(rate_limit_sleep, 6)
                    print(
                        f"Replicate model error on {candidate_model}; retrying with safer simplified prompt "
                        f"{attempt + 1}/{attempts} after {sleep_seconds:.0f}s."
                    )
                    time.sleep(sleep_seconds)
                    continue

                raise RuntimeError(
                    f"Image generation failed with model {candidate_model}. "
                    f"Last error: {type(exc).__name__}: {exc}"
                ) from exc
            finally:
                for handle in file_handles:
                    handle.close()

    raise RuntimeError(
        f"Image generation failed after trying {', '.join(models_to_try)}. "
        f"Last error: {type(last_error).__name__}: {last_error}"
    ) from last_error


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "throttled" in text


def _rate_limit_reset_seconds(exc: Exception) -> float:
    match = re.search(r"resets?\s+in\s+~?(\d+(?:\.\d+)?)s", str(exc), flags=re.IGNORECASE)
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


def _is_model_backend_error(exc: Exception) -> bool:
    if _is_rate_limit_error(exc):
        return False
    text = str(exc).lower()
    return "modelerror" in type(exc).__name__.lower() or "q_descale" in text or "prediction failed" in text


def _replicate_input(
    prompt: str,
    *,
    model: str,
    config: dict[str, Any],
    reference_paths: list[Path] | None = None,
) -> tuple[dict[str, Any], list[Any]]:
    image_config = config["image"]
    aspect_ratio = str(image_config.get("aspect_ratio", "16:9"))
    output_format = str(image_config.get("output_format", "png"))
    reference_paths = reference_paths or []
    file_handles = [path.open("rb") for path in reference_paths if path.exists()]

    if model.startswith("google/nano-banana"):
        request_input: dict[str, Any] = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "output_format": output_format,
        }
        if file_handles:
            request_input["image_input"] = file_handles
        return request_input, file_handles

    if model.startswith("black-forest-labs/flux-kontext"):
        request_input = {
            "prompt": prompt,
            "aspect_ratio": "match_input_image" if file_handles else aspect_ratio,
            "output_format": output_format,
            "safety_tolerance": int(image_config.get("safety_tolerance", 2)),
            "prompt_upsampling": bool(image_config.get("prompt_upsampling", False)),
        }
        if file_handles:
            request_input["input_image"] = file_handles[0]
        return request_input, file_handles

    if model.startswith("recraft-ai/recraft-v3"):
        return {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "style": _recraft_style(str(image_config.get("style", "digital_illustration"))),
        }, file_handles

    if model.startswith("google/imagen-4"):
        return {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "output_format": output_format,
        }, file_handles

    if model.startswith("ideogram-ai/ideogram-v4"):
        return {
            "prompt": prompt,
            "resolution": str(image_config.get("resolution", "1280x720")),
        }, file_handles

    if model.startswith("black-forest-labs/flux-2-max") or model.startswith("black-forest-labs/flux-2-pro"):
        return {
            "prompt": prompt,
            "resolution": str(image_config.get("resolution", "1 MP")),
            "aspect_ratio": aspect_ratio,
            "output_format": output_format,
            "output_quality": int(image_config.get("output_quality", 95)),
            "safety_tolerance": int(image_config.get("safety_tolerance", 2)),
        }, file_handles

    if model.startswith("black-forest-labs/flux-2-dev"):
        return {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "go_fast": bool(image_config.get("go_fast", True)),
            "output_format": output_format,
            "output_quality": int(image_config.get("output_quality", 95)),
        }, file_handles

    if model.startswith("black-forest-labs/flux-1.1-pro"):
        return {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "output_format": output_format,
        }, file_handles

    request_input = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "num_outputs": 1,
        "num_inference_steps": int(image_config.get("num_inference_steps", 4)),
        "output_format": output_format,
    }
    if "go_fast" in image_config:
        request_input["go_fast"] = bool(image_config.get("go_fast"))
    return request_input, file_handles


def _safe_prompt(prompt: str, *, allow_doodle: bool = False) -> str:
    replacements = {
        r"\bKargil War\b": "Kargil mountain border crisis",
        r"\btitle[- ]?banner\b": "scene layout",
        r"\btitle[- ]?band\b": "open sky",
        r"\btitle[- ]?space\b": "open sky",
        r"\bYouTube\b": "educational",
        r"\bwar\b": "crisis",
        r"\bwars\b": "crises",
        r"\bsoldiers\b": "bundled figures",
        r"\bsoldier\b": "bundled figure",
        r"\btroops\b": "groups of figures",
        r"\barmy\b": "organized group",
        r"\barmies\b": "organized groups",
        r"\bmilitary\b": "official",
        r"\bmilitants\b": "hidden figures",
        r"\bmilitant\b": "hidden figure",
        r"\bfighters\b": "figures",
        r"\bfighter\b": "figure",
        r"\bweapon\b": "object",
        r"\bweapons\b": "objects",
        r"\bgun\b": "stick-like object",
        r"\bguns\b": "stick-like objects",
        r"\brifle\b": "stick-like object",
        r"\brifles\b": "stick-like objects",
        r"\bmissile\b": "alert screen",
        r"\bmissiles\b": "alert screens",
        r"\bnuclear\b": "high-stakes",
        r"\bwarhead\b": "alert screen",
        r"\bwarheads\b": "alert screens",
        r"\bbomb\b": "alert screen",
        r"\bbombs\b": "alert screens",
        r"\bartillery\b": "distant pressure",
        r"\bcannon\b": "large tube shape",
        r"\bcannons\b": "large tube shapes",
        r"\bshooting\b": "confronting",
        r"\bshoot\b": "confront",
        r"\bfiring\b": "sending smoke puffs",
        r"\bfire\b": "send smoke puffs",
        r"\bexplosion\b": "smoke puff",
        r"\bexplosions\b": "smoke puffs",
        r"\bdead\b": "fallen",
        r"\bblood\b": "red accent",
        r"\bstrychnine\b": "period medicine vial",
        r"\bpoison\b": "dangerous substance",
        r"\bpoisoned\b": "made ill",
        r"\bbrandy\b": "small amber flask",
        r"\balcohol\b": "amber liquid",
        r"\bdrug\b": "medicine",
        r"\bdrugs\b": "medicine",
        r"\bdrugged\b": "dazed",
        r"\bhallucinating\b": "dazed",
        r"\bhallucination\b": "dazed vision",
        r"\bdelirious\b": "dazed",
        r"\bdisturbing\b": "tense",
        r"\bhandlers?\b": "support figures",
    }
    safe = prompt
    for pattern, replacement in replacements.items():
        safe = re.sub(pattern, replacement, safe, flags=re.IGNORECASE)
    safe = re.sub(r"\bchar_[a-z0-9_]+\b", "the referenced character", safe, flags=re.IGNORECASE)
    safe = re.sub(r"\bbg_[a-z0-9_]+\b", "the referenced background", safe, flags=re.IGNORECASE)
    safe = re.sub(r"\bprop_[a-z0-9_]+\b", "the referenced prop", safe, flags=re.IGNORECASE)
    if allow_doodle:
        safe += (
            " Safe educational hand-drawn explainer visual, symbolic scene, harmless objects only. "
            "Polished ink linework, clean sketchbook composition, confident simple shapes, lightly textured paper, tasteful flat color accents. "
            "Make the image instantly readable at a glance with one clear focal point, cinematic framing, expressive poses, and strong object-based storytelling. "
            "When people are relevant use anonymous simplified cutout figures, not realistic likenesses of living people. "
            "NO glossy 3D render, NO comic-book superhero style, NO crude childlike drawing, NO whiteboard marker style. "
            "Scene-only image, not a worksheet, not a poster, not a form, no ruled blank lines, no header area, no fake paragraphs, no logos, no watermarks. "
            "Do not draw a decorative border, page frame, boxed panel outline, or enclosing rectangle around the whole image."
        )
    else:
        safe += (
            " Safe educational documentary collage, symbolic scene, harmless objects only. "
            "Catchy YouTube documentary explainer visual, archival-inspired but fully fictional generated imagery, layered paper texture, subtle film grain, halftone accents, cutout editorial composition. "
            "Make the image instantly readable at a glance with one clear focal point, cinematic framing, expressive body language, and strong object-based storytelling. "
            "When people are relevant use anonymous editorial cutouts or silhouettes, not stick figures, and avoid realistic likenesses of living people. "
            "Use bold but tasteful flat color accents on important screens, clothing, props, documents, maps, and background areas, usually 2 to 4 colors. "
            "NO glossy 3D render, NO comic-book superhero style, NO children's doodle style, NO whiteboard marker style. "
            "Scene-only image, not a worksheet, not a poster, not a form, no ruled blank lines, no header area, no letters, no readable text, no labels, no visible asset ids, no logos, no watermarks. "
            "Do not draw a decorative border, page frame, boxed panel outline, or enclosing rectangle around the whole image."
        )
    return safe


def _allow_doodle_style(config: dict[str, Any]) -> bool:
    style = str(config.get("image", {}).get("style", "")).lower()
    return any(word in style for word in ("doodle", "hand-drawn", "hand drawn", "sketch"))


def _recraft_style(style: str) -> str:
    allowed = {
        "any",
        "realistic_image",
        "digital_illustration",
        "digital_illustration/pixel_art",
        "digital_illustration/hand_drawn",
        "digital_illustration/grain",
        "digital_illustration/infantile_sketch",
        "digital_illustration/2d_art_poster",
        "digital_illustration/handmade_3d",
        "digital_illustration/hand_drawn_outline",
        "digital_illustration/engraving_color",
        "digital_illustration/2d_art_poster_2",
        "realistic_image/b_and_w",
        "realistic_image/hard_flash",
        "realistic_image/hdr",
        "realistic_image/natural_light",
        "realistic_image/studio_portrait",
        "realistic_image/enterprise",
        "realistic_image/motion_blur",
    }
    cleaned = style.strip()
    if cleaned in allowed:
        return cleaned
    lowered = cleaned.lower()
    if any(word in lowered for word in ("doodle", "hand-drawn", "hand drawn", "sketch", "ink")):
        return "digital_illustration/hand_drawn"
    if any(word in lowered for word in ("collage", "documentary", "illustration", "explainer")):
        return "digital_illustration"
    if any(word in lowered for word in ("photo", "realistic", "cinematic")):
        return "realistic_image"
    return "digital_illustration"


def _recovery_prompt(prompt: str) -> str:
    shortened = re.sub(r"\s+", " ", _safe_prompt(prompt)).strip()
    shortened = shortened[:1200]
    return (
        "Create a safe educational documentary collage image. "
        "Use only harmless symbolic objects and anonymous figures. "
        "Avoid any readable text, labels, logos, medical dosing, injury, intoxication, or explicit harm. "
        f"{shortened}"
    )


def _save_replicate_output(output: Any, output_path: Path) -> None:
    if hasattr(output, "read"):
        output_path.write_bytes(output.read())
        return

    url = output.url() if hasattr(output, "url") else str(output)
    urllib.request.urlretrieve(url, output_path)


def _image_file_valid(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def _create_placeholder_image(output_path: Path, prompt: str, *, config: dict[str, Any]) -> None:
    width = int(config["video"].get("width", 1280))
    height = int(config["video"].get("height", 720))
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    margin = 70
    draw.rectangle((margin, margin, width - margin, height - margin), outline="black", width=5)
    draw.line((margin + 80, height - 160, width - margin - 80, height - 160), fill="black", width=4)
    draw.ellipse((width // 2 - 70, 160, width // 2 + 70, 300), outline="black", width=5)
    draw.line((width // 2, 300, width // 2, 465), fill="black", width=5)
    draw.line((width // 2, 360, width // 2 - 130, 440), fill="black", width=5)
    draw.line((width // 2, 360, width // 2 + 130, 440), fill="black", width=5)
    draw.line((width // 2, 465, width // 2 - 100, 590), fill="black", width=5)
    draw.line((width // 2, 465, width // 2 + 100, 590), fill="black", width=5)

    image.save(output_path)


def _add_title_banner(image_path: Path, title: str, *, config: dict[str, Any]) -> None:
    width = int(config["video"].get("width", 1280))
    height = int(config["video"].get("height", 720))
    banner_h = int(height * 0.14)
    title = _format_title(title)

    with Image.open(image_path).convert("RGB") as source:
        canvas = Image.new("RGB", (width, height), "white")
        drawing_h = height - banner_h
        source.thumbnail((width, drawing_h), Image.Resampling.LANCZOS)
        x = (width - source.width) // 2
        y = banner_h + (drawing_h - source.height) // 2
        canvas.paste(source, (x, y))

    draw = ImageDraw.Draw(canvas)
    font = _title_font(64)
    while font.size > 30 and draw.textbbox((0, 0), title, font=font)[2] > width - 80:
        font = _title_font(font.size - 4)

    bbox = draw.textbbox((0, 0), title, font=font)
    text_x = (width - (bbox[2] - bbox[0])) / 2
    text_y = (banner_h - (bbox[3] - bbox[1])) / 2 - 8
    draw.text((text_x, text_y), title, fill="black", font=font)
    canvas.save(image_path)


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24]


def _reference_cache_key(reference_paths: list[Path]) -> str:
    if not reference_paths:
        return ""
    parts = []
    for path in reference_paths:
        if path.exists():
            parts.append(f"{path.name}:{_file_hash(path)}")
    return "|".join(parts)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _safe_key(image_key: str | int) -> str:
    if isinstance(image_key, int):
        return f"scene_{image_key:02d}"
    safe = "".join(char if char.isalnum() or char in ("_", "-") else "_" for char in str(image_key))
    return safe.strip("_") or "image"


def _title_from_key(image_key: str | int) -> str:
    return str(image_key).replace("_", " ")


def _format_title(title: str) -> str:
    cleaned = " ".join(title.replace("_", " ").split())
    return cleaned.upper()


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


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
    return _font(size)


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if len(candidate) > width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines
