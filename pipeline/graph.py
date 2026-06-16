from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from langsmith import traceable

from . import final_assembler, grid_intro, image_gen, scene_assembler, script_gen, tts
from .config import load_config, load_environment
from .media import ensure_media_tools
from .paths import ensure_run_dirs
from .tts import AudioResult


class VideoState(TypedDict, total=False):
    topic: str
    run_id: str
    run_dir: Path
    mock: bool
    duration_seconds: int | None
    scene_count: int | None
    seconds_per_box: int | None
    shots_min: int | None
    shots_max: int | None
    box_mode: bool
    doodle_mode: bool
    image_model: str | None
    config: dict[str, Any]
    scenes: list[dict[str, Any]]
    visual_assets: dict[str, Any]
    image_paths: list[Path]
    image_assets: dict[str, Any]
    audio_results: list[AudioResult]
    intro_path: Path | None
    scene_clips: list[Path]
    final_path: Path


def run_video_graph(
    topic: str,
    *,
    run_id: str | None = None,
    mock: bool = False,
    duration_seconds: int | None = None,
    scene_count: int | None = None,
    seconds_per_box: int | None = None,
    shots_min: int | None = None,
    shots_max: int | None = None,
    box_mode: bool = False,
    doodle_mode: bool = False,
    image_model: str | None = None,
) -> Path:
    load_environment()
    app = build_video_graph()
    initial_state: VideoState = {
        "topic": topic,
        "run_id": run_id or uuid.uuid4().hex[:8],
        "mock": mock,
        "duration_seconds": duration_seconds,
        "scene_count": scene_count,
        "seconds_per_box": seconds_per_box,
        "shots_min": shots_min,
        "shots_max": shots_max,
        "box_mode": box_mode,
        "doodle_mode": doodle_mode,
        "image_model": image_model,
    }
    result = app.invoke(
        initial_state,
        config={
            "run_name": "youtube_video_pipeline",
            "tags": ["yt-auto", "mock" if mock else "real"],
            "metadata": {
                "topic": topic,
                "run_id": initial_state["run_id"],
                "duration_seconds": duration_seconds,
                "scene_count": scene_count,
                "seconds_per_box": seconds_per_box,
                "shots_min": shots_min,
                "shots_max": shots_max,
                "box_mode": box_mode,
                "doodle_mode": doodle_mode,
                "image_model": image_model,
            },
        },
    )
    return result["final_path"]


def build_video_graph():
    graph = StateGraph(VideoState)
    graph.add_node("setup", setup)
    graph.add_node("script", generate_script)
    graph.add_node("assets", generate_assets)
    graph.add_node("intro", build_intro)
    graph.add_node("scenes", render_scenes)
    graph.add_node("final", assemble_final)

    graph.set_entry_point("setup")
    graph.add_edge("setup", "script")
    graph.add_edge("script", "assets")
    graph.add_edge("assets", "intro")
    graph.add_edge("intro", "scenes")
    graph.add_edge("scenes", "final")
    graph.add_edge("final", END)
    return graph.compile()


@traceable(name="setup")
def setup(state: VideoState) -> VideoState:
    config = load_config()
    run_id = state["run_id"]
    run_dir = ensure_run_dirs(run_id)
    _apply_run_overrides(config, state)

    print(f"[{run_id}] Starting pipeline for: {state['topic']}")
    ensure_media_tools()
    return {"config": config, "run_dir": run_dir}


def _apply_run_overrides(config: dict[str, Any], state: VideoState) -> None:
    scene_count = state.get("scene_count")
    if scene_count:
        config["script"]["target_scene_count"] = max(1, int(scene_count))
    if state.get("seconds_per_box"):
        config["script"]["target_seconds_per_scene"] = max(30, int(state["seconds_per_box"] or 120))
    if state.get("shots_min"):
        config["script"]["shots_per_scene_min"] = max(1, int(state["shots_min"] or 1))
    if state.get("shots_max"):
        config["script"]["shots_per_scene_max"] = max(
            _config_int(config["script"].get("shots_per_scene_min"), 1),
            int(state["shots_max"] or 1),
        )

    duration_seconds = state.get("duration_seconds")
    words_per_second = float(config["script"].get("words_per_second", 2.35))
    if duration_seconds:
        intro_seconds = 0
        if config["intro"].get("enabled", True):
            intro_seconds = (
                float(config["intro"].get("pan_seconds", 0))
                + float(config["intro"].get("zoom_seconds", 0))
                + float(config["intro"].get("hold_seconds", 0))
            )
        narration_seconds = max(15, int(duration_seconds) - intro_seconds)
        config["script"]["target_word_count"] = int(narration_seconds * words_per_second)
    elif state.get("box_mode"):
        seconds_per_scene = float(config["script"].get("target_seconds_per_scene", 120))
        scene_total = int(config["script"].get("target_scene_count", 1))
        config["script"]["target_word_count"] = int(scene_total * seconds_per_scene * words_per_second)

    if state.get("box_mode"):
        config["script"]["format"] = "box_explainer"
    if state.get("doodle_mode"):
        config["script"]["format"] = "doodle_explainer"
        config["image"]["style"] = "Polished hand-drawn documentary doodle explainer"
        config["image"]["add_title_banner"] = False
        config["image"]["add_intro_title_banner"] = False
    if state.get("image_model"):
        config["image"]["model"] = str(state["image_model"]).strip()


def _config_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@traceable(name="generate_script")
def generate_script(state: VideoState) -> VideoState:
    run_id = state["run_id"]
    script_payload = script_gen.generate_payload(
        state["topic"],
        run_id,
        config=state["config"],
        mock=bool(state.get("mock")),
    )
    scenes = script_payload["scenes"]
    print(f"[{run_id}] Script ready with {len(scenes)} scenes")
    return {"scenes": scenes, "visual_assets": script_payload.get("visual_assets", {})}


@traceable(name="generate_assets")
def generate_assets(state: VideoState) -> VideoState:
    run_id = state["run_id"]
    scenes = state["scenes"]

    shot_count = sum(len(scene.get("shots", [])) for scene in scenes)
    if state["config"].get("hybrid", {}).get("enabled", False):
        stage_count = sum(len(scene.get("stages", [])) for scene in scenes)
        print(f"[{run_id}] Generating {len(scenes)} intro images and {stage_count} stage images for {shot_count} shots")
    else:
        print(f"[{run_id}] Generating {len(scenes)} intro images and {shot_count} shot images")
    image_assets = image_gen.generate_assets(
        scenes,
        run_id,
        config=state["config"],
        mock=bool(state.get("mock")),
        visual_assets=state.get("visual_assets", {}),
    )

    print(f"[{run_id}] Generating narration audio")
    audio_results = tts.generate_all(
        scenes,
        run_id,
        config=state["config"],
        mock=bool(state.get("mock")),
    )
    return {"image_paths": image_assets["intro"], "image_assets": image_assets, "audio_results": audio_results}


@traceable(name="build_intro")
def build_intro(state: VideoState) -> VideoState:
    config = state["config"]
    if not config["intro"].get("enabled", True):
        return {"intro_path": None}

    run_id = state["run_id"]
    print(f"[{run_id}] Building grid intro")
    intro_path = grid_intro.build(
        state["image_assets"]["intro"],
        topic=state["topic"],
        run_id=run_id,
        config=config,
        topic_index=int(config["intro"].get("target_index", 0)),
        labels=[str(scene.get("box_title") or f"Box {scene['id']}") for scene in state["scenes"]],
    )
    return {"intro_path": intro_path}


@traceable(name="render_scenes")
def render_scenes(state: VideoState) -> VideoState:
    run_id = state["run_id"]
    print(f"[{run_id}] Rendering scene clips")
    scene_clips = []
    for scene, audio_result in zip(state["scenes"], state["audio_results"]):
        scene_id = int(scene["id"])
        scene_clips.append(
            scene_assembler.assemble(
                image_paths=state["image_assets"]["shots"][scene_id],
                shots=scene.get("shots", []),
                audio_path=audio_result.path,
                word_timestamps_path=audio_result.word_timestamps_path,
                scene_id=scene_id,
                run_id=run_id,
                config=state["config"],
            )
        )
    return {"scene_clips": scene_clips}


@traceable(name="assemble_final")
def assemble_final(state: VideoState) -> VideoState:
    run_id = state["run_id"]
    print(f"[{run_id}] Assembling final video")

    intro_path = state.get("intro_path")
    clips = [intro_path, *state["scene_clips"]] if intro_path else state["scene_clips"]
    final_path = final_assembler.assemble(clips, run_id, config=state["config"])

    print(f"[{run_id}] Done: {final_path}")
    print(f"[{run_id}] Run folder: {state['run_dir']}")
    return {"final_path": final_path}
