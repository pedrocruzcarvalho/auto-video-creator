from __future__ import annotations

from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.asset_resolver import AMBIENCE_DIR, SFX_DIR, ensure_asset_dirs
from pipeline.media import run_ffmpeg


def main() -> None:
    ensure_asset_dirs()
    _sfx()
    _ambience()
    print(f"Wrote SFX assets to {SFX_DIR}")
    print(f"Wrote ambience assets to {AMBIENCE_DIR}")


def _sfx() -> None:
    _render(
        SFX_DIR / "camera_click.wav",
        (
            "anoisesrc=color=white:duration=0.045:sample_rate=44100,highpass=f=1900,lowpass=f=7600,volume=0.95[n];"
            "sine=frequency=130:duration=0.050,volume=0.32[b];"
            "sine=frequency=2850:duration=0.022,volume=0.20[h];"
            "[n][b][h]amix=inputs=3,afade=t=out:st=0.055:d=0.055[out]"
        ),
        0.16,
    )
    _render(
        SFX_DIR / "typewriter_tick.wav",
        (
            "anoisesrc=color=white:duration=0.022:sample_rate=44100,highpass=f=2400,lowpass=f=7800,volume=0.72[n];"
            "sine=frequency=1750:duration=0.018,volume=0.18[t];"
            "[n][t]amix=inputs=2,afade=t=out:st=0.018:d=0.018[out]"
        ),
        0.07,
    )
    _render(
        SFX_DIR / "glitch_burst.wav",
        (
            "anoisesrc=color=pink:duration=0.130:sample_rate=44100,highpass=f=850,lowpass=f=8200,volume=0.55[n];"
            "sine=frequency=82:duration=0.110,volume=0.12[b];"
            "[n][b]amix=inputs=2,acrusher=level_in=1:level_out=0.65:bits=8:mode=log,afade=t=out:st=0.10:d=0.035[out]"
        ),
        0.18,
    )
    _render(
        SFX_DIR / "boom.wav",
        "sine=frequency=54:duration=0.420,volume=0.72,afade=t=out:st=0.05:d=0.36[out]",
        0.46,
    )
    _render(
        SFX_DIR / "whoosh.wav",
        "anoisesrc=color=pink:duration=0.420:sample_rate=44100,highpass=f=450,lowpass=f=6200,volume=0.42,afade=t=in:st=0:d=0.18,afade=t=out:st=0.25:d=0.16[out]",
        0.46,
    )


def _ambience() -> None:
    _render(
        AMBIENCE_DIR / "low_drone.wav",
        (
            "sine=frequency=48:duration=20,volume=0.34[a0];"
            "sine=frequency=96:duration=20,volume=0.12[a1];"
            "anoisesrc=color=brown:duration=20:sample_rate=44100,lowpass=f=380,volume=0.18[a2];"
            "[a0][a1][a2]amix=inputs=3,afade=t=in:st=0:d=1.0,afade=t=out:st=19:d=1[out]"
        ),
        20,
    )
    _render(
        AMBIENCE_DIR / "room_tone.wav",
        "anoisesrc=color=pink:duration=20:sample_rate=44100,highpass=f=80,lowpass=f=1400,volume=0.22,afade=t=in:st=0:d=1.0,afade=t=out:st=19:d=1[out]",
        20,
    )
    _render(
        AMBIENCE_DIR / "industrial_hum.wav",
        (
            "sine=frequency=58:duration=20,volume=0.30[a0];"
            "sine=frequency=116:duration=20,volume=0.13[a1];"
            "anoisesrc=color=brown:duration=20:sample_rate=44100,lowpass=f=520,volume=0.16[a2];"
            "[a0][a1][a2]amix=inputs=3,afade=t=in:st=0:d=1.0,afade=t=out:st=19:d=1[out]"
        ),
        20,
    )
    _render(
        AMBIENCE_DIR / "surveillance_noise.wav",
        (
            "anoisesrc=color=pink:duration=20:sample_rate=44100,highpass=f=1000,lowpass=f=5400,volume=0.14[a0];"
            "sine=frequency=1560:duration=20,volume=0.035[a1];"
            "[a0][a1]amix=inputs=2,afade=t=in:st=0:d=1.0,afade=t=out:st=19:d=1[out]"
        ),
        20,
    )
    _render(
        AMBIENCE_DIR / "distant_wind.wav",
        "anoisesrc=color=brown:duration=20:sample_rate=44100,highpass=f=100,lowpass=f=1150,volume=0.26,afade=t=in:st=0:d=1.0,afade=t=out:st=19:d=1[out]",
        20,
    )
    _render(
        AMBIENCE_DIR / "courthouse_room.wav",
        (
            "anoisesrc=color=pink:duration=20:sample_rate=44100,highpass=f=120,lowpass=f=1900,volume=0.16[a0];"
            "sine=frequency=72:duration=20,volume=0.08[a1];"
            "[a0][a1]amix=inputs=2,afade=t=in:st=0:d=1.0,afade=t=out:st=19:d=1[out]"
        ),
        20,
    )


def _render(path: Path, filter_complex: str, duration: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and os.getenv("FORCE_REGENERATE_ASSETS") != "1":
        return
    run_ffmpeg(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-t",
            f"{duration:.3f}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
    )


if __name__ == "__main__":
    main()
