# Local Media Assets

The pipeline treats files here as reusable production assets and will not overwrite them during normal video generation.

`python scripts/create_generic_assets.py` can create placeholder sounds, but hand-picked/downloaded sounds should replace those placeholders when quality matters.

## SFX

- `sfx/camera_click.wav`
- `sfx/typewriter_tick.wav`
- `sfx/glitch_burst.wav`
- `sfx/boom.wav`
- `sfx/whoosh.wav`

## Ambience

- `ambience/low_drone.wav`
- `ambience/room_tone.wav`
- `ambience/industrial_hum.wav`
- `ambience/surveillance_noise.wav`
- `ambience/distant_wind.wav`
- `ambience/courthouse_room.wav`

## Archival Images

Archival downloads go under `archival/<query-slug>/`.
Resolution/source/license manifests go under `archival/_manifests/`.

The current safe downloader is Wikimedia Commons only. User-provided local images can also be referenced by path in a plan.

## Downloaded Sounds

When `pvideo_story.py` runs with `--download-assets`, missing SFX/ambience requests can be downloaded from Openverse audio using CC0/public-domain filters.
Sound source/license manifests go under `sound_manifests/`.
