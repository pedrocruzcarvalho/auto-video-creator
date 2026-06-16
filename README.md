# AI YouTube Video Pipeline

Local-first LangGraph pipeline that turns a topic into a rendered MP4 under `output/<run_id>/final.mp4`.

## Setup

1. Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

2. Install `ffmpeg` and make sure both `ffmpeg` and `ffprobe` are on PATH.

3. Copy `.env.example` to `.env` and add the keys you want to use:

```powershell
Copy-Item .env.example .env
```

## Run

Fern-style Streamlit control room:

```powershell
streamlit run streamlit_app.py
```

Start with "Actually call Replicate / render video" unchecked to generate only
`output/<run_id>/fern_plan.json`, manifests, worker events, and budget estimates.
When the checkbox is enabled, the Fern runner reuses the existing Replicate image,
OpenAI TTS, motion-graphics, and final assembly pipeline. Replicate AI video clip
generation is planned in `video_clip_manifest.json` and currently falls back to
animated still plates during assembly.

Full AI pipeline:

```powershell
python main.py "The Kessler Syndrome"
```

Offline structure test with placeholder images, script, and silent audio:

```powershell
python main.py "The Kessler Syndrome" --mock
```

One-minute paid smoke test with one generated box/image:

```powershell
python main.py "Every Time We Went Close To World War 3: Kargil War" --box-mode --boxes 1 --seconds-per-box 120 --run-id ww3_kargil_test
```

Each run writes:

- `output/<run_id>/assets/`
- `output/<run_id>/script.json`
- `output/<run_id>/images/`
- `output/<run_id>/audio/`
- `output/<run_id>/clips/`
- `output/<run_id>/final.mp4`

## Notes

- YouTube upload is intentionally not included.
- LangGraph orchestrates the pipeline stages in `pipeline/graph.py`.
- Claude now emits a reusable `visual_assets` plan with backgrounds, characters, and props. These assets are generated first, stored under `output/<run_id>/assets/`, and passed as reference images to Replicate models that support image inputs.
- Generated images are cached by prompt hash under `.cache/images/`.
- The mock mode still needs `ffmpeg` because it renders real audio/video files.
