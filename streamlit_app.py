from __future__ import annotations

from pathlib import Path
import time
from typing import Any

import streamlit as st

from pipeline.fern import WORKERS, FernRunOptions, run_fern_pipeline
from pipeline.progress import ProgressEvent


STATUS_ICONS = {
    "waiting": "IDLE",
    "running": "RUN",
    "done": "OK",
    "failed": "!",
}


def main() -> None:
    st.set_page_config(page_title="Fern Documentary Control Room", page_icon=":movie_camera:", layout="wide")
    _inject_css()
    _init_state()

    st.markdown('<div class="topline">AI Documentary Production Control</div>', unsafe_allow_html=True)
    st.title("Fern / Blackfiles-Style Video Generator")

    with st.sidebar:
        st.header("Run Controls")
        topic = st.text_area(
            "Topic",
            value="The bizarre story of the 1904 Olympic marathon",
            height=90,
            help="The subject Claude plans, scripts, and visually directs.",
        )
        target_minutes = st.number_input(
            "Target video length (minutes)",
            min_value=0.5,
            max_value=60.0,
            value=3.0,
            step=0.5,
            help="The intended final runtime. This sizes the narration, beat timeline, and rough TTS cost.",
        )
        max_budget = st.number_input(
            "Max budget (USD)",
            min_value=0.0,
            max_value=500.0,
            value=5.0,
            step=0.5,
            help="The spending ceiling Claude should plan under. Dry-run never spends it.",
        )
        run_id = st.text_input(
            "run_id",
            value=_default_run_id(topic),
            help="The output folder name. Files are saved under output/<run_id>/ and reused when resume is on.",
        )
        style_preset = st.text_input(
            "Style preset",
            value="Fern-style AI documentary",
            help="The visual and editorial style lock passed into the planner and prompts.",
        )
        quality_mode = st.segmented_control(
            "Quality mode",
            options=["cheap", "balanced", "high"],
            default="balanced",
            help="Cheap plans fewer/lower-cost assets, balanced is the default, high allows a denser plan and higher assumed unit costs.",
        )
        max_video_clips = st.number_input(
            "Max generated video clips",
            min_value=0,
            max_value=50,
            value=3,
            step=1,
            help="Hard cap on expensive AI video moments. These are only for key dramatic beats, not every second.",
        )
        max_stills = st.number_input(
            "Max generated still/reference images",
            min_value=1,
            max_value=200,
            value=12,
            step=1,
            help="Hard cap for generated reference assets and still plates: characters, environments, documents, and beat images.",
        )
        call_replicate = st.checkbox(
            "Actually call Replicate / render video",
            value=False,
            help="Off means dry-run: write the plan and manifests only. On allows real image generation, TTS, and assembly.",
        )
        resume = st.checkbox(
            "Resume and reuse existing outputs",
            value=True,
            help="When on, existing plan/assets in output/<run_id>/ are reused instead of regenerated where possible.",
        )

        with st.expander("What these controls mean", expanded=False):
            st.markdown(
                """
                - **Quality mode** changes the planning profile and cost assumptions.
                - **Max generated video clips** limits the expensive Replicate video shots.
                - **Max generated still/reference images** limits reusable characters, environments, documents, and still plates.
                - **Actually call Replicate / render video** toggles spending/rendering. Leave it off for planning.
                - **Resume and reuse existing outputs** prevents redoing files already saved under the same `run_id`.
                """
            )

        rough_image_cost = max_stills * 0.039
        rough_video_cost = max_video_clips * 4 * 0.12
        st.metric(
            "Rough media estimate",
            f"${rough_image_cost + rough_video_cost:.2f}",
            help="Quick pre-planner estimate based on the caps above. The generated plan includes the real estimate.",
        )
        run_clicked = st.button("Start Production Run", type="primary", use_container_width=True)

    dashboard = st.container()
    result_box = st.container()
    _render_dashboard(dashboard)

    if run_clicked:
        st.session_state.events = []
        st.session_state.worker_started_at = {}
        st.session_state.worker_finished_at = {}
        st.session_state.result = None
        st.session_state.error = None
        options = FernRunOptions(
            topic=topic.strip(),
            target_minutes=float(target_minutes),
            max_budget_usd=float(max_budget),
            run_id=run_id.strip() or _default_run_id(topic),
            style_preset=style_preset.strip() or "Fern-style AI documentary",
            quality_mode=str(quality_mode or "balanced"),
            max_generated_video_clips=int(max_video_clips),
            max_generated_stills=int(max_stills),
            call_replicate=bool(call_replicate),
            resume=bool(resume),
        )

        def on_progress(event: ProgressEvent) -> None:
            _record_event(event)
            _render_dashboard(dashboard)

        try:
            with st.spinner("Production run in progress..."):
                st.session_state.result = run_fern_pipeline(options, progress_callback=on_progress)
        except Exception as exc:
            st.session_state.error = f"{type(exc).__name__}: {exc}"
            st.error(st.session_state.error)
        _render_dashboard(dashboard)

    _render_results(result_box)


def _init_state() -> None:
    st.session_state.setdefault("events", [])
    st.session_state.setdefault("worker_started_at", {})
    st.session_state.setdefault("worker_finished_at", {})
    st.session_state.setdefault("result", None)
    st.session_state.setdefault("error", None)


def _record_event(event: ProgressEvent) -> None:
    payload = event.to_dict()
    payload["timestamp"] = time.time()
    st.session_state.events.append(payload)
    if event.status == "running" and event.worker not in st.session_state.worker_started_at:
        st.session_state.worker_started_at[event.worker] = payload["timestamp"]
    if event.status in {"done", "failed"}:
        st.session_state.worker_finished_at[event.worker] = payload["timestamp"]


def _render_dashboard(container: Any) -> None:
    states = _worker_states()
    with container:
        st.subheader("Production Workers")
        rows = [WORKERS[:4], WORKERS[4:]]
        for row in rows:
            cols = st.columns(4)
            for col, worker in zip(cols, row):
                with col:
                    _worker_card(worker, states[worker])

        recent_events = st.session_state.events[-10:]
        with st.expander("Pipeline events", expanded=False):
            if recent_events:
                for event in reversed(recent_events):
                    artifact = f" - `{event['artifact_path']}`" if event.get("artifact_path") else ""
                    timestamp = time.strftime("%H:%M:%S", time.localtime(event["timestamp"]))
                    st.caption(f"{timestamp} - {event['worker']} - {event['status']} - {event['message']}{artifact}")
            else:
                st.caption("No events yet.")


def _worker_states() -> dict[str, dict[str, Any]]:
    states = {
        worker: {
            "status": "waiting",
            "message": "Waiting for the run to start",
            "progress": None,
            "artifact_path": None,
            "preview_path": None,
            "error": None,
            "elapsed": 0.0,
        }
        for worker in WORKERS
    }
    for event in st.session_state.events:
        worker = event["worker"]
        if worker not in states:
            continue
        states[worker].update(
            {
                "status": event["status"],
                "message": event["message"],
                "progress": event.get("progress"),
                "artifact_path": event.get("artifact_path"),
                "preview_path": event.get("preview_path"),
                "error": event.get("error"),
            }
        )
    now = time.time()
    for worker, state in states.items():
        started = st.session_state.worker_started_at.get(worker)
        finished = st.session_state.worker_finished_at.get(worker)
        if started:
            state["elapsed"] = (finished or now) - started
    return states


def _worker_card(worker: str, state: dict[str, Any]) -> None:
    status = state["status"]
    icon = STATUS_ICONS.get(status, "IDLE")
    elapsed = _format_elapsed(float(state.get("elapsed") or 0))
    artifact = state.get("artifact_path")
    preview = state.get("preview_path")
    message = state.get("message") or ""
    status_class = f"status-{status}"

    st.markdown(
        f"""
        <div class="worker-card {status_class}">
          <div class="worker-head">
            <span class="pulse">{icon}</span>
            <span class="worker-title">{worker}</span>
          </div>
          <div class="worker-status">{status.upper()} - {elapsed}</div>
          <div class="worker-message">{_escape_html(message)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    progress = state.get("progress")
    if progress is not None:
        st.progress(float(progress))
    if preview and _is_image_path(preview) and Path(preview).exists():
        st.image(preview, use_container_width=True)
    elif preview and _is_video_path(preview) and Path(preview).exists():
        st.video(preview)
    if artifact:
        st.caption(f"Artifact: `{artifact}`")
    if state.get("error"):
        st.error(state["error"])


def _render_results(container: Any) -> None:
    result = st.session_state.result
    if not result:
        return

    plan = result.get("plan", {})
    budget = plan.get("budget_plan", {})
    with container:
        st.subheader("Run Output")
        metric_cols = st.columns(5)
        metric_cols[0].metric("Budget cap", f"${budget.get('max_budget_usd', 0):.2f}")
        metric_cols[1].metric("Estimated total", f"${budget.get('estimated_total_usd', 0):.2f}")
        metric_cols[2].metric("Images", f"${budget.get('image_usd', 0):.2f}")
        metric_cols[3].metric("Video clips", f"${budget.get('video_usd', 0):.2f}")
        metric_cols[4].metric("TTS", f"${budget.get('tts_usd', 0):.2f}")

        final_path = result.get("final_path")
        if final_path:
            st.success(f"Final video: `{final_path}`")
            if Path(final_path).exists():
                st.video(final_path)
        else:
            st.info("Dry-run complete. No Replicate calls or final MP4 render were performed.")

        tab_plan, tab_assets, tab_beats = st.tabs(["Plan JSON", "Assets", "Beat Timeline"])
        with tab_plan:
            st.caption(f"Saved to `{result.get('plan_path')}`")
            st.json(plan)
        with tab_assets:
            _render_assets(plan.get("visual_assets", {}))
        with tab_beats:
            _render_beats(plan.get("beats", []))


def _render_assets(visual_assets: dict[str, Any]) -> None:
    for group in ("backgrounds", "characters", "props"):
        st.markdown(f"**{group.title()}**")
        assets = visual_assets.get(group, [])
        if not assets:
            st.caption("None planned.")
            continue
        for asset in assets:
            st.write(f"`{asset.get('id')}` - {asset.get('description')}")
            with st.expander("Prompt", expanded=False):
                st.write(asset.get("image_prompt", ""))


def _render_beats(beats: list[dict[str, Any]]) -> None:
    for beat in beats:
        start = float(beat.get("start_seconds", 0))
        end = float(beat.get("end_seconds", 0))
        st.markdown(f"**{beat.get('id')} - {beat.get('title')} - {start:.1f}s-{end:.1f}s - `{beat.get('visual_type')}`**")
        st.write(beat.get("narration", ""))
        overlays = [*beat.get("overlay_text", []), *beat.get("callouts", [])]
        if overlays:
            st.caption(f"Overlays/callouts: {', '.join(overlays)}")


def _default_run_id(topic: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "_" for char in topic.strip())[:36]
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned or "fern_doc_run"


def _is_image_path(path: str) -> bool:
    return Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}


def _is_video_path(path: str) -> bool:
    return Path(path).suffix.lower() in {".mp4", ".mov", ".webm"}


def _format_elapsed(seconds: float) -> str:
    if seconds <= 0:
        return "0s"
    minutes, sec = divmod(int(seconds), 60)
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background:
              linear-gradient(135deg, #151515 0%, #202020 45%, #171b19 100%);
            color: #f5f1e8;
        }
        .topline {
            color: #e2b84c;
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0;
            text-transform: uppercase;
            margin-bottom: 0.35rem;
        }
        h1, h2, h3 {
            letter-spacing: 0;
        }
        section[data-testid="stSidebar"] {
            background: #101010;
            border-right: 1px solid #343434;
        }
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
        section[data-testid="stSidebar"] p,
        section[data-testid="stSidebar"] span {
            color: #e9e0c8;
        }
        section[data-testid="stSidebar"] small,
        section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
            color: #b8ad93;
        }
        section[data-testid="stSidebar"] input,
        section[data-testid="stSidebar"] textarea {
            background: #181818 !important;
            color: #fff7e6 !important;
            border: 1px solid #4c4637 !important;
            border-radius: 7px !important;
            caret-color: #e2b84c !important;
        }
        section[data-testid="stSidebar"] input:focus,
        section[data-testid="stSidebar"] textarea:focus {
            border-color: #e2b84c !important;
            box-shadow: 0 0 0 1px rgba(226,184,76,0.35) !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stNumberInput"] button {
            background: #242424 !important;
            color: #f5f1e8 !important;
            border: 1px solid #4c4637 !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stNumberInput"] button:hover {
            background: #343126 !important;
            border-color: #e2b84c !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stSegmentedControl"] button {
            background: #1a1a1a !important;
            color: #f5f1e8 !important;
            border: 1px solid #4c4637 !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stSegmentedControl"] button:hover {
            background: #2b281f !important;
            color: #fff7e6 !important;
            border-color: #e2b84c !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stSegmentedControl"] button[aria-pressed="true"],
        section[data-testid="stSidebar"] div[data-testid="stSegmentedControl"] button[aria-checked="true"] {
            background: #e2b84c !important;
            color: #101010 !important;
            border-color: #e2b84c !important;
            font-weight: 800 !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stSegmentedControl"] button[aria-pressed="true"] *,
        section[data-testid="stSidebar"] div[data-testid="stSegmentedControl"] button[aria-checked="true"] * {
            color: #101010 !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stCheckbox"] label {
            background: transparent !important;
            color: #e9e0c8 !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stCheckbox"] svg {
            color: #101010 !important;
        }
        section[data-testid="stSidebar"] div[data-testid="stMetric"] {
            background: #191919;
            border: 1px solid #3a3528;
            border-radius: 8px;
            padding: 10px;
        }
        section[data-testid="stSidebar"] div[data-testid="stMetric"] [data-testid="stMetricValue"] {
            color: #f4d06f;
        }
        section[data-testid="stSidebar"] div[data-testid="stExpander"] {
            background: #171717;
            border: 1px solid #3a3528;
            border-radius: 8px;
        }
        section[data-testid="stSidebar"] .stButton button {
            background: #e2b84c !important;
            color: #101010 !important;
            border: 1px solid #e2b84c !important;
            border-radius: 7px !important;
            font-weight: 800 !important;
        }
        section[data-testid="stSidebar"] .stButton button:hover {
            background: #f0cc69 !important;
            color: #101010 !important;
            border-color: #f0cc69 !important;
        }
        .worker-card {
            min-height: 154px;
            padding: 14px 14px 12px;
            border: 1px solid #3a3a3a;
            border-radius: 8px;
            background: #1d1d1d;
            box-shadow: 0 10px 24px rgba(0,0,0,0.24);
        }
        .worker-head {
            display: flex;
            gap: 9px;
            align-items: center;
            margin-bottom: 8px;
        }
        .worker-title {
            color: #fbf4e2;
            font-size: 0.95rem;
            font-weight: 800;
            line-height: 1.18;
        }
        .worker-status {
            color: #d7c9a0;
            font-size: 0.76rem;
            font-weight: 800;
            margin-bottom: 10px;
        }
        .worker-message {
            color: #d8d3c7;
            font-size: 0.84rem;
            line-height: 1.35;
        }
        .status-running {
            border-color: #e2b84c;
            box-shadow: 0 0 0 1px rgba(226,184,76,0.18), 0 10px 24px rgba(0,0,0,0.24);
        }
        .status-done {
            border-color: #4fb39a;
        }
        .status-failed {
            border-color: #d75f5f;
        }
        .pulse {
            color: #e2b84c;
            font-weight: 900;
            display: inline-block;
            min-width: 34px;
        }
        .status-running .pulse {
            animation: blink 1.1s infinite ease-in-out;
        }
        @keyframes blink {
            0%, 100% { opacity: 0.35; }
            50% { opacity: 1; }
        }
        div[data-testid="stMetric"] {
            background: #191919;
            border: 1px solid #333333;
            border-radius: 8px;
            padding: 10px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
