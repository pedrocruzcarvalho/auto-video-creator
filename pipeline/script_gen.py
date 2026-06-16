from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .paths import ensure_run_dirs, run_dir


STYLE_SUFFIX = (
    "documentary collage explainer style, archival-inspired but fully fictional generated imagery, "
    "cutout editorial composition, layered paper textures, subtle film grain, halftone accents, "
    "clean cinematic framing, strong focal point, high contrast, tasteful dramatic lighting, "
    "limited but bold color palette with 2 to 4 accent colors, maps, documents, objects, silhouettes, and environment details when useful, "
    "scene-only image, no title text, no readable captions, no generated letters, no dates, no numbers, no labels, no visible asset ids, "
    "no decorative border, no drawn frame around the whole image, no boxed panel outline, no enclosing rectangle, "
    "do not imitate a real newspaper, do not create readable articles, do not create logos, do not create watermarks, "
    "not a worksheet, not a poster, not a comic panel, not a whiteboard drawing, not a children's doodle"
)

DOODLE_STYLE_SUFFIX = (
    "polished hand-drawn documentary doodle explainer style, confident ink linework, expressive but simple characters, "
    "clean visual metaphor, editorial sketchbook composition, lightly textured paper, tasteful flat color accents, "
    "clear readable silhouette, strong focal point, professional educational animation concept art, "
    "maps, timelines, documents, charts, arrows, labels, objects, and simplified people when useful, "
    "scene-only image, no generated paragraphs, no fake article text, no logos, no watermarks, "
    "not crude, not childish, not messy, not low-effort, not glossy 3D, not photorealistic"
)

SHOT_TYPES = {
    "hook",
    "establishing",
    "reaction_closeup",
    "object_insert",
    "diagram",
    "before_after",
    "escalation",
    "decision",
    "reveal",
    "payoff",
}
VISUAL_ENERGIES = {"low", "medium", "high", "surprise"}


def generate(topic: str, run_id: str, *, config: dict[str, Any], mock: bool = False) -> list[dict[str, Any]]:
    return generate_payload(topic, run_id, config=config, mock=mock)["scenes"]


def generate_payload(topic: str, run_id: str, *, config: dict[str, Any], mock: bool = False) -> dict[str, Any]:
    ensure_run_dirs(run_id)
    if mock:
        scenes = _mock_scenes(topic, int(config["script"].get("target_scene_count", 7)))
        visual_assets = _mock_visual_assets(topic)
    elif os.getenv("ANTHROPIC_API_KEY"):
        script = generate_script(topic, config=config)
        payload = split_into_scene_payload(topic, script, config=config, run_id=run_id)
        scenes = payload["scenes"]
        visual_assets = payload.get("visual_assets", {})
    else:
        raise RuntimeError("ANTHROPIC_API_KEY is required for a real run. Use --mock for offline testing.")

    visual_assets = _normalize_visual_assets(visual_assets, config=config)
    normalized = _normalize_scenes(scenes, config=config)
    budget = _image_budget(
        config,
        scene_count=max(1, len(normalized)),
        requested_shots_max=_shot_range(config)[1],
    )
    save_script(topic, normalized, run_id, visual_assets=visual_assets, image_budget=budget)
    return {"scenes": normalized, "visual_assets": visual_assets}


def generate_script(topic: str, *, config: dict[str, Any]) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("Install anthropic first: python -m pip install -r requirements.txt") from exc

    target_words = int(config["script"].get("target_word_count", 150))
    scene_count = int(config["script"].get("target_scene_count", 7))
    seconds_per_scene = int(config["script"].get("target_seconds_per_scene", 120))
    format_name = str(config["script"].get("format", "general"))
    style_instruction = _script_style_instruction(format_name)
    model = _anthropic_model(config)
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=max(700, int(target_words * 1.6)),
        temperature=0.8,
        system=(
            "You write concise, punchy educational YouTube narration. "
            "Use vivid examples, plain language, and no markdown."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Write a {target_words}-word narration for a short explainer video about: {topic}. "
                    f"Structure it as {scene_count} major boxes/topics. "
                    f"In box mode, each box should be around {seconds_per_scene} seconds of spoken narration; "
                    "1:30 to 2:30 is acceptable for a 120-second target, but do not make it a 30-second summary. "
                    "Make it coherent, surprising, and easy to visualize. "
                    "End each box with a clean final sentence that names the outcome and why the crisis mattered. "
                    "Do not end on a dangling action beat like someone signing, boarding, walking away, or saying one last thing. "
                    f"{style_instruction}"
                ),
            }
        ],
    )
    return response.content[0].text.strip()


def split_into_scenes(topic: str, script: str, *, config: dict[str, Any], run_id: str | None = None) -> list[dict[str, Any]]:
    return split_into_scene_payload(topic, script, config=config, run_id=run_id)["scenes"]


def split_into_scene_payload(topic: str, script: str, *, config: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("Install anthropic first: python -m pip install -r requirements.txt") from exc

    scene_count = int(config["script"].get("target_scene_count", 7))
    seconds_per_scene = int(config["script"].get("target_seconds_per_scene", 120))
    shots_min, shots_max = _shot_range(config)
    shot_budget = _image_budget(config, scene_count=scene_count, requested_shots_max=shots_max)
    allowed_shots_max = shot_budget["max_final_shots_per_scene"]
    allowed_shots_min = min(shots_min, allowed_shots_max)
    timed_text_popups = _config_bool(config["script"].get("timed_text_popups"), False)
    aligned_popups = _config_bool(config.get("popups", {}).get("enabled"), False)
    callout_candidates = timed_text_popups or aligned_popups
    hybrid_enabled = _config_bool(config.get("hybrid", {}).get("enabled"), False)
    max_stage_images = int(config.get("hybrid", {}).get("max_stage_images_per_scene", 10))
    format_name = str(config["script"].get("format", "general"))
    image_instruction = _image_style_instruction(format_name)
    model = _anthropic_model(config)
    client = anthropic.Anthropic()
    if hybrid_enabled:
        json_shape = (
            '{"visual_assets":{"backgrounds":[{"id":"bg_control_room","description":"...",'
            '"image_prompt":"empty reusable background plate, no people"}],'
            '"characters":[{"id":"char_operator","description":"...",'
            '"image_prompt":"character reference sheet"}],'
            '"props":[{"id":"prop_alert_screen","description":"...",'
            '"image_prompt":"reusable prop reference"}]},'
            '"scenes":[{"id":1,"box_title":"...","text":"full narration for this box",'
            '"intro_image_prompt":"summary documentary collage thumbnail for the starting grid",'
            '"background_id":"bg_control_room","character_ids":["char_operator"],"prop_ids":["prop_alert_screen"],'
            '"stages":[{"id":"control_room_wide","background_id":"bg_control_room",'
            '"character_ids":["char_operator"],"prop_ids":["prop_alert_screen"],'
            '"composition":"operator sits at console while alert screen glows",'
            '"replicate_prompt":"edit the referenced background and character into this stage plate",'
            '"image_prompt":"reusable full-screen documentary collage stage plate"}],'
            '"shots":[{"id":1,"type":"image","text":"narration covered by this visual",'
            '"stage_id":"control_room_wide","camera":"wide",'
            '"background_id":"bg_control_room","character_ids":["char_operator"],"prop_ids":["prop_alert_screen"],'
            '"shot_type":"reaction_closeup","visual_energy":"high","color_palette":["red","yellow","blue"],'
            '"composition_style":"big worried face in foreground, glowing screen behind",'
            '"composition":"same stage, tighter emotional beat",'
            '"replicate_prompt":"preserve references and show the described beat",'
            '"callout":{"text":"FALSE ALARM","color":"red","position":"center"},'
            '"overlays":[]}]}]}'
        )
        visual_workflow_instruction = (
            f"Use no more than {max_stage_images} generated stage plates per scene. "
            "A stage plate is a reusable full-screen documentary collage image. It is not an empty background: "
            "it should already include the important pose and layout needed for several beats, such as a man seated at a control desk, "
            "a close-up alert screen, a worried face, a command room wide shot, or a quiet exterior. "
            "Then make many shots reference those stage plates using stage_id. "
            "Every shot must have stage_id matching one of the scene.stages ids. "
            "Use the stage budget generously. For a 120-second scene, aim for 8 to 10 stage plates unless the narration is unusually short. "
            "Do not keep the same stage_id for more than two consecutive shots. "
            "If one visual would stay on screen for more than about 8 seconds, create another generated stage plate that preserves the same ambient layout but adds/removes a real drawn element. "
            "The first stage should often be a broad ambient establishing plate with no main characters: room, coastline, mountain pass, city skyline, command center, map-like space, or exterior location. "
            "Later stages may reuse that same ambient composition and add characters, screens, papers, facial reactions, or other story elements in the generated image itself. "
            "Only reuse a broad ambient stage directly when the narration is setting location or mood. "
            "Do not use effect fields. If a visual needs sweat, glow, signal lines, papers, tension marks, or other drawn additions, make a new generated stage plate for that beat instead. "
            "Do not include image_prompt on shots unless you need it as a note; the paid generated images come from stages. "
        )
    else:
        json_shape = (
            '{"visual_assets":{"backgrounds":[{"id":"bg_control_room","description":"...",'
            '"image_prompt":"empty reusable background plate, no people"}],'
            '"characters":[{"id":"char_operator","description":"...",'
            '"image_prompt":"character reference sheet"}],'
            '"props":[{"id":"prop_alert_screen","description":"...",'
            '"image_prompt":"reusable prop reference"}]},'
            '"scenes":[{"id":1,"box_title":"...","text":"full narration for this box",'
            '"intro_image_prompt":"summary documentary collage thumbnail for the starting grid",'
            '"background_id":"bg_control_room","character_ids":["char_operator"],"prop_ids":["prop_alert_screen"],'
            '"shots":[{"id":1,"type":"image","text":"narration covered by this visual",'
            '"background_id":"bg_control_room","character_ids":["char_operator"],"prop_ids":["prop_alert_screen"],'
            '"shot_type":"reaction_closeup","visual_energy":"high","color_palette":["red","yellow","blue"],'
            '"composition_style":"big worried face in foreground, glowing screen behind",'
            '"composition":"operator sits at the console in an anxious pose",'
            '"replicate_prompt":"edit the referenced background and character into this scene",'
            '"image_prompt":"specific documentary collage image to show during this line",'
            '"callout":{"text":"FALSE ALARM","color":"red","position":"center"},'
            '"overlays":[]}]}]}'
        )
        visual_workflow_instruction = (
            "Every shot must include a complete image_prompt for one paid generated image. "
            "Do not use stages or stage_id. Each shot image_prompt should be self-contained and describe exactly what should be visible for that beat. "
            "Use only as many final shot images as the visual story needs; do not fill the budget with rushed or redundant images. "
            "Do not let any one concept feel like it would sit unchanged for more than about 5 seconds. "
            "A visual beat may preserve the same setting as the previous shot, but it should add a clear generated change: different pose, closer face, new screen state, paper, phone, group reaction, exterior cutaway, map-like simple scene, or broad establishing view. "
        )
    user_prompt = (
        f"Split this narration about {topic!r} into exactly {scene_count} scenes.\n\n"
        "Return this JSON shape:\n"
        f"{json_shape}\n\n"
        f"For this run, the image budget is at most {shot_budget['max_total_requests']} total Replicate image requests, "
        f"including {shot_budget['intro_requests']} intro image, reusable assets, and final scene shots. "
        f"The estimated image cost is ${shot_budget['estimated_request_cost_usd']:.3f} each, so the planned image requests must stay near or under ${shot_budget['max_run_image_cost_usd']:.2f}. "
        f"Across all scenes, create no more than {shot_budget['max_final_shots_total']} final scene shot images. "
        f"For each scene, create between {allowed_shots_min} and {allowed_shots_max} final shot images, choosing the exact number yourself based on pacing and quality. "
        "Prefer fewer strong, deliberate images over many rushed images. "
        f"{visual_workflow_instruction}"
        "Before the scenes, create a top-level visual_assets object. "
        "Keep the asset library lean because every asset costs money: for a one-minute scene, prefer 1 to 2 backgrounds, 1 to 2 characters, and 2 to 4 props. "
        "Only add another reusable asset if it will appear in multiple shots or is essential for consistency. "
        "visual_assets.backgrounds are reusable empty environment plates: rooms, streets, maps, exteriors, interiors, or landscapes with no main characters. "
        "visual_assets.characters are reusable character reference sheets: one character per asset, neutral full-body or bust view, clear outfit, body shape, face shape, hair, and expression range. "
        "visual_assets.props are reusable objects, screens, vehicles, documents, alarms, signs, phones, maps, or symbolic devices that may appear in multiple shots. "
        "One-off extras should not become visual assets: generic crowds, background figures, single-use assistants, one-time passersby, and disposable objects should be described inside that shot's composition and replicate_prompt only. "
        "Create a character asset only for a person whose identity must stay consistent across multiple shots. "
        "Create a prop asset only for an object that recurs or must stay visually consistent. "
        "Use stable snake_case ids with prefixes bg_, char_, and prop_. Do not create duplicate assets for the same visual identity. "
        "Every background, character, and prop asset must include id, description, and image_prompt. "
        "Every scene, stage, and shot should include background_id, character_ids, prop_ids, composition, and replicate_prompt whenever relevant. "
        "Every shot must also include shot_type, visual_energy, color_palette, and composition_style. "
        "Allowed shot_type values are: hook, establishing, reaction_closeup, object_insert, diagram, before_after, escalation, decision, reveal, payoff. "
        "Use a deliberate one-minute visual arc: begin with a weird hook or strong visual question, quickly establish the setting, escalate with close-ups and object inserts, use one simple diagram only when it clarifies, pause on a decision or contradiction, then end with a reveal/payoff image. "
        "Do not make all shots the same type. Across a one-minute scene, include at least one reaction_closeup, one object_insert or diagram, one escalation, and one reveal or payoff. "
        "visual_energy must be low, medium, high, or surprise. Most videos should alternate energy instead of staying flat. "
        "color_palette must list 2 to 4 plain color words, chosen for the shot's emotion: red/orange for danger, yellow for surprise, blue for explanation/calm, green for success/money/science, purple only if truly useful. "
        "composition_style should be punchy and cinematic for documentary collage: big foreground object, tiny human silhouette, split-screen contrast, before/after layout, looming object, flying documents, evidence-board layering, zoomed-in hand, map cutaway, archival photo cutout, or dramatic silhouette. "
        "replicate_prompt is the instruction for an image-editing model that receives the referenced asset images. "
        "It must explicitly say what to preserve from the background, which characters/props to place, where they go, their pose, expression, action, camera, and mood. "
        "replicate_prompt must include the shot_type's visual idea and the color_palette so the image has energy and color. "
        "Do not write asset ids like char_petrov, bg_bunker, or prop_phone inside replicate_prompt or image_prompt; use natural phrases like the referenced officer, the referenced bunker room, or the referenced telephone. "
        "When a beat uses the same location or person as an earlier beat, reference the same asset ids instead of redescribing a new identity. "
        "Build each scene like a fast documentary edit: establish a place, cut to a human reaction, insert an object or document close-up, use a map/diagram only when it clarifies, then land a reveal/payoff image. "
        "Most consecutive visuals in a scene should preserve the same camera angle, horizon, background colors, and main layout, while adding/removing one clear layer: "
        "a character, vehicle, signal line, screen glow, room detail, worried face, document, telephone, or crossed-out object. "
        "Do not reset to totally unrelated compositions unless the narration moves to a new location. "
        "Use image_prompt wording like: 'archival-inspired control room collage, anxious operator cutout at a console, red alert screen glow, paper texture, dramatic shadow, no readable text...' "
        "The intro_image_prompt is different from the scene stages: make it a broad ambient thumbnail with no main characters, no title, no readable text, and no close-up face. "
        "It should communicate the place or situation generally, and it can be reused conceptually as the first wide ambient stage. "
        "Use simplified human cutouts or silhouettes when people are relevant. Avoid realistic likenesses of living people. "
        "Use safe neutral visual language in image_prompt: say 'figures', 'control room', 'alert screen', 'signal line', 'tense moment', 'map cutaway', and 'document close-up'. "
        "Avoid sensitive words in image_prompt such as war, soldier, weapon, missile, nuclear, bomb, shooting, firing, artillery, military, militant, fighter, dead, blood, or explosion. "
        "Do not create symbol overlays. Do not add arrows, warning signs, danger icons, flags, circles, X marks, route markers, or decorative borders as overlays. "
        "Set overlays to an empty list for every shot. "
        f"Each scene.text should be long enough for roughly {seconds_per_scene} seconds of narration unless the source topic genuinely needs less. "
        "The shot text must collectively cover the scene narration in order. "
        "box_title must be short, specific, and suitable as a top title banner, ideally 3 to 7 words. "
        "Do not use the full video title as box_title. For the Petrov false alarm topic, prefer a concise title like 'The 1983 False Alarm'. "
        "Each scene.text must begin by saying the box_title aloud. "
        "Each scene.text must end with a complete closing sentence that states the outcome and why the incident mattered. "
        "Do not end a scene on a clipped action beat like 'he signed it' or 'the plane landed.' "
        "If a real historically relevant date is important and you are confident it is correct, put it immediately after the title, "
        "for example: 'Kargil War, 1999.' Do not invent dates and do not add a date when uncertain. "
        + (
            "Add a callout to about half of the shots: one crucial word, date, acronym, or phrase, "
            "maximum 4 words. Every historically relevant spoken year or date, such as 1983, must get a callout on the shot where it is spoken. "
            "Prefer punchy callouts that make the video catchier, such as WAIT, NO LAUNCH, BAD SENSOR, TOO STICKY, SOLD ONLINE, or ACCIDENTAL GENIUS, but only when the phrase is actually spoken in shot.text. "
            "callout.text MUST appear verbatim in that shot.text; if the narrator does not say the phrase, do not make it a callout. "
            "Use color red, yellow, blue, green, or black. Use position left, right, top, bottom, or center. "
            if callout_candidates
            else "Set callout to null for every shot. Do not create timed text popups. "
        )
        +
        "Do not assign exact timestamps; use natural shot chunks.\n"
        f"{image_instruction}\n"
        "Do not ask the image model to draw title text, numbers, dates, acronyms, labels, captions, or asset ids; all readable writing must be overlays/callouts added later in code. "
        "Keep every image_prompt and replicate_prompt concise: describe only subject, setting, composition, pose, emotion, camera, and required preservation. "
        "Do not repeat the global documentary collage style phrase; the pipeline appends it later in code. "
        "For every intro_image_prompt, include: broad ambient establishing thumbnail, no people, no close-up face, no title banner, no readable text.\n\n"
        "Strict JSON rules: use double quotes for every key and string, escape quotes inside strings, "
        "do not use trailing commas, comments, markdown, NaN, Infinity, undefined, or Python None/True/False.\n\n"
        f"Narration:\n{script}"
    )
    response = client.messages.create(
        model=model,
        max_tokens=max(5000, scene_count * shots_max * 450),
        temperature=0.2,
        system=(
            "You are a strict JSON API. Return exactly one valid JSON object and nothing else. "
            "No markdown fences, no comments, no prose before or after the object. "
            "Image prompts must describe documentary collage explainer images with cinematic editorial composition."
        ),
        messages=[
            {
                "role": "user",
                "content": user_prompt,
            }
        ],
    )
    raw_text = response.content[0].text
    _save_bad_json(raw_text, run_id, "scene_json_raw.txt")
    try:
        payload = _loads_json(raw_text)
    except json.JSONDecodeError as exc:
        _save_bad_json(raw_text, run_id, "scene_json_invalid.txt")
        repaired = _repair_json_with_anthropic(raw_text, error=str(exc), client=client, model=model)
        _save_bad_json(repaired, run_id, "scene_json_repaired.txt")
        payload = _loads_json(repaired)
    if "scenes" not in payload:
        _save_bad_json(raw_text, run_id, "scene_json_missing_scenes.txt")
        repaired = _repair_scene_payload_with_anthropic(
            raw_text,
            topic=topic,
            script=script,
            client=client,
            model=model,
            scene_count=scene_count,
            shots_min=shots_min,
            shots_max=shots_max,
        )
        _save_bad_json(repaired, run_id, "scene_json_missing_scenes_repaired.txt")
        payload = _loads_json(repaired)
    if "scenes" not in payload:
        keys = ", ".join(payload.keys())
        raise ValueError(f"Claude returned JSON without a top-level 'scenes' list. Top-level keys: {keys}")
    return {
        "visual_assets": payload.get("visual_assets") or payload.get("assets") or {},
        "scenes": payload["scenes"],
    }


def _anthropic_model(config: dict[str, Any]) -> str:
    return os.getenv("ANTHROPIC_MODEL") or str(config["script"].get("model", "claude-sonnet-4-6"))


def _shot_range(config: dict[str, Any]) -> tuple[int, int]:
    seconds = int(config["script"].get("target_seconds_per_scene", 120))
    raw_min = config["script"].get("shots_per_scene_min", "auto")
    raw_max = config["script"].get("shots_per_scene_max", "auto")
    if str(raw_min).lower() == "auto" or str(raw_max).lower() == "auto":
        interval_max = float(config["script"].get("shot_interval_seconds_max", 8))
        interval_min = float(config["script"].get("shot_interval_seconds_min", 5))
        auto_min = max(4, round(seconds / interval_max))
        auto_max = max(auto_min + 2, round(seconds / interval_min))
        return auto_min, auto_max
    return int(raw_min), max(int(raw_min), int(raw_max))


def _image_budget(config: dict[str, Any], *, scene_count: int, requested_shots_max: int) -> dict[str, Any]:
    image_config = config.get("image", {})
    asset_config = config.get("visual_assets", {})
    script_config = config.get("script", {})
    estimated_cost = max(0.001, _config_float(image_config.get("estimated_request_cost_usd"), 0.039))
    max_cost = max(estimated_cost, _config_float(image_config.get("max_run_image_cost_usd"), 1.0))
    max_total_requests = max(1, int(max_cost / estimated_cost))
    intro_requests = 1 if config.get("intro", {}).get("enabled", True) else 0
    max_assets = (
        max(0, _config_int(asset_config.get("max_backgrounds"), 2))
        + max(0, _config_int(asset_config.get("max_characters"), 2))
        + max(0, _config_int(asset_config.get("max_props"), 4))
    )
    configured_final_cap = max(1, _config_int(script_config.get("max_final_images_per_scene"), 15) * max(1, scene_count))
    budget_final_cap = max(1, max_total_requests - intro_requests - max_assets)
    max_final_shots_total = max(1, min(requested_shots_max * max(1, scene_count), configured_final_cap, budget_final_cap))
    max_final_shots_per_scene = max(1, max_final_shots_total // max(1, scene_count))
    return {
        "estimated_request_cost_usd": estimated_cost,
        "max_run_image_cost_usd": max_cost,
        "max_total_requests": max_total_requests,
        "intro_requests": intro_requests,
        "max_assets": max_assets,
        "max_final_shots_total": max_final_shots_total,
        "max_final_shots_per_scene": max_final_shots_per_scene,
    }


def _script_style_instruction(format_name: str) -> str:
    if format_name == "box_explainer":
        return (
            "Frame it as a compact documentary explainer chapter. "
            "Each scene is one focused chapter in a list-style educational video, built around one concrete incident or story. "
            "For a one-scene test, focus on only the first box and do not tease a full list."
        )
    if format_name == "doodle_explainer":
        return (
            "Frame it as a polished hand-drawn explainer with a strong story spine. "
            "Use concrete visual metaphors, simple cause-and-effect narration, and surprising turns that can be drawn clearly. "
            "Avoid generic hype. Make every sentence easy to convert into a clean sketch, map, chart, document, or character action."
        )
    return ""


def _image_style_instruction(format_name: str) -> str:
    if format_name == "box_explainer":
        return (
            "Each image_prompt should describe one clean full-screen documentary collage image for the incident: "
            "a location cue, human cutout or silhouette when useful, a strong object/document/map close-up, and a clear emotional focal point. "
            "Use layered paper texture, archival-photo feel, halftone or film-grain accents, and a limited bold color palette. "
            "Backgrounds may use flat map shapes, rooms, streets, stadiums, labs, skies, documents, or object tables with cinematic lighting. "
            "Do not draw a border, frame, page edge, box outline, panel outline, or enclosing rectangle around the whole image. "
            "Prefer readable editorial silhouettes, anonymous period-appropriate figures, and object-focused storytelling over generic smiling characters. "
            "For image prompts, avoid sensitive terms. Use neutral phrases like figures, control room, simple route, tense moment, alert screen, signal lines, map cutaway, and document close-up. "
            "Avoid fake readable text, logos, exact emblems, complex maps, glossy 3D renders, and any generated words/numbers/dates."
        )
    if format_name == "doodle_explainer":
        return (
            "Each image_prompt should describe one polished hand-drawn explainer frame: confident ink outlines, clean shapes, "
            "simple character poses, object-based storytelling, tasteful flat color accents, and a strong composition. "
            "Use maps, timelines, charts, dossiers, warning cards, machines, rooms, roads, or simplified people when they clarify the idea. "
            "The style should feel professionally illustrated, like a premium educational YouTube animation storyboard, not a child's doodle. "
            "Use short labels only when crucial, but prefer overlay/callout fields for text. Avoid fake paragraphs, logos, and watermarks. "
            "Every visual beat should be animation-friendly: leave room for zooms, pans, popups, arrows, and cutout motion."
        )
    return ""


def save_script(
    topic: str,
    scenes: list[dict[str, Any]],
    run_id: str,
    *,
    visual_assets: dict[str, Any] | None = None,
    image_budget: dict[str, Any] | None = None,
) -> Path:
    path = run_dir(run_id) / "script.json"
    payload = {
        "topic": topic,
        "total_scenes": len(scenes),
        "image_budget": image_budget or {},
        "visual_assets": visual_assets or {"backgrounds": [], "characters": [], "props": []},
        "scenes": scenes,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _loads_json(text: str) -> dict[str, Any]:
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
    cleaned = text.strip()
    cleaned = cleaned.replace("\ufeff", "")
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    cleaned = re.sub(r"\bNone\b", "null", cleaned)
    cleaned = re.sub(r"\bTrue\b", "true", cleaned)
    cleaned = re.sub(r"\bFalse\b", "false", cleaned)
    cleaned = re.sub(r"(?<=[{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", r'"\1":', cleaned)
    return cleaned


def _repair_json_with_anthropic(raw_text: str, *, error: str, client: Any, model: str) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=max(2000, min(12000, len(raw_text) // 2 + 2000)),
        temperature=0,
        system=(
            "You repair malformed JSON. Return only one valid JSON object. "
            "Do not summarize, do not omit fields, do not use markdown."
        ),
        messages=[
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
    )
    return response.content[0].text.strip()


def _repair_scene_payload_with_anthropic(
    raw_text: str,
    *,
    topic: str,
    script: str,
    client: Any,
    model: str,
    scene_count: int,
    shots_min: int,
    shots_max: int,
) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=max(5000, scene_count * shots_max * 450),
        temperature=0,
        system=(
            "You are a strict JSON API. Return exactly one valid JSON object and nothing else. "
            "No markdown fences, no comments, no prose before or after the object."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    "The previous response was valid JSON but did not include the required top-level scenes list, "
                    "probably because it was truncated. Rebuild the complete payload now.\n\n"
                    "Return this exact top-level shape:\n"
                    '{"visual_assets":{"backgrounds":[],"characters":[],"props":[]},"scenes":[]}\n\n'
                    f"Topic: {topic}\n"
                    f"Create exactly {scene_count} scenes. Each scene may use {shots_min} to {shots_max} final shots, but choose fewer if that creates stronger images. "
                    "Keep image_prompt and replicate_prompt concise; do not repeat any global style phrase. "
                    "Keep assets lean: 1 to 2 backgrounds, 1 to 2 characters, and 2 to 4 props for a one-minute scene. "
                    "Do not create assets for one-time extras or background figures; describe them only in the shot prompt. "
                    "Use reusable asset ids with bg_, char_, and prop_ prefixes. "
                    "Each scene and shot should reference relevant background_id, character_ids, and prop_ids. "
                    "Each shot must include text, image_prompt, callout null, overlays empty list, composition, replicate_prompt, "
                    "shot_type, visual_energy, color_palette, and composition_style. "
                    "Allowed shot_type values: hook, establishing, reaction_closeup, object_insert, diagram, before_after, escalation, decision, reveal, payoff. "
                    "Use bold flat color accents and make each shot visually distinct and catchy. "
                    "Do not draw borders, page frames, boxed panel outlines, or enclosing rectangles around the whole image.\n\n"
                    "Previous partial JSON, if useful:\n"
                    f"{raw_text}\n\n"
                    "Narration:\n"
                    f"{script}"
                ),
            }
        ],
    )
    return response.content[0].text.strip()


def _save_bad_json(text: str, run_id: str | None, filename: str) -> None:
    if not run_id:
        return
    try:
        ensure_run_dirs(run_id)
        (run_dir(run_id) / filename).write_text(text, encoding="utf-8")
    except OSError:
        pass


def _normalize_visual_assets(raw_assets: Any, *, config: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    if not isinstance(raw_assets, dict):
        raw_assets = {}

    caps = config.get("visual_assets", {})
    normalized: dict[str, list[dict[str, str]]] = {}
    for group, prefix, default_limit, config_key in (
        ("backgrounds", "bg", 2, "max_backgrounds"),
        ("characters", "char", 2, "max_characters"),
        ("props", "prop", 4, "max_props"),
    ):
        items = raw_assets.get(group)
        if not isinstance(items, list):
            items = []
        limit = _config_int(caps.get(config_key), default_limit) if isinstance(caps, dict) else default_limit
        normalized[group] = _normalize_asset_group(items, prefix=prefix)[: max(0, limit)]
    return normalized


def _normalize_asset_group(items: list[Any], *, prefix: str) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        asset_id = _asset_id(item.get("id") or item.get("asset_id") or item.get("name") or f"{prefix}_{index:02d}", prefix=prefix)
        if asset_id in seen:
            continue
        seen.add(asset_id)
        description = str(item.get("description") or item.get("name") or asset_id).strip()
        prompt = str(item.get("image_prompt") or item.get("prompt") or description or asset_id).strip()
        if prefix == "bg" and "no people" not in prompt.lower():
            prompt = f"{prompt}, empty reusable background plate, no people, no main characters"
        elif prefix == "char" and "reference" not in prompt.lower():
            prompt = f"{prompt}, reusable character reference sheet, neutral pose, consistent outfit and face"
        elif prefix == "prop" and "reference" not in prompt.lower():
            prompt = f"{prompt}, reusable prop reference, isolated clear shape"
        normalized.append(
            {
                "id": asset_id,
                "description": description,
                "image_prompt": _with_style(prompt),
            }
        )
    return normalized


def _normalize_scenes(scenes: list[dict[str, Any]], *, config: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = []
    timed_text_popups = _config_bool(config["script"].get("timed_text_popups"), False)
    keep_callout_candidates = timed_text_popups or _config_bool(config.get("popups", {}).get("enabled"), False)
    hybrid_enabled = _config_bool(config.get("hybrid", {}).get("enabled"), False)
    max_stage_images = int(config.get("hybrid", {}).get("max_stage_images_per_scene", 10))
    _, shots_max = _shot_range(config)
    budget = _image_budget(config, scene_count=max(1, len(scenes)), requested_shots_max=shots_max)
    max_final_shots = budget["max_final_shots_per_scene"]
    for index, scene in enumerate(scenes, start=1):
        box_title = _shorten_box_title(str(scene.get("box_title") or f"Box {index}").strip())
        legacy_prompt = str(scene.get("image_prompt", "")).strip()
        intro_prompt = str(scene.get("intro_image_prompt") or legacy_prompt or box_title).strip()
        intro_prompt = _with_intro_style(intro_prompt)
        stages = _normalize_stages(scene, intro_prompt, max_stage_images=max_stage_images) if hybrid_enabled else []
        shots = _normalize_shots(
            scene,
            intro_prompt,
            keep_callout_candidates=keep_callout_candidates,
            allow_text_overlays=False,
            stages=stages,
            max_shots=max_final_shots,
        )
        text = _ensure_title_lead(_scene_text_from_shots(shots) or str(scene.get("text", "")).strip(), box_title)
        normalized_scene = {
            "id": int(scene.get("id") or index),
            "box_title": box_title,
            "text": text,
            "intro_image_prompt": intro_prompt,
            "image_prompt": intro_prompt,
            "shots": shots,
            "duration_estimate": float(scene.get("duration_estimate") or 6),
        }
        _copy_visual_reference_fields(scene, normalized_scene)
        _copy_visual_direction_fields(scene, normalized_scene)
        if stages:
            normalized_scene["stages"] = stages
        normalized.append(normalized_scene)
    return normalized


def _normalize_stages(
    scene: dict[str, Any],
    fallback_prompt: str,
    *,
    max_stage_images: int,
) -> list[dict[str, str]]:
    raw_stages = scene.get("stages")
    stages: list[dict[str, str]] = []
    if isinstance(raw_stages, list):
        for index, stage in enumerate(raw_stages[:max(1, max_stage_images)], start=1):
            if not isinstance(stage, dict):
                continue
            stage_id = _stage_id(stage.get("id") or stage.get("stage_id") or f"stage_{index:02d}")
            replicate_prompt = str(stage.get("replicate_prompt") or "").strip()
            prompt = str(replicate_prompt or stage.get("image_prompt") or stage.get("prompt") or fallback_prompt).strip()
            prompt = _with_visual_direction(prompt, stage)
            normalized_stage = {"id": stage_id, "image_prompt": _with_style(prompt)}
            if replicate_prompt:
                normalized_stage["replicate_prompt"] = _with_style(replicate_prompt)
            _copy_visual_reference_fields(scene, normalized_stage)
            _copy_visual_reference_fields(stage, normalized_stage)
            _copy_visual_direction_fields(scene, normalized_stage)
            _copy_visual_direction_fields(stage, normalized_stage)
            stages.append(normalized_stage)

    if stages:
        return stages

    seen_prompts: list[str] = []
    raw_shots = scene.get("shots")
    if isinstance(raw_shots, list):
        for shot in raw_shots:
            if not isinstance(shot, dict):
                continue
            prompt = str(shot.get("image_prompt") or "").strip()
            if prompt and prompt not in seen_prompts:
                seen_prompts.append(prompt)
            if len(seen_prompts) >= max(1, max_stage_images):
                break

    if not seen_prompts:
        seen_prompts = [fallback_prompt]

    return [
        {"id": f"stage_{index:02d}", "image_prompt": _with_style(prompt)}
        for index, prompt in enumerate(seen_prompts[: max(1, max_stage_images)], start=1)
    ]


def _normalize_shots(
    scene: dict[str, Any],
    fallback_prompt: str,
    *,
    keep_callout_candidates: bool,
    allow_text_overlays: bool,
    stages: list[dict[str, str]] | None = None,
    max_shots: int | None = None,
) -> list[dict[str, Any]]:
    raw_shots = scene.get("shots")
    if not isinstance(raw_shots, list) or not raw_shots:
        raw_shots = [
            {
                "id": 1,
                "text": str(scene.get("text", "")).strip(),
                "image_prompt": str(scene.get("image_prompt") or fallback_prompt).strip(),
            }
        ]
    raw_shots = _compact_raw_shots(raw_shots, max_shots=max_shots)

    shots = []
    stage_prompts = {stage["id"]: stage["image_prompt"] for stage in stages or []}
    stage_ids = list(stage_prompts)
    for index, shot in enumerate(raw_shots, start=1):
        shot_text = str(shot.get("text", "")).strip()
        requested_stage_id = _stage_id(shot.get("stage_id") or shot.get("stage") or "") if stage_ids else ""
        stage_id = requested_stage_id if requested_stage_id in stage_prompts else ""
        if stage_ids and not stage_id:
            stage_id = stage_ids[(index - 1) % len(stage_ids)]
        fallback_stage_prompt = stage_prompts.get(stage_id, fallback_prompt)
        replicate_prompt = str(shot.get("replicate_prompt") or "").strip()
        prompt_base = str(replicate_prompt or shot.get("image_prompt") or fallback_stage_prompt).strip()
        prompt = _with_style(_with_visual_direction(prompt_base, shot))
        callout = _normalize_callout(shot.get("callout")) if keep_callout_candidates else None
        if callout and not _phrase_is_spoken(callout["text"], shot_text):
            callout = None
        if keep_callout_candidates:
            date_callout = _date_callout(shot_text)
            if date_callout and (callout is None or _is_less_specific_date_callout(callout, date_callout)):
                callout = date_callout
        normalized_shot = {
            "id": int(shot.get("id") or index),
            "text": shot_text,
            "image_prompt": prompt,
            "callout": callout,
            "overlays": [],
        }
        if replicate_prompt:
            normalized_shot["replicate_prompt"] = _with_style(replicate_prompt)
        if stage_id:
            normalized_shot["stage_id"] = stage_id
        _copy_visual_reference_fields(scene, normalized_shot)
        _copy_visual_reference_fields(shot, normalized_shot)
        _copy_visual_direction_fields(scene, normalized_shot)
        _copy_visual_direction_fields(shot, normalized_shot)
        camera = _normalize_camera(shot.get("camera"))
        if camera:
            normalized_shot["camera"] = camera
        shots.append(normalized_shot)
    if stage_ids:
        _limit_stage_repeats(shots, stage_ids, stage_prompts, max_repeats=2)
    return shots


def _scene_text_from_shots(shots: list[dict[str, Any]]) -> str:
    return " ".join(str(shot.get("text", "")).strip() for shot in shots if str(shot.get("text", "")).strip())


def _compact_raw_shots(raw_shots: list[Any], *, max_shots: int | None) -> list[Any]:
    if not max_shots or max_shots <= 0 or len(raw_shots) <= max_shots:
        return raw_shots

    buckets: list[list[dict[str, Any]]] = [[] for _ in range(max_shots)]
    dict_shots = [shot for shot in raw_shots if isinstance(shot, dict)]
    if len(dict_shots) != len(raw_shots):
        return raw_shots[:max_shots]

    total = len(dict_shots)
    for index, shot in enumerate(dict_shots):
        bucket_index = min(max_shots - 1, int(index * max_shots / total))
        buckets[bucket_index].append(shot)

    compacted: list[dict[str, Any]] = []
    for index, bucket in enumerate(buckets, start=1):
        if not bucket:
            continue
        representative = dict(bucket[len(bucket) // 2])
        representative["id"] = index
        representative["text"] = " ".join(str(shot.get("text", "")).strip() for shot in bucket if str(shot.get("text", "")).strip())
        representative["callout"] = _first_bucket_callout(bucket)
        compacted.append(representative)
    return compacted


def _first_bucket_callout(bucket: list[dict[str, Any]]) -> Any:
    for shot in bucket:
        callout = shot.get("callout")
        if callout:
            return callout
    return None


def _copy_visual_reference_fields(source: dict[str, Any], target: dict[str, Any]) -> None:
    background_id = _optional_prefixed_id(source.get("background_id"), prefix="bg")
    if background_id:
        target["background_id"] = background_id

    character_ids = _prefixed_id_list(source.get("character_ids") or source.get("characters"), prefix="char")
    if character_ids:
        target["character_ids"] = character_ids

    prop_ids = _prefixed_id_list(source.get("prop_ids") or source.get("props"), prefix="prop")
    if prop_ids:
        target["prop_ids"] = prop_ids

    reference_ids = _prefixed_id_list(source.get("reference_asset_ids") or source.get("asset_ids"), prefix="")
    if reference_ids:
        target["reference_asset_ids"] = reference_ids

    composition = str(source.get("composition") or "").strip()
    if composition:
        target["composition"] = composition


def _copy_visual_direction_fields(source: dict[str, Any], target: dict[str, Any]) -> None:
    shot_type = _normalize_shot_type(source.get("shot_type"))
    if shot_type:
        target["shot_type"] = shot_type

    visual_energy = _normalize_visual_energy(source.get("visual_energy"))
    if visual_energy:
        target["visual_energy"] = visual_energy

    color_palette = _normalize_color_palette(source.get("color_palette"))
    if color_palette:
        target["color_palette"] = color_palette

    composition_style = str(source.get("composition_style") or "").strip()
    if composition_style:
        target["composition_style"] = composition_style[:180]


def _normalize_shot_type(value: Any) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_ -]+", "", str(value or "").strip().lower())
    cleaned = re.sub(r"[\s-]+", "_", cleaned).strip("_")
    return cleaned if cleaned in SHOT_TYPES else ""


def _normalize_visual_energy(value: Any) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in VISUAL_ENERGIES else ""


def _normalize_color_palette(value: Any) -> list[str]:
    raw_items = value if isinstance(value, list) else ([value] if value else [])
    allowed = {"red", "orange", "yellow", "blue", "green", "purple", "black", "white", "gray", "grey", "pink"}
    colors: list[str] = []
    for item in raw_items:
        color = str(item or "").strip().lower()
        if color == "grey":
            color = "gray"
        if color in allowed and color not in colors:
            colors.append(color)
    return colors[:4]


def _optional_prefixed_id(value: Any, *, prefix: str) -> str:
    if value is None or str(value).strip() == "":
        return ""
    return _asset_id(value, prefix=prefix)


def _prefixed_id_list(value: Any, *, prefix: str) -> list[str]:
    raw_items = value if isinstance(value, list) else ([value] if value else [])
    ids: list[str] = []
    for item in raw_items:
        if item is None or str(item).strip() == "":
            continue
        if prefix:
            asset_id = _asset_id(item, prefix=prefix)
        else:
            cleaned = re.sub(r"[^a-zA-Z0-9_ -]+", "", str(item).strip().lower())
            asset_id = re.sub(r"[\s-]+", "_", cleaned).strip("_")
        if asset_id and asset_id not in ids:
            ids.append(asset_id)
    return ids


def _normalize_callout(callout: Any) -> dict[str, str] | None:
    if not isinstance(callout, dict):
        return None
    text = str(callout.get("text", "")).strip()
    if not text:
        return None
    color = str(callout.get("color") or "red").strip().lower()
    position = str(callout.get("position") or "right").strip().lower()
    if color not in {"red", "yellow", "blue", "green", "black", "white"}:
        color = "red"
    if position not in {"left", "right", "top", "bottom", "center"}:
        position = "right"
    return {"text": text[:40], "color": color, "position": position}


def _date_callout(text: str) -> dict[str, str] | None:
    month_names = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
    month_pattern = "|".join(month_names)
    month_day_year = re.search(
        rf"\b({month_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+((?:1[5-9]\d{{2}}|20\d{{2}}))\b",
        text,
        flags=re.IGNORECASE,
    )
    if month_day_year:
        month = month_day_year.group(1).capitalize()
        day = str(int(month_day_year.group(2)))
        year = month_day_year.group(3)
        return {"text": f"{month} {day}, {year}", "color": "red", "position": "top"}

    day_month_year = re.search(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_pattern})[,]?\s+((?:1[5-9]\d{{2}}|20\d{{2}}))\b",
        text,
        flags=re.IGNORECASE,
    )
    if day_month_year:
        day = str(int(day_month_year.group(1)))
        month = day_month_year.group(2).capitalize()
        year = day_month_year.group(3)
        return {"text": f"{month} {day}, {year}", "color": "red", "position": "top"}

    match = re.search(r"\b(?:1[5-9]\d{2}|20\d{2})\b", text)
    if not match:
        return None
    return {"text": match.group(0), "color": "red", "position": "top"}


def _is_less_specific_date_callout(callout: dict[str, str], date_callout: dict[str, str]) -> bool:
    existing = str(callout.get("text", ""))
    candidate = str(date_callout.get("text", ""))
    return bool(re.fullmatch(r"(?:1[5-9]\d{2}|20\d{2})", existing.strip())) and len(candidate.split()) > 1


def _limit_stage_repeats(
    shots: list[dict[str, Any]],
    stage_ids: list[str],
    stage_prompts: dict[str, str],
    *,
    max_repeats: int,
) -> None:
    if len(stage_ids) < 2:
        return
    streak_id = ""
    streak_count = 0
    for shot in shots:
        stage_id = str(shot.get("stage_id") or "")
        if stage_id == streak_id:
            streak_count += 1
        else:
            streak_id = stage_id
            streak_count = 1
        if streak_count <= max_repeats:
            continue
        try:
            next_index = (stage_ids.index(stage_id) + 1) % len(stage_ids)
        except ValueError:
            next_index = 0
        replacement = stage_ids[next_index]
        shot["stage_id"] = replacement
        shot["image_prompt"] = stage_prompts.get(replacement, shot.get("image_prompt", ""))
        streak_id = replacement
        streak_count = 1


def _stage_id(value: Any) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_ -]+", "", str(value).strip().lower())
    cleaned = re.sub(r"[\s-]+", "_", cleaned).strip("_")
    return cleaned[:48] or "stage"


def _asset_id(value: Any, *, prefix: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_ -]+", "", str(value).strip().lower())
    cleaned = re.sub(r"[\s-]+", "_", cleaned).strip("_")
    cleaned = cleaned[:56].strip("_") or f"{prefix}_asset"
    if not cleaned.startswith(f"{prefix}_"):
        cleaned = f"{prefix}_{cleaned}"
    return cleaned


def _normalize_camera(value: Any) -> str | None:
    camera = str(value or "").strip().lower()
    allowed = {"wide", "medium", "close", "left", "right", "center"}
    return camera if camera in allowed else None


def _normalize_overlays(overlays: Any, *, allow_text: bool = False) -> list[dict[str, str]]:
    if not isinstance(overlays, list):
        return []
    normalized = []
    for overlay in overlays:
        if not isinstance(overlay, dict):
            continue
        overlay_type = str(overlay.get("type", "")).strip().lower()
        if overlay_type == "text" and not allow_text:
            continue
        if overlay_type not in {"text", "symbol"}:
            continue
        item = {
            "type": overlay_type,
            "position": _overlay_position(str(overlay.get("position") or "center")),
            "color": _overlay_color(str(overlay.get("color") or "red")),
            "style": _overlay_style(str(overlay.get("style") or "normal")),
        }
        if overlay_type == "text":
            text = str(overlay.get("text", "")).strip()
            if not text:
                continue
            item["text"] = text[:60]
        else:
            symbol = str(overlay.get("symbol", "")).strip().lower()
            if symbol not in {
                "warning",
                "arrow",
                "x",
                "circle",
                "box",
                "flag_canada",
                "flag_india",
                "flag_pakistan",
            }:
                continue
            item["symbol"] = symbol
        normalized.append(item)
    return normalized[:4]


def _overlay_position(position: str) -> str:
    allowed = {"top_left", "top", "top_right", "left", "center", "right", "bottom_left", "bottom", "bottom_right"}
    return position if position in allowed else "center"


def _overlay_color(color: str) -> str:
    allowed = {"red", "yellow", "blue", "green", "black", "white", "gray", "orange"}
    return color if color in allowed else "red"


def _overlay_style(style: str) -> str:
    allowed = {"normal", "crossed_out", "outline", "small", "large"}
    return style if style in allowed else "normal"


def _config_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _config_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _config_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ensure_title_lead(text: str, box_title: str) -> str:
    if not box_title:
        return text
    if text.lower().startswith(box_title.lower()):
        return text
    return f"{box_title}. {text}".strip()


def _shorten_box_title(title: str) -> str:
    title = " ".join(title.split())
    if ":" in title:
        title = title.rsplit(":", 1)[1].strip()
    title = re.sub(r"^(the\s+man\s+who\s+almost\s+had\s+to\s+end\s+the\s+world\s*:?\s*)", "", title, flags=re.IGNORECASE)
    words = title.split()
    if len(words) > 8:
        title = " ".join(words[:8])
    return title or "Box"


def _phrase_is_spoken(phrase: str, text: str) -> bool:
    phrase_words = re.findall(r"[a-zA-Z0-9']+", phrase.lower())
    text_words = re.findall(r"[a-zA-Z0-9']+", text.lower())
    if not phrase_words:
        return False
    for index in range(0, len(text_words) - len(phrase_words) + 1):
        if text_words[index : index + len(phrase_words)] == phrase_words:
            return True
    return False


def _with_style(prompt: str) -> str:
    prompt = _strip_asset_ids_for_prompt(prompt)
    prompt = _strip_border_language(prompt)
    if _looks_like_doodle_prompt(prompt):
        if "polished hand-drawn documentary doodle explainer style" not in prompt.lower():
            prompt = f"{prompt}, {DOODLE_STYLE_SUFFIX}".strip(", ")
        return prompt
    if "documentary collage explainer style" not in prompt.lower():
        prompt = f"{prompt}, {STYLE_SUFFIX}".strip(", ")
    return prompt


def _looks_like_doodle_prompt(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(
        phrase in lowered
        for phrase in (
            "doodle",
            "hand-drawn",
            "hand drawn",
            "ink linework",
            "sketchbook",
            "educational animation storyboard",
        )
    )


def _strip_asset_ids_for_prompt(prompt: str) -> str:
    cleaned = re.sub(r"\bbg_[a-zA-Z0-9_]+\b", "the referenced background", prompt)
    cleaned = re.sub(r"\bchar_[a-zA-Z0-9_]+\b", "the referenced character", cleaned)
    cleaned = re.sub(r"\bprop_[a-zA-Z0-9_]+\b", "the referenced prop", cleaned)
    return cleaned


def _strip_border_language(prompt: str) -> str:
    protected = {
        "__NO_DECORATIVE_BORDER__": "no decorative border",
        "__NO_DRAWN_FRAME__": "no drawn frame around the whole image",
        "__NO_BOXED_PANEL__": "no boxed panel outline",
        "__NO_ENCLOSING_RECTANGLE__": "no enclosing rectangle",
    }
    cleaned = prompt
    for placeholder, phrase in protected.items():
        cleaned = re.sub(re.escape(phrase), placeholder, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:decorative\s+)?border\s+frame\b", "open background", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bboxed\s+panel\s+outline\b", "open composition", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bpage\s+frame\b", "open background", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\benclosing\s+rectangle\b", "open composition", cleaned, flags=re.IGNORECASE)
    for placeholder, phrase in protected.items():
        cleaned = cleaned.replace(placeholder, phrase)
    return cleaned


def _with_visual_direction(prompt: str, item: dict[str, Any]) -> str:
    parts = []
    shot_type = _normalize_shot_type(item.get("shot_type"))
    if shot_type:
        parts.append(f"shot type: {shot_type.replace('_', ' ')}")
    energy = _normalize_visual_energy(item.get("visual_energy"))
    if energy:
        parts.append(f"visual energy: {energy}")
    palette = _normalize_color_palette(item.get("color_palette"))
    if palette:
        parts.append(f"use bold flat color accents from this palette: {', '.join(palette)}")
    composition_style = str(item.get("composition_style") or "").strip()
    if composition_style:
        parts.append(f"composition style: {composition_style[:180]}")
    if not parts:
        return prompt
    direction = "; ".join(parts)
    if direction.lower() in prompt.lower():
        return prompt
    return f"{direction}. {prompt}".strip()


def _with_intro_style(prompt: str) -> str:
    prompt = _with_style(prompt)
    intro_suffix = "broad ambient establishing thumbnail, no people, no close-up face, no title banner, no readable text"
    if intro_suffix not in prompt:
        prompt = f"{prompt}, {intro_suffix}"
    return prompt


def _mock_scenes(topic: str, scene_count: int) -> list[dict[str, Any]]:
    beats = [
        f"Imagine hearing about {topic} for the first time, and realizing the simple version hides a much stranger story.",
        "The key idea starts small, with one ordinary cause setting off a chain of effects.",
        "Then the system begins to amplify itself, turning a manageable problem into a visible pattern.",
        "The surprising part is that every piece still follows common sense once you slow it down.",
        "A single example makes the whole thing click: one action changes the environment for the next action.",
        "That is why experts care about it. The danger is not one dramatic moment, but the feedback loop.",
        f"So {topic} is really a lesson about thresholds: once enough small things pile up, the rules change.",
    ]
    scenes = []
    for index in range(scene_count):
        text = beats[index % len(beats)]
        box_title = _mock_box_title(topic, index)
        text = _ensure_title_lead(text, box_title)
        first_half, second_half = _split_words_in_half(text)
        shots = [
            {
                "id": 1,
                "type": "image",
                "text": first_half,
                "background_id": "bg_mock_room",
                "character_ids": ["char_mock_narrator"],
                "prop_ids": ["prop_mock_signal"],
                "shot_type": "hook",
                "visual_energy": "surprise",
                "color_palette": ["yellow", "blue", "red"],
                "composition_style": "big foreground signal prop with tiny confused narrator cutout",
                "composition": "mock narrator cutout stands in the reusable room beside a simple signal prop",
                "replicate_prompt": f"Use bg_mock_room as the background. Place char_mock_narrator in the room beside prop_mock_signal for the opening beat about {topic}. Preserve the documentary collage style.",
                "image_prompt": f"opening documentary collage for {topic}, chapter {index + 1}, {STYLE_SUFFIX}",
                "callout": {"text": "1999", "color": "red", "position": "right"} if index == 0 else None,
                "overlays": [{"type": "symbol", "symbol": "warning", "position": "center", "color": "yellow"}] if index == 0 else [],
            },
            {
                "id": 2,
                "type": "image",
                "text": second_half,
                "background_id": "bg_mock_room",
                "character_ids": ["char_mock_narrator"],
                "prop_ids": ["prop_mock_signal"],
                "shot_type": "payoff",
                "visual_energy": "high",
                "color_palette": ["green", "yellow", "blue"],
                "composition_style": "reaction closeup with simple before-after feeling",
                "composition": "same mock room, narrator cutout reacts to the signal prop",
                "replicate_prompt": f"Use bg_mock_room as the background. Keep char_mock_narrator consistent and show a reaction beside prop_mock_signal for the consequence beat about {topic}. Preserve the documentary collage style.",
                "image_prompt": f"consequence documentary collage for {topic}, chapter {index + 1}, {STYLE_SUFFIX}",
                "callout": {"text": "NUCLEAR RISK", "color": "yellow", "position": "top"} if index == 0 else None,
                "overlays": [{"type": "symbol", "symbol": "x", "position": "center", "color": "red", "style": "large"}] if index == 0 else [],
            },
        ]
        scenes.append(
            {
                "id": index + 1,
                "box_title": box_title,
                "text": text,
                "intro_image_prompt": f"simple documentary collage thumbnail about {topic}, chapter {index + 1}, {STYLE_SUFFIX}",
                "shots": shots,
                "duration_estimate": 6,
            }
        )
    return scenes


def _mock_visual_assets(topic: str) -> dict[str, list[dict[str, str]]]:
    return {
        "backgrounds": [
            {
                "id": "bg_mock_room",
                "description": f"Simple reusable documentary room background for {topic}",
                "image_prompt": f"empty simple documentary collage room background for {topic}, no people, {STYLE_SUFFIX}",
            }
        ],
        "characters": [
            {
                "id": "char_mock_narrator",
                "description": "Simple anonymous narrator cutout reference",
                "image_prompt": f"single anonymous narrator cutout character reference for {topic}, neutral pose, {STYLE_SUFFIX}",
            }
        ],
        "props": [
            {
                "id": "prop_mock_signal",
                "description": "Simple signal prop reference",
                "image_prompt": f"simple signal line prop reference for {topic}, isolated object, documentary collage style, {STYLE_SUFFIX}",
            }
        ],
    }


def _mock_box_title(topic: str, index: int) -> str:
    if ":" in topic and index == 0:
        return topic.split(":", 1)[1].strip()
    return f"Box {index + 1}"


def _split_words_in_half(text: str) -> tuple[str, str]:
    words = text.split()
    midpoint = max(1, len(words) // 2)
    return " ".join(words[:midpoint]), " ".join(words[midpoint:])
