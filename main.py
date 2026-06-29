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


CLI_PRESETS = {
    "warehouse_shelf_collapse": {
        "topic": "Warehouse shelf starts collapsing beside you",
        "run_id": "warehouse_shelf_collapse_v1",
        "script_part_1": (
            "You are walking through a warehouse aisle when a loaded shelf starts tipping toward you. "
            "Do not run straight down the aisle. The boxes will chase the same path. "
            "Drop low and grab the pallet jack beside you."
        ),
        "script_part_2": (
            "Pull the pallet jack across the aisle like a shield. "
            "The first boxes hit it and bounce sideways for one second. "
            "Slide under the lowest shelf gap and roll into the open space."
        ),
        "clip_1_visual": (
            "Vertical 9:16 glossy viral 3D survival simulation, clearly fictional CGI. "
            "Same adult male training avatar, brown hair, gray work shirt, realistic hands, bright clean warehouse training aisle, "
            "tall metal shelf stacked with cardboard boxes, low red pallet jack beside him, polished concrete floor, strong readable action. "
            "Dynamic camera, fast push-ins, snap zooms, macro object close-ups. Camera always points to the exact narrated object. "
            "Not live-action, not real accident footage, not children's cartoon, not Pixar. No on-screen text, no captions, no letters, no numbers, "
            "no logos, no signs, no UI, no watermark.\n\n"
            "Create part 1 of a continuous fictional warehouse shelf collapse survival simulation. Native serious male narrator plus shelf creaks, "
            "box slides, metal rattles, fast whooshes, and bass impacts.\n\n"
            "0-2s: camera rushes down a bright warehouse aisle as a tall shelf begins tipping toward the avatar.\n"
            "2-5s: snap zoom to stacked boxes sliding off the upper shelf, moving toward camera.\n"
            "5-8s: show the avatar almost running straight down the aisle, then stopping as boxes fall into that path.\n"
            "8-11s: whip pan and push-in to a low red pallet jack beside his leg.\n"
            "11-15s: he drops low, grabs the pallet jack handle with both hands, and pulls it across the aisle, ending on the jack between him and the falling boxes."
        ),
        "clip_2_visual": (
            "Vertical 9:16 glossy viral 3D survival simulation, clearly fictional CGI. Continue exactly from the input first frame. "
            "Same bright warehouse aisle, same gray-work-shirt avatar, same tipping metal shelf, same low red pallet jack between him and the falling boxes. "
            "Dynamic camera, fast push-ins, snap zooms, macro object close-ups. Camera always points to the exact narrated object. "
            "No on-screen text, no captions, no letters, no numbers, no logos, no signs, no UI, no watermark.\n\n"
            "Continue the warehouse shelf collapse survival simulation with native serious male narrator plus pallet jack scrape, box impacts, metal clank, "
            "short alarm chirp, and final relief hit.\n\n"
            "0-3s: begin on the pallet jack blocking the aisle as the first boxes slam into it and bounce sideways.\n"
            "3-6s: macro close-up of wheels sliding across concrete while cardboard boxes burst around the jack.\n"
            "6-10s: camera snap zooms to the low gap under the bottom shelf; the avatar dives and slides under it.\n"
            "10-13s: camera follows his shoulder and hands as he rolls out into an open safe space beside the shelf.\n"
            "13-15s: final wide shot shows the boxes crashing into the pallet jack while he is clear on the other side."
        ),
    }
}


def run(
    topic: str,
    *,
    run_id: str | None = None,
    preset: str | None = None,
    resolution: str = "720p",
    seed: int = 42420,
    no_captions: bool = False,
    no_voice_reference: bool = False,
    fresh: bool = False,
) -> Path:
    selected = CLI_PRESETS[preset] if preset else _preset_for_topic(topic)
    result = run_seedance_native_pipeline(
        SeedanceOptions(
            run_id=run_id or selected.get("run_id") or _default_run_id(topic),
            script_part_1=selected["script_part_1"],
            script_part_2=selected["script_part_2"],
            clip_1_visual=selected["clip_1_visual"],
            clip_2_visual=selected["clip_2_visual"],
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
    parser = argparse.ArgumentParser(description="Generate a Seedance-native Extreme Survival Short.")
    parser.add_argument("topic", nargs="?", default="Sinking car water tank", help="Used for the output folder name.")
    parser.add_argument("--preset", choices=sorted(CLI_PRESETS), help="Use a built-in terminal preset.")
    parser.add_argument("--run-id", help="Optional fixed output folder under output/<run_id>.")
    parser.add_argument("--resolution", choices=["480p", "720p", "1080p"], default="720p")
    parser.add_argument("--seed", type=int, default=42420)
    parser.add_argument("--no-captions", action="store_true", help="Skip Whisper transcription and burned subtitles.")
    parser.add_argument("--no-voice-reference", action="store_true", help="Compatibility flag. Last-frame mode already disables Seedance audio/video references.")
    parser.add_argument("--fresh", action="store_true", help="Regenerate clips even if files already exist.")
    return parser.parse_args(argv)


def _default_run_id(topic: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", topic.lower())
    slug = "_".join(words[:8]) or "seedance_short"
    return slug[:80]


def _preset_for_topic(topic: str) -> dict[str, str]:
    normalized = _default_run_id(topic)
    for key, preset in CLI_PRESETS.items():
        if key in normalized or normalized in key:
            return preset
    return {
        "topic": topic,
        "run_id": _default_run_id(topic),
        "script_part_1": DEFAULT_SCRIPT_PART_1,
        "script_part_2": DEFAULT_SCRIPT_PART_2,
        "clip_1_visual": DEFAULT_CLIP_1_VISUAL,
        "clip_2_visual": DEFAULT_CLIP_2_VISUAL,
    }


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    run(
        args.topic,
        run_id=args.run_id,
        preset=args.preset,
        resolution=args.resolution,
        seed=args.seed,
        no_captions=args.no_captions,
        no_voice_reference=args.no_voice_reference,
        fresh=args.fresh,
    )

