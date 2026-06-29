from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from pipeline.seedance_native import (
    DEFAULT_CLIP_1_VISUAL,
    DEFAULT_CLIP_2_VISUAL,
    DEFAULT_SCRIPT_PART_1,
    DEFAULT_SCRIPT_PART_2,
    SeedanceOptions,
    run_seedance_native_pipeline,
)


def run(
    topic: str,
    *,
    run_id: str | None = None,
    resolution: str = "720p",
    seed: int = 42420,
    no_captions: bool = False,
    no_voice_reference: bool = False,
    fresh: bool = False,
) -> Path:
    result = run_seedance_native_pipeline(
        SeedanceOptions(
            run_id=run_id or _default_run_id(topic),
            script_part_1=DEFAULT_SCRIPT_PART_1,
            script_part_2=DEFAULT_SCRIPT_PART_2,
            clip_1_visual=DEFAULT_CLIP_1_VISUAL,
            clip_2_visual=DEFAULT_CLIP_2_VISUAL,
            resolution=resolution,
            seed=seed,
            add_captions=not no_captions,
            use_voice_reference=not no_voice_reference,
            resume=not fresh,
        )
    )
    final_path = Path(result["final_path"])
    print(final_path)
    return final_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Seedance-native Exit Scenario Short.")
    parser.add_argument("topic", nargs="?", default="Sinking car water tank", help="Used for the output folder name.")
    parser.add_argument("--run-id", help="Optional fixed output folder under output/<run_id>.")
    parser.add_argument("--resolution", choices=["480p", "720p", "1080p"], default="720p")
    parser.add_argument("--seed", type=int, default=42420)
    parser.add_argument("--no-captions", action="store_true", help="Skip Whisper transcription and burned subtitles.")
    parser.add_argument("--no-voice-reference", action="store_true", help="Do not pass clip 1 audio into clip 2.")
    parser.add_argument("--fresh", action="store_true", help="Regenerate clips even if files already exist.")
    return parser.parse_args(argv)


def _default_run_id(topic: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", topic.lower())
    slug = "_".join(words[:8]) or "seedance_short"
    return slug[:80]


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    run(
        args.topic,
        run_id=args.run_id,
        resolution=args.resolution,
        seed=args.seed,
        no_captions=args.no_captions,
        no_voice_reference=args.no_voice_reference,
        fresh=args.fresh,
    )
