from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

import streamlit as st

from pipeline.config import load_environment
from pipeline.progress import ProgressEvent
from pipeline.seedance_native import (
    DEFAULT_CLIP_1_VISUAL,
    DEFAULT_CLIP_2_VISUAL,
    DEFAULT_SCRIPT_PART_1,
    DEFAULT_SCRIPT_PART_2,
    WORKERS,
    SeedanceOptions,
    estimate_seedance_cost,
    run_seedance_native_pipeline,
)


STATUS_LABELS = {
    "waiting": "Waiting",
    "running": "Running",
    "done": "Done",
    "failed": "Failed",
}


def main() -> None:
    load_environment()
    st.set_page_config(page_title="Exit Scenario Studio", page_icon="!", layout="wide")
    _inject_css()
    _init_state()

    st.markdown('<div class="eyebrow">Exit Scenario</div>', unsafe_allow_html=True)
    st.title("Seedance Shorts Studio")
    st.caption("Two 15-second Seedance clips, last-frame continuation, native voice, synced captions.")
    _render_key_status()

    left, right = st.columns([0.42, 0.58], gap="large")
    with left:
        options = _render_controls()
    with right:
        _render_preview_panel(options)

    _render_progress()

    if st.session_state.get("run_clicked"):
        st.session_state.run_clicked = False
        _run(options)

    _render_result()


def _render_key_status() -> None:
    replicate_ready = bool(os.getenv("REPLICATE_API_TOKEN"))
    openai_ready = bool(os.getenv("OPENAI_API_KEY"))
    if replicate_ready and openai_ready:
        st.success("Ready: Seedance generation and caption transcription keys are loaded.")
        return
    if not replicate_ready:
        st.error("Missing REPLICATE_API_TOKEN. Seedance video generation cannot run yet.")
    if not openai_ready:
        st.warning("Missing OPENAI_API_KEY. Video generation can run, but synced captions need this key.")


def _render_controls() -> SeedanceOptions:
    st.subheader("1. Short Setup")
    preset = st.selectbox(
        "Scenario",
        [
            "Sinking car water tank",
            "Custom",
        ],
        index=0,
    )

    topic = st.text_input(
        "Folder name / run id",
        value=st.session_state.get("run_id") or "seedance_sinking_car_v1",
        help="One output folder is created under output/<run_id>.",
    )

    resolution = st.radio(
        "Quality",
        options=["480p", "720p", "1080p"],
        index=1,
        horizontal=True,
        help="720p is the current price-quality target. 1080p is much more expensive.",
    )
    seed = st.number_input("Seed", min_value=1, max_value=999999, value=int(st.session_state.get("seed", 42420)), step=1)

    st.subheader("2. Output")
    add_captions = st.toggle("Add synced captions", value=True, help="Transcribe Seedance audio, clean obvious errors, and burn subtitles with FFmpeg.")
    use_voice_reference = st.toggle(
        "Keep voice consistent between clips",
        value=True,
        help="Pass clip 1 audio into clip 2 as a Seedance voice reference.",
    )
    resume = st.toggle("Reuse existing clips if present", value=True, help="Useful for caption-only rebuilds without paying for video again.")

    estimate = estimate_seedance_cost(str(resolution))
    m1, m2, m3 = st.columns(3)
    m1.metric("Clip 1", f"${estimate['clip_1_usd']:.2f}")
    m2.metric("Clip 2", f"${estimate['clip_2_usd']:.2f}")
    m3.metric("Total", f"${estimate['total_usd']:.2f}")

    if preset == "Sinking car water tank":
        script_part_1 = DEFAULT_SCRIPT_PART_1
        script_part_2 = DEFAULT_SCRIPT_PART_2
        clip_1_visual = DEFAULT_CLIP_1_VISUAL
        clip_2_visual = DEFAULT_CLIP_2_VISUAL
    else:
        script_part_1 = st.session_state.get("script_part_1", DEFAULT_SCRIPT_PART_1)
        script_part_2 = st.session_state.get("script_part_2", DEFAULT_SCRIPT_PART_2)
        clip_1_visual = st.session_state.get("clip_1_visual", DEFAULT_CLIP_1_VISUAL)
        clip_2_visual = st.session_state.get("clip_2_visual", DEFAULT_CLIP_2_VISUAL)

    with st.expander("Advanced script and visual prompts", expanded=(preset == "Custom")):
        st.caption("Keep each part around 15 seconds. Seedance speaks each part separately.")
        script_part_1 = st.text_area("Narration clip 1", value=script_part_1, height=110)
        script_part_2 = st.text_area("Narration clip 2", value=script_part_2, height=110)
        clip_1_visual = st.text_area("Visual prompt clip 1", value=clip_1_visual, height=240)
        clip_2_visual = st.text_area("Visual prompt clip 2", value=clip_2_visual, height=240)

    st.session_state.run_id = topic
    st.session_state.seed = int(seed)
    st.session_state.script_part_1 = script_part_1
    st.session_state.script_part_2 = script_part_2
    st.session_state.clip_1_visual = clip_1_visual
    st.session_state.clip_2_visual = clip_2_visual

    if st.button("Generate Seedance Short", type="primary", use_container_width=True):
        st.session_state.run_clicked = True
        st.rerun()

    return SeedanceOptions(
        run_id=_safe_run_id(topic),
        script_part_1=script_part_1.strip(),
        script_part_2=script_part_2.strip(),
        clip_1_visual=clip_1_visual.strip(),
        clip_2_visual=clip_2_visual.strip(),
        resolution=str(resolution),
        seed=int(seed),
        add_captions=bool(add_captions),
        use_voice_reference=bool(use_voice_reference),
        resume=bool(resume),
    )


def _render_preview_panel(options: SeedanceOptions) -> None:
    st.subheader("What Will Happen")
    st.markdown(
        """
        <div class="step">1. Generate clip 1 with Seedance native voice and sound.</div>
        <div class="step">2. Extract the final frame and clip 1 voice reference.</div>
        <div class="step">3. Generate clip 2 from that frame, matching the same voice.</div>
        <div class="step">4. Stitch both clips, transcribe the native audio, and add clean captions.</div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.markdown("**Narration**")
        st.write(f"{options.script_part_1} {options.script_part_2}")

    with st.container(border=True):
        st.markdown("**Output folder**")
        st.code(str(Path("output") / options.run_id), language="text")


def _run(options: SeedanceOptions) -> None:
    st.session_state.events = []
    st.session_state.error = None
    st.session_state.result = None
    try:
        with st.spinner("Generating Seedance short..."):
            st.session_state.result = run_seedance_native_pipeline(options, progress_callback=_record_event)
    except Exception as exc:
        st.session_state.error = f"{type(exc).__name__}: {exc}"


def _render_progress() -> None:
    st.subheader("Progress")
    latest = {worker: {"worker": worker, "status": "waiting", "message": "Waiting"} for worker in WORKERS}
    for event in st.session_state.get("events", []):
        latest[event["worker"]] = event

    cols = st.columns(len(WORKERS))
    for col, worker in zip(cols, WORKERS):
        event = latest[worker]
        status = event.get("status", "waiting")
        col.markdown(f'<div class="status status-{status}">{STATUS_LABELS.get(status, status)}</div>', unsafe_allow_html=True)
        col.caption(worker)
        col.caption(event.get("message", ""))


def _render_result() -> None:
    if st.session_state.get("error"):
        st.error(st.session_state.error)
        return

    result = st.session_state.get("result")
    if not result:
        st.info("Ready. Start with the preset, 720p, captions on, voice reference on.")
        return

    st.subheader("Result")
    cols = st.columns(4)
    cols[0].metric("Duration", f"{result['duration_seconds']}s")
    cols[1].metric("Estimate", f"${result['estimate']['total_usd']:.2f}")
    cols[2].metric("Resolution", result["resolution"])
    cols[3].metric("Voice ref", "On" if result["voice_reference_used_for_clip2"] else "Off")

    final_path = Path(result["final_path"])
    if final_path.exists():
        st.video(str(final_path))
        st.success(f"Final: `{final_path}`")

    tab_final, tab_review, tab_files = st.tabs(["Transcript", "Review Frames", "Files"])
    with tab_final:
        transcript = result.get("transcript_path")
        if transcript and Path(transcript).exists():
            st.write(Path(transcript).read_text(encoding="utf-8"))
        else:
            st.caption("No transcript file.")
    with tab_review:
        contact = result.get("contact_sheet")
        if contact and Path(contact).exists():
            st.image(str(contact), use_column_width=True)
        for frame in result.get("review_frames", []):
            if Path(frame).exists():
                st.image(str(frame), use_column_width=True)
    with tab_files:
        st.json(result)


def _record_event(event: ProgressEvent) -> None:
    st.session_state.setdefault("events", []).append(event.to_dict())


def _init_state() -> None:
    st.session_state.setdefault("events", [])
    st.session_state.setdefault("result", None)
    st.session_state.setdefault("error", None)
    st.session_state.setdefault("run_clicked", False)
    st.session_state.setdefault("run_id", "seedance_sinking_car_v1")
    st.session_state.setdefault("seed", 42420)


def _safe_run_id(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value.lower())
    return ("_".join(words) or "seedance_short")[:80]


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        .eyebrow {
            color: #d61f35;
            font-weight: 800;
            letter-spacing: 0;
            text-transform: uppercase;
            font-size: 0.82rem;
            margin-bottom: 0.2rem;
        }
        .step {
            border-left: 4px solid #d61f35;
            padding: 0.55rem 0.8rem;
            margin-bottom: 0.5rem;
            background: rgba(127,127,127,0.08);
            border-radius: 6px;
        }
        .status {
            border-radius: 8px;
            padding: 0.55rem 0.65rem;
            text-align: center;
            font-weight: 700;
            border: 1px solid rgba(127,127,127,0.25);
        }
        .status-waiting { background: rgba(127,127,127,0.08); }
        .status-running { background: rgba(214,31,53,0.16); }
        .status-done { background: rgba(34,139,84,0.18); }
        .status-failed { background: rgba(214,31,53,0.28); }
        div[data-testid="stMetric"] {
            border: 1px solid rgba(127,127,127,0.25);
            border-radius: 8px;
            padding: 0.75rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
