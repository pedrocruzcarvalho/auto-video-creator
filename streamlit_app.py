from __future__ import annotations

import json
import os
import random
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

PRESETS = {
    "Sinking car water tank": {
        "run_id": "seedance_sinking_car_v1",
        "script_part_1": DEFAULT_SCRIPT_PART_1,
        "script_part_2": DEFAULT_SCRIPT_PART_2,
        "clip_1_visual": DEFAULT_CLIP_1_VISUAL,
        "clip_2_visual": DEFAULT_CLIP_2_VISUAL,
    },
    "Conveyor belt pulls you toward rollers": {
        "run_id": "conveyor_rollers_v1",
        "script_part_1": (
            "You fall onto a moving conveyor belt, and it is dragging you toward crushing rollers. "
            "Do not grab the roller. Look beside you. A loose metal tray is the fastest way out. "
            "Turn your body sideways and reach for it."
        ),
        "script_part_2": (
            "Slide the tray across the belt, flat against the moving rubber. "
            "When it wedges under the roller guard, the belt jolts for one second. "
            "Use that pause to roll sideways off the belt and hit the red stop paddle."
        ),
        "clip_1_visual": (
            "Vertical 9:16 glossy viral 3D survival simulation, clearly fictional CGI. "
            "Same athletic adult male training avatar, short brown hair, gray work shirt, realistic hands, bright clean factory training room, "
            "wide black conveyor belt, silver crushing rollers, loose metal tray nearby, red stop paddle on side rail. "
            "Dynamic camera, fast push-ins, snap zooms, macro object close-ups. Camera always points to the exact narrated object. "
            "Not live-action, not real accident footage, not children's cartoon, not Pixar. No on-screen text, no captions, no letters, no numbers, "
            "no logos, no signs, no UI, no watermark.\n\n"
            "Create part 1 of a continuous fictional conveyor belt survival simulation. Native serious male narrator plus conveyor motor rumble, roller hum, "
            "metal clanks, breath hits, and bass impacts.\n\n"
            "0-2s: high angle shot as the avatar lands on a fast moving black conveyor belt, sliding feet-first toward two silver rollers.\n"
            "2-5s: snap zoom to the rollers spinning with a narrow gap, then whip pan back to his hands gripping the belt surface.\n"
            "5-8s: close-up of his hand almost reaching toward the roller, then pulling back before touching it.\n"
            "8-11s: camera push-in to a loose rectangular metal tray rattling beside the belt on the floor.\n"
            "11-15s: avatar twists sideways across the belt and stretches one arm toward the tray, end on his fingers grabbing the tray edge."
        ),
        "clip_2_visual": (
            "Vertical 9:16 glossy viral 3D survival simulation, clearly fictional CGI. Continue exactly from the input first frame. "
            "Same bright factory training room, same black conveyor belt, same silver rollers, same gray-shirt avatar, same metal tray in his hand. "
            "Dynamic camera, fast push-ins, snap zooms, macro object close-ups. Camera always points to the exact narrated object. "
            "No on-screen text, no captions, no letters, no numbers, no logos, no signs, no UI, no watermark.\n\n"
            "Continue the conveyor belt survival simulation with native serious male narrator plus belt rumble, tray scrape, roller jolt, alarm chirp, and stop thump.\n\n"
            "0-3s: begin on the same fingers gripping the tray; he pulls it onto the moving belt while sliding closer to the rollers.\n"
            "3-6s: macro shot of the tray laid flat across the moving rubber, vibrating as the belt drags it forward.\n"
            "6-10s: snap zoom as the tray wedges under the roller guard; the belt jolts and slows for one second.\n"
            "10-13s: avatar rolls sideways off the belt onto the safe floor, camera follows the fast roll.\n"
            "13-15s: close-up of his palm slapping the red stop paddle; rollers stop and the tray drops with a metal clank."
        ),
    },
    "Walk-in freezer locks behind you": {
        "run_id": "freezer_lockin_v1",
        "script_part_1": (
            "You step into a walk-in freezer and the door locks behind you. "
            "Do not waste your breath yelling. The warmest tool is already in the room. "
            "Grab a metal shelf and move toward the door gasket."
        ),
        "script_part_2": (
            "Slide the shelf edge between the rubber seal and the frame. "
            "Warm air starts leaking through the gap, and the frost cracks around the latch. "
            "Now lever the shelf sideways and shoulder the door open."
        ),
        "clip_1_visual": (
            "Vertical 9:16 glossy viral 3D survival simulation, clearly fictional CGI. Same adult male training avatar, brown hair, gray hoodie, "
            "bright walk-in freezer room, frosted metal shelves, white door gasket, icy latch, visible cold vapor, clean readable action. "
            "Dynamic camera, fast push-ins, snap zooms, macro object close-ups. Camera always points to the exact narrated object. "
            "No on-screen text, no captions, no letters, no numbers, no logos, no signs, no UI, no watermark.\n\n"
            "0-2s: door slams shut behind the avatar inside a bright freezer, cold vapor rushes around him.\n"
            "2-5s: snap zoom to his hand pulling the locked handle, frost shaking loose.\n"
            "5-8s: close-up of him breathing into cold air, then stopping and looking around.\n"
            "8-11s: camera push-in to a loose metal shelf panel on the rack.\n"
            "11-15s: he grabs the shelf with both hands and carries it toward the door gasket, end on shelf edge near the rubber seal."
        ),
        "clip_2_visual": (
            "Vertical 9:16 glossy viral 3D survival simulation, clearly fictional CGI. Continue exactly from the input first frame. "
            "Same freezer room, same gray-hoodie avatar, same loose metal shelf edge at the white rubber door gasket. "
            "Dynamic camera, fast push-ins, snap zooms, macro object close-ups. No on-screen text, no captions, no letters, no numbers, no logos, no signs, no UI.\n\n"
            "0-3s: shelf edge slides into the gap between rubber gasket and metal frame.\n"
            "3-6s: macro shot of frost cracking as a thin stream of warmer air leaks through.\n"
            "6-10s: he levers the shelf sideways, latch area flexes, ice flakes fall.\n"
            "10-13s: shoulder push on the door; it pops open with cold vapor blasting out.\n"
            "13-15s: he steps into bright hallway light, shelf still in hand, door swinging open behind him."
        ),
    },
    "Escalator step collapses under you": {
        "run_id": "escalator_step_collapse_v1",
        "script_part_1": (
            "You are riding an escalator when the step under your foot drops open. "
            "Do not step deeper into the gap. Throw your weight onto both handrails and lift your knees."
        ),
        "script_part_2": (
            "The moving stairs keep folding underneath you. Keep your shoes away from the teeth at the top. "
            "Swing one leg onto the side panel, then pull your body sideways onto the landing."
        ),
        "clip_1_visual": (
            "Vertical 9:16 glossy viral 3D survival simulation, clearly fictional CGI. Same adult male training avatar, brown hair, navy jacket, "
            "clean shopping mall escalator training scene, moving metal steps, black rubber handrails, bright readable lighting. "
            "Dynamic camera, fast push-ins, snap zooms, macro object close-ups. Camera always points to the exact narrated object. "
            "No on-screen text, no captions, no letters, no numbers, no logos, no signs, no UI, no watermark.\n\n"
            "0-2s: escalator moving upward, one metal step suddenly drops open beneath his shoe.\n"
            "2-5s: snap zoom to the dark gap and folding metal step edges moving below.\n"
            "5-8s: close-up of his shoe hovering above the gap, then pulling back before it drops deeper.\n"
            "8-11s: camera pushes to both hands grabbing the black rubber handrails hard.\n"
            "11-15s: he lifts both knees while suspended between the rails, end on feet clear above the broken step gap."
        ),
        "clip_2_visual": (
            "Vertical 9:16 glossy viral 3D survival simulation, clearly fictional CGI. Continue exactly from the input first frame. "
            "Same mall escalator, same navy-jacket avatar, same hands gripping both handrails, same broken moving step gap. "
            "Dynamic camera, fast push-ins, snap zooms, macro object close-ups. No on-screen text, no captions, no letters, no numbers, no logos, no signs, no UI.\n\n"
            "0-3s: broken steps fold under him while he keeps both knees high.\n"
            "3-6s: macro shot of shoe staying away from the metal teeth at the top landing.\n"
            "6-10s: he swings one leg sideways onto the smooth side panel, camera follows the leg.\n"
            "10-13s: he pulls his torso sideways off the moving steps onto the landing floor.\n"
            "13-15s: close-up of him safely on the landing as the broken step passes below."
        ),
    },
}

IDEAS = [
    {
        "title": "Vending machine tips over toward you",
        "run_id": "vending_machine_tip_v1",
        "place": "bright break-room training set",
        "character": "gray hoodie",
        "danger": "a heavy vending machine tipping forward",
        "wrong_move": "push against the front glass",
        "tool": "a rolling office chair",
        "move": "kick the chair under the falling edge and roll sideways away",
        "payoff": "the machine lands on the chair instead of the floor space where you were",
        "score": 23,
    },
    {
        "title": "Garage door closes while you crawl under it",
        "run_id": "garage_door_trap_v1",
        "place": "clean garage training set",
        "character": "navy sweatshirt",
        "danger": "a heavy garage door dropping fast",
        "wrong_move": "try to hold the door with your back",
        "tool": "a toolbox sitting beside the track",
        "move": "shove the toolbox into the side track and roll under the gap",
        "payoff": "the door jams for one second and you clear the threshold",
        "score": 22,
    },
    {
        "title": "Warehouse shelf starts collapsing beside you",
        "run_id": "warehouse_shelf_collapse_v1",
        "place": "bright warehouse training aisle",
        "character": "gray work shirt",
        "danger": "a tall metal shelf tilting over with boxes sliding down",
        "wrong_move": "run straight down the aisle",
        "tool": "a low pallet jack",
        "move": "drop behind the pallet jack and slide sideways under the lowest shelf gap",
        "payoff": "boxes crash over the pallet jack while you slide clear",
        "score": 24,
    },
    {
        "title": "Elevator doors open to an empty shaft",
        "run_id": "empty_elevator_shaft_v1",
        "place": "clean office elevator training set",
        "character": "dark blue shirt",
        "danger": "elevator doors opening to a dark empty shaft",
        "wrong_move": "step forward before looking down",
        "tool": "an umbrella in your hand",
        "move": "tap the floor space first, then wedge the umbrella handle into the door track",
        "payoff": "the doors stay open long enough for you to step back safely",
        "score": 23,
    },
    {
        "title": "Glass bridge cracks under your feet",
        "run_id": "glass_bridge_crack_v1",
        "place": "bright indoor glass bridge training set",
        "character": "navy jacket",
        "danger": "glass floor cracking under both shoes",
        "wrong_move": "jump on one foot",
        "tool": "a flat backpack",
        "move": "drop the backpack flat and crawl across it to spread your weight",
        "payoff": "the cracks spread slower while you reach the metal frame",
        "score": 24,
    },
    {
        "title": "Moving walkway pulls your shoelace under",
        "run_id": "moving_walkway_lace_v1",
        "place": "clean airport moving walkway training set",
        "character": "gray hoodie",
        "danger": "a shoelace being pulled into the moving walkway comb plate",
        "wrong_move": "pull your foot straight backward",
        "tool": "a hard suitcase handle",
        "move": "jam the suitcase handle across the comb plate and slip your foot out sideways",
        "payoff": "the lace snaps loose while your foot clears the teeth",
        "score": 22,
    },
    {
        "title": "Forklift load slides off above you",
        "run_id": "forklift_load_slide_v1",
        "place": "bright warehouse loading bay training set",
        "character": "gray work shirt",
        "danger": "stacked boxes sliding off a raised forklift",
        "wrong_move": "run under the falling load",
        "tool": "a long cardboard tube",
        "move": "push the tube against the stack to redirect the first box and step behind a pillar",
        "payoff": "the boxes spill to the side while the pillar shields you",
        "score": 21,
    },
    {
        "title": "Restaurant freezer shelf falls from above",
        "run_id": "freezer_shelf_fall_v1",
        "place": "bright restaurant freezer training set",
        "character": "dark blue shirt",
        "danger": "a loaded freezer shelf snapping loose overhead",
        "wrong_move": "reach up with bare hands",
        "tool": "a plastic food crate",
        "move": "raise the crate like a shield and step into the doorway corner",
        "payoff": "frozen boxes hit the crate while you slide out through the doorway",
        "score": 22,
    },
]


def main() -> None:
    load_environment()
    st.set_page_config(page_title="Extreme Survival Studio", page_icon="!", layout="wide")
    _inject_css()
    _init_state()

    st.markdown('<div class="eyebrow">Extreme Survival</div>', unsafe_allow_html=True)
    st.title("Seedance Survival Studio")
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
    else:
        if not replicate_ready:
            st.error("Missing REPLICATE_API_TOKEN. Seedance video generation cannot run yet.")
        if not openai_ready:
            st.warning("Missing OPENAI_API_KEY. Video generation can run, but synced captions need this key.")
    if os.getenv("ANTHROPIC_API_KEY"):
        st.info("Claude Idea Lab is available. It only runs when you click the Claude idea button.")


def _render_controls() -> SeedanceOptions:
    st.subheader("1. Short Setup")
    _render_idea_lab()
    available_presets = _available_presets()
    scenario_options = [*available_presets.keys(), "Custom"]
    selected_preset = st.session_state.get("selected_preset")
    scenario_index = scenario_options.index(selected_preset) if selected_preset in scenario_options else 0

    preset = st.selectbox(
        "Scenario",
        scenario_options,
        index=scenario_index,
    )

    if st.session_state.get("selected_preset") != preset:
        st.session_state.selected_preset = preset
        if preset in available_presets:
            st.session_state.run_id = available_presets[preset]["run_id"]

    st.session_state.setdefault("run_id", _preset_value(preset, "run_id", "seedance_short"))
    topic = st.text_input(
        "Folder name / run id",
        key="run_id",
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
    st.info("Clip 2 uses the final frame from clip 1 for visual continuity. Seedance does not allow voice/audio references together with first-frame continuation.")
    use_voice_reference = False
    resume = st.toggle("Reuse existing clips if present", value=True, help="Useful for caption-only rebuilds without paying for video again.")

    estimate = estimate_seedance_cost(str(resolution))
    m1, m2, m3 = st.columns(3)
    m1.metric("Clip 1", f"${estimate['clip_1_usd']:.2f}")
    m2.metric("Clip 2", f"${estimate['clip_2_usd']:.2f}")
    m3.metric("Total", f"${estimate['total_usd']:.2f}")

    if preset in available_presets:
        script_part_1 = available_presets[preset]["script_part_1"]
        script_part_2 = available_presets[preset]["script_part_2"]
        clip_1_visual = available_presets[preset]["clip_1_visual"]
        clip_2_visual = available_presets[preset]["clip_2_visual"]
    else:
        script_part_1 = st.session_state.get("script_part_1", DEFAULT_SCRIPT_PART_1)
        script_part_2 = st.session_state.get("script_part_2", DEFAULT_SCRIPT_PART_2)
        clip_1_visual = st.session_state.get("clip_1_visual", DEFAULT_CLIP_1_VISUAL)
        clip_2_visual = st.session_state.get("clip_2_visual", DEFAULT_CLIP_2_VISUAL)

    with st.expander("Advanced script and visual prompts", expanded=(preset == "Custom")):
        st.caption("Keep each part around 15 seconds. Seedance speaks each part separately.")
        disabled = preset in available_presets
        script_part_1 = st.text_area("Narration clip 1", value=script_part_1, height=110, disabled=disabled)
        script_part_2 = st.text_area("Narration clip 2", value=script_part_2, height=110, disabled=disabled)
        clip_1_visual = st.text_area("Visual prompt clip 1", value=clip_1_visual, height=240, disabled=disabled)
        clip_2_visual = st.text_area("Visual prompt clip 2", value=clip_2_visual, height=240, disabled=disabled)

    st.session_state.seed = int(seed)
    st.session_state.script_part_1 = script_part_1
    st.session_state.script_part_2 = script_part_2
    st.session_state.clip_1_visual = clip_1_visual
    st.session_state.clip_2_visual = clip_2_visual

    auto_generate = bool(st.session_state.get("auto_generate_selected_idea", False))
    if auto_generate:
        st.session_state.auto_generate_selected_idea = False

    if st.button("Generate Seedance Short", type="primary", use_container_width=True) or auto_generate:
        st.session_state.run_clicked = True

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
        <div class="step">2. Extract the final frame from clip 1.</div>
        <div class="step">3. Generate clip 2 from that frame for visual continuity.</div>
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
        st.info("Ready. Start with a preset, 720p, captions on, and one fresh run id.")
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
        if result.get("reused_existing_final"):
            st.info("This reused an existing final video, so no new Seedance generation was run.")

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
    st.session_state.setdefault("idea_seed", 101)
    st.session_state.setdefault("idea_source", "local")
    st.session_state.setdefault("claude_ideas", None)
    st.session_state.setdefault("auto_generate_selected_idea", False)


def _safe_run_id(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value.lower())
    return ("_".join(words) or "seedance_short")[:80]


def _preset_value(preset: str, key: str, fallback: str) -> str:
    presets = _available_presets()
    if preset in presets:
        return str(presets[preset][key])
    return fallback


def _available_presets() -> dict[str, dict[str, str]]:
    presets = dict(PRESETS)
    idea_preset = st.session_state.get("idea_preset")
    if isinstance(idea_preset, dict):
        presets[str(idea_preset["title"])] = {
            "run_id": str(idea_preset["run_id"]),
            "script_part_1": str(idea_preset["script_part_1"]),
            "script_part_2": str(idea_preset["script_part_2"]),
            "clip_1_visual": str(idea_preset["clip_1_visual"]),
            "clip_2_visual": str(idea_preset["clip_2_visual"]),
        }
    return presets


def _render_idea_lab() -> None:
    with st.expander("Idea Lab - no video generation", expanded=False):
        st.caption("Generate smart-survival ideas. Local is free. Claude is optional and uses a small text API call.")
        c1, c2 = st.columns([0.5, 0.5])
        if c1.button("Generate local ideas", use_container_width=True):
            st.session_state.idea_seed = int(st.session_state.get("idea_seed", 101)) + 1
            st.session_state.idea_source = "local"
            st.session_state.claude_ideas = None
        if c2.button("Generate Claude ideas", use_container_width=True):
            try:
                st.session_state.claude_ideas = _claude_idea_batch(count=6)
                st.session_state.idea_source = "claude"
            except Exception as exc:
                st.session_state.claude_ideas = None
                st.session_state.idea_source = "local"
                st.error(f"Claude idea generation failed: {type(exc).__name__}: {exc}")

        if st.session_state.get("idea_source") == "claude" and st.session_state.get("claude_ideas"):
            ideas = st.session_state.claude_ideas
            st.caption("Showing Claude ideas. No video was generated.")
        else:
            ideas = _idea_batch(int(st.session_state.get("idea_seed", 101)), count=4)
            st.caption("Showing local ideas. No API calls were made.")

        for idea in ideas:
            with st.container(border=True):
                st.markdown(f"**{idea['title']}**")
                st.caption(f"Score {idea['score']}/25 | Smart move: {idea['move']}")
                st.write(f"Wrong instinct: {idea['wrong_move']}. Useful object: {idea['tool']}.")
                if st.button(f"Use idea: {idea['title']}", key=f"use_{idea['run_id']}", use_container_width=True):
                    preset = _preset_from_idea(idea)
                    st.session_state.idea_preset = preset
                    st.session_state.selected_preset = preset["title"]
                    st.session_state.run_id = preset["run_id"]
                    st.success(f"Loaded: {idea['title']}. Close Idea Lab and select it in Scenario.")
                if st.button(f"Generate video: {idea['title']}", key=f"generate_{idea['run_id']}", use_container_width=True):
                    preset = _preset_from_idea(idea)
                    st.session_state.idea_preset = preset
                    st.session_state.selected_preset = preset["title"]
                    st.session_state.run_id = preset["run_id"]
                    st.session_state.auto_generate_selected_idea = True
                    st.success(f"Loaded and queued: {idea['title']}. Generation will start with the settings below.")


def _idea_batch(seed: int, *, count: int) -> list[dict[str, str | int]]:
    rng = random.Random(seed)
    ideas = list(IDEAS)
    rng.shuffle(ideas)
    return ideas[:count]


def _claude_idea_batch(*, count: int) -> list[dict[str, str | int]]:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is missing.")
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError("Install dependencies with: python -m pip install -r requirements.txt") from exc

    client = Anthropic()
    response = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        max_tokens=2600,
        temperature=0.9,
        system=(
            "You are the idea strategist for Extreme Survival, a YouTube Shorts channel. "
            "Create viral fictional smart-survival scenarios for 30-second 3D simulation Shorts. "
            "Avoid gore, real tragedies, illegal advice, and repetitive water ideas. "
            "Prefer everyday places, immediate danger, one wrong instinct, one nearby object, and one clever physical move."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Generate {count} high-retention Extreme Survival video ideas as strict JSON only.\n"
                    "Return a JSON array. Each object must have exactly these keys:\n"
                    "title, run_id, place, character, danger, wrong_move, tool, move, payoff, score.\n\n"
                    "Rules:\n"
                    "- title: short YouTube idea, no numbering.\n"
                    "- run_id: lowercase snake_case ending in _v1.\n"
                    "- place: bright, clean fictional training set.\n"
                    "- character: simple clothing phrase like gray hoodie or navy jacket.\n"
                    "- danger: one visible moving danger.\n"
                    "- wrong_move: one instinct viewers might do incorrectly.\n"
                    "- tool: one nearby ordinary object to use.\n"
                    "- move: one clever physical action using the tool.\n"
                    "- payoff: one visually clear escape/payoff.\n"
                    "- score: integer from 18 to 25.\n\n"
                    "Make the ideas non-water unless one is exceptionally strong. No markdown."
                ),
            }
        ],
    )
    text = "\n".join(getattr(block, "text", "") for block in response.content).strip()
    raw_ideas = _loads_json_array(text)
    ideas = [_normalize_claude_idea(item) for item in raw_ideas]
    return [idea for idea in ideas if idea][:count]


def _loads_json_array(text: str) -> list[dict[str, object]]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Claude did not return a JSON array.")
    payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, list):
        raise ValueError("Claude JSON was not a list.")
    return [item for item in payload if isinstance(item, dict)]


def _normalize_claude_idea(item: dict[str, object]) -> dict[str, str | int]:
    title = _clean_idea_field(item.get("title"), "Untitled survival idea")
    run_id = _safe_run_id(_clean_idea_field(item.get("run_id"), title))
    if not run_id.endswith("_v1"):
        run_id = f"{run_id}_v1"
    score_raw = item.get("score", 20)
    try:
        score = max(18, min(25, int(score_raw)))
    except (TypeError, ValueError):
        score = 20
    return {
        "title": title,
        "run_id": run_id,
        "place": _clean_idea_field(item.get("place"), "bright training set"),
        "character": _clean_idea_field(item.get("character"), "gray hoodie"),
        "danger": _clean_idea_field(item.get("danger"), "a visible moving danger"),
        "wrong_move": _clean_idea_field(item.get("wrong_move"), "panic and move straight toward the danger"),
        "tool": _clean_idea_field(item.get("tool"), "a nearby ordinary object"),
        "move": _clean_idea_field(item.get("move"), "use the object to create one second of space"),
        "payoff": _clean_idea_field(item.get("payoff"), "you escape into a clear safe zone"),
        "score": score,
    }


def _clean_idea_field(value: object, fallback: str) -> str:
    text = str(value or fallback)
    return " ".join(text.replace('"', "").replace("'", "").split())[:180]


def _preset_from_idea(idea: dict[str, str | int]) -> dict[str, str]:
    title = str(idea["title"])
    place = str(idea["place"])
    character = str(idea["character"])
    danger = str(idea["danger"])
    wrong_move = str(idea["wrong_move"])
    tool = str(idea["tool"])
    move = str(idea["move"])
    payoff = str(idea["payoff"])
    run_id = str(idea["run_id"])
    script_part_1 = (
        f"You are in a {place} when {danger} happens right in front of you. "
        f"Do not {wrong_move}. Look around fast. The useful object is {tool}. "
        "Move before the danger reaches you."
    )
    script_part_2 = (
        f"Use {tool} to create one second of space. "
        f"The smart move is to {move}. "
        f"That is enough time: {payoff}."
    )
    style = (
        "Vertical 9:16 glossy viral 3D survival simulation, clearly fictional CGI. "
        f"Same adult male training avatar, brown hair, {character}, realistic hands, {place}, clean readable lighting. "
        f"Show {danger}, {tool}, and the surrounding objects clearly. Dynamic camera, fast push-ins, snap zooms, macro object close-ups. "
        "Camera always points to the exact narrated object. Not live-action, not real accident footage, not children's cartoon, not Pixar. "
        "No on-screen text, no captions, no letters, no numbers, no logos, no signs, no UI, no watermark."
    )
    clip_1_visual = (
        f"{style}\n\n"
        "Create part 1 of a continuous fictional smart survival simulation. Native serious male narrator plus object impacts, mechanical rumble, "
        "fast whooshes, close-up hits, and bass impacts.\n\n"
        f"0-2s: show the avatar in {place} as {danger} begins suddenly, camera snaps toward the danger.\n"
        f"2-5s: macro close-up of the worst point of danger moving closer.\n"
        f"5-8s: show the wrong instinct, {wrong_move}, beginning for a split second, then stopping.\n"
        f"8-11s: whip pan and push-in to {tool} nearby, clearly reachable.\n"
        f"11-15s: avatar moves sideways and grabs {tool}, end on his hands holding it while the danger closes in."
    )
    clip_2_visual = (
        f"{style} Continue exactly from the input first frame. Same avatar, same location, same danger, same {tool}. "
        "No on-screen text, no captions, no letters, no numbers, no logos, no signs, no UI, no watermark.\n\n"
        "Continue the smart survival simulation with native serious male narrator plus object scrape, impact hit, short alarm chirp, and final relief sound.\n\n"
        f"0-3s: begin on the same hands holding {tool} as {danger} moves closer.\n"
        f"3-6s: macro shot as he positions {tool} exactly where it can interrupt the danger.\n"
        f"6-10s: snap zoom on the survival move: {move}.\n"
        f"10-13s: camera follows the avatar escaping sideways through the opening created by the move.\n"
        f"13-15s: final clear payoff shot: {payoff}."
    )
    return {
        "title": f"Idea: {title}",
        "run_id": run_id,
        "script_part_1": script_part_1,
        "script_part_2": script_part_2,
        "clip_1_visual": clip_1_visual,
        "clip_2_visual": clip_2_visual,
    }


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

