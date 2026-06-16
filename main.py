from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.graph import run_video_graph


def run(
    topic: str,
    *,
    run_id: str | None = None,
    mock: bool = False,
    duration: int | None = None,
    scene_count: int | None = None,
    seconds_per_box: int | None = None,
    shots_min: int | None = None,
    shots_max: int | None = None,
    box_mode: bool = False,
    doodle_mode: bool = False,
    image_model: str | None = None,
) -> Path:
    return run_video_graph(
        topic,
        run_id=run_id,
        mock=mock,
        duration_seconds=duration,
        scene_count=scene_count,
        seconds_per_box=seconds_per_box,
        shots_min=shots_min,
        shots_max=shots_max,
        box_mode=box_mode,
        doodle_mode=doodle_mode,
        image_model=image_model,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a narrated video from a topic.")
    parser.add_argument("topic", help="Video topic, for example: The Kessler Syndrome")
    parser.add_argument("--run-id", help="Optional fixed run id for reproducible output folders")
    parser.add_argument(
        "--duration",
        type=int,
        help="Approximate final video duration in seconds. Used to size the narration script.",
    )
    parser.add_argument(
        "--scene-count",
        "--boxes",
        dest="scene_count",
        type=int,
        help="Number of scenes/boxes to generate. For a paid smoke test, use 1.",
    )
    parser.add_argument(
        "--seconds-per-box",
        type=int,
        help="Approximate narration duration per box in box mode. Default is 120 seconds.",
    )
    parser.add_argument(
        "--box-mode",
        action="store_true",
        help="Prompt the script as a Paint Explainer-style grid/box video.",
    )
    parser.add_argument(
        "--doodle-mode",
        action="store_true",
        help="Prompt the run as a polished hand-drawn doodle explainer.",
    )
    parser.add_argument(
        "--image-model",
        help="Optional Replicate image model override, for example recraft-ai/recraft-v3.",
    )
    parser.add_argument("--shots-min", type=int, help="Override minimum visual beats per scene/box. Defaults to auto.")
    parser.add_argument("--shots-max", type=int, help="Override maximum visual beats per scene/box. Defaults to auto.")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use placeholder script/images/audio instead of external AI APIs.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    run(
        args.topic,
        run_id=args.run_id,
        mock=args.mock,
        duration=args.duration,
        scene_count=args.scene_count,
        seconds_per_box=args.seconds_per_box,
        shots_min=args.shots_min,
        shots_max=args.shots_max,
        box_mode=args.box_mode,
        doodle_mode=args.doodle_mode,
        image_model=args.image_model,
    )
