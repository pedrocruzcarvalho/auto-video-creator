from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.video_gen import estimate_pvideo_cost, generate_pvideo_clip


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a single Pruna p-video test clip.")
    parser.add_argument("prompt", help="Video prompt to send to prunaai/p-video.")
    parser.add_argument("--run-id", default="pvideo_test", help="Output folder under output/<run_id>/")
    parser.add_argument("--clip-key", default="clip_01", help="Output filename key under clips/")
    parser.add_argument("--image", help="Optional input image for image-to-video.")
    parser.add_argument("--duration", type=int, default=5, help="Clip duration in seconds, 1-20.")
    parser.add_argument("--resolution", choices=["720p", "1080p"], default="720p")
    parser.add_argument("--fps", type=int, choices=[24, 48], default=24)
    parser.add_argument("--draft", action="store_true", help="Use cheaper/faster draft mode.")
    parser.add_argument("--save-audio", action="store_true", help="Keep model-generated audio. Off by default.")
    parser.add_argument("--prompt-upsampling", action="store_true", help="Let the model expand the prompt.")
    parser.add_argument("--seed", type=int)
    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    args = parse_args(argv)
    estimate = estimate_pvideo_cost(seconds=args.duration, resolution=args.resolution, draft=args.draft)
    print(f"Estimated p-video cost: ${estimate:.4f}")
    path = generate_pvideo_clip(
        prompt=args.prompt,
        run_id=args.run_id,
        clip_key=args.clip_key,
        image_path=Path(args.image) if args.image else None,
        duration=args.duration,
        resolution=args.resolution,
        fps=args.fps,
        draft=args.draft,
        save_audio=args.save_audio,
        prompt_upsampling=args.prompt_upsampling,
        seed=args.seed,
    )
    print(f"Saved: {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
