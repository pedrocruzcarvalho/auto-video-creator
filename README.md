# Extreme Survival Shorts Studio

Local production tool for **Extreme Survival**:

> One situation. One rule. One way out.

The current workflow is Seedance-first:

- generate two 15-second Seedance clips;
- extract the last frame from clip 1;
- start clip 2 from that exact frame;
- use clip 1's final frame as the first frame for clip 2;
- stitch the native-audio clips;
- transcribe the native audio;
- burn clean synced subtitles with FFmpeg.

## Setup

Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

Install `ffmpeg` and make sure `ffmpeg` and `ffprobe` are on PATH.

Create `.env`:

```powershell
Copy-Item .env.example .env
```

Required keys:

- `REPLICATE_API_TOKEN` for Seedance video generation
- `OPENAI_API_KEY` for transcription and captions
- `ANTHROPIC_API_KEY` for optional Claude-powered idea generation

Optional:

- `ANTHROPIC_MODEL`, defaults to `claude-sonnet-4-6`

## Run The App

```powershell
streamlit run streamlit_app.py
```

Recommended first run:

- Scenario: `Conveyor belt pulls you toward rollers` for a non-water smart-survival test, or `Sinking car water tank` for the known baseline
- Quality: `720p`
- Add synced captions: on
- Reuse existing clips: on

Seedance does not allow audio/video references together with first-frame continuation, so the current workflow prioritizes visual continuity.

The app includes built-in presets, so you do not need to type scripts or prompts for normal runs. Use `Custom` only when testing a new manually written idea.

To find a new concept without typing prompts:

1. Open `Idea Lab - no video generation`.
2. Click `Generate local ideas` for free ideas, or `Generate Claude ideas` for a better paid text-only batch.
3. Click `Generate video` on the best concept when ready to spend.

For a slower review flow, click `Use idea`, select the new `Idea:` scenario, review the narration, then click `Generate Seedance Short`.

At 720p the planning estimate is about **$6 per 30-second Short**:

- clip 1: about `$2.70`
- continuation clip 2: about `$3.30`

Caption rebuilds do not require regenerating Seedance clips when reuse is on. Rerunning a completed run id reuses the existing `final.mp4` and does not call Seedance again unless `--fresh` is used.

## Run CLI

```powershell
python main.py "Sinking car water tank" --run-id seedance_sinking_car_v1
```

Recommended terminal preset:

```powershell
python main.py --preset warehouse_shelf_collapse --run-id warehouse_shelf_collapse_v1 --resolution 720p
```

Useful options:

```powershell
python main.py "Sinking car water tank" --resolution 480p
python main.py "Sinking car water tank" --no-captions
python main.py "Sinking car water tank" --fresh
```

Outputs are written to:

```text
output/<run_id>/
```

Important files:

- `final.mp4` - captioned final Short
- `final_seedance_native.mp4` - original stitched native-audio video
- `native_transcript.txt` - cleaned transcript
- `contact_sheet.jpg` - quick visual review
- `run_report.md` / `run_report.json` - run details

## Content Guides

The creative direction lives in `docs/`:

- `docs/channel_strategy.md`
- `docs/shorts_format.md`
- `docs/visual_style.md`
- `docs/sound_design.md`
- `docs/prompt_templates.md`
- `docs/production_sop.md`
- `docs/analytics_log.md`

