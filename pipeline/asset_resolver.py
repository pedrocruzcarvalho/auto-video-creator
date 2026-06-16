from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ASSET_ROOT = Path("assets")
SFX_DIR = ASSET_ROOT / "sfx"
AMBIENCE_DIR = ASSET_ROOT / "ambience"
ARCHIVAL_DIR = ASSET_ROOT / "archival"
SOUND_MANIFEST_DIR = ASSET_ROOT / "sound_manifests"

SAFE_IMAGE_PROVIDERS = {"wikimedia_commons", "openverse"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}


def ensure_asset_dirs() -> None:
    for path in (ASSET_ROOT, SFX_DIR, AMBIENCE_DIR, ARCHIVAL_DIR, SOUND_MANIFEST_DIR):
        path.mkdir(parents=True, exist_ok=True)


def resolve_archival_insert(
    insert: dict[str, Any] | None,
    *,
    run_id: str,
    beat_index: int,
    download: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(insert, dict):
        return None

    ensure_asset_dirs()
    resolved = dict(insert)
    resolved.setdefault("provider", "wikimedia_commons")
    resolved.setdefault("available", False)
    resolved.setdefault("asset_path", None)
    resolved.setdefault("source_url", None)
    resolved.setdefault("license", None)
    resolved.setdefault("reason_unavailable", None)

    local_path = resolved.get("local_path")
    if local_path:
        candidate = Path(str(local_path))
        if candidate.exists() and candidate.suffix.lower() in IMAGE_SUFFIXES:
            resolved.update(
                {
                    "available": True,
                    "asset_path": str(candidate),
                    "provider": "local",
                    "reason_unavailable": None,
                }
            )
            _write_manifest(run_id, beat_index, resolved)
            return resolved

    query = str(resolved.get("query") or "").strip()
    if not query:
        resolved["reason_unavailable"] = "missing_query"
        _write_manifest(run_id, beat_index, resolved)
        return resolved

    if not download:
        existing = _find_cached_archival_image(query)
        if existing:
            resolved.update({"available": True, "asset_path": str(existing), "reason_unavailable": None})
        else:
            resolved["reason_unavailable"] = "not_cached_download_assets_disabled"
        _write_manifest(run_id, beat_index, resolved)
        return resolved

    provider = str(resolved.get("provider") or "wikimedia_commons").strip().lower()
    if provider not in SAFE_IMAGE_PROVIDERS:
        resolved["reason_unavailable"] = f"provider_not_allowed:{provider}"
        _write_manifest(run_id, beat_index, resolved)
        return resolved

    try:
        downloaded = _download_safe_archival_image(query)
    except Exception as exc:
        resolved["reason_unavailable"] = f"download_failed:{type(exc).__name__}:{exc}"
        _write_manifest(run_id, beat_index, resolved)
        return resolved

    if not downloaded:
        resolved["reason_unavailable"] = "no_safe_result"
        _write_manifest(run_id, beat_index, resolved)
        return resolved

    resolved.update(downloaded)
    resolved["available"] = True
    resolved["reason_unavailable"] = None
    _write_manifest(run_id, beat_index, resolved)
    return resolved


def asset_path_for_sfx(name: str, *, download: bool = False) -> Path | None:
    key = _sound_key(name)
    aliases = {
        "camera": "camera_click",
        "click": "camera_click",
        "camera_click": "camera_click",
        "typewriter": "typewriter_tick",
        "typewriter_tick": "typewriter_tick",
        "glitch": "glitch_burst",
        "glitch_burst": "glitch_burst",
        "boom": "boom",
        "whoosh": "whoosh",
    }
    stem = aliases.get(key, key)
    existing = _first_existing(SFX_DIR, stem)
    if existing or not download:
        return existing
    print(f"[assets] Missing SFX '{stem}'. Searching safe audio sources...")
    return _download_safe_audio(stem, SFX_DIR, query=_sound_search_query(stem, kind="sfx"))


def asset_path_for_ambience(name: str, *, download: bool = False) -> Path | None:
    stem = _sound_key(name)
    existing = _first_existing(AMBIENCE_DIR, stem)
    if existing or not download:
        return existing
    print(f"[assets] Missing ambience '{stem}'. Searching safe audio sources...")
    return _download_safe_audio(stem, AMBIENCE_DIR, query=_sound_search_query(stem, kind="ambience"))


def _download_safe_archival_image(query: str) -> dict[str, Any] | None:
    for candidate_query in _image_query_variants(query):
        downloaded = _download_wikimedia_commons_image(candidate_query, cache_query=query)
        if downloaded:
            return downloaded
    for candidate_query in _image_query_variants(query):
        downloaded = _download_openverse_image(candidate_query, cache_query=query)
        if downloaded:
            return downloaded
    return None


def _download_wikimedia_commons_image(query: str, *, cache_query: str | None = None) -> dict[str, Any] | None:
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": "3",
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime",
        "format": "json",
    }
    api_url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    payload = _read_json_url(api_url)
    pages = payload.get("query", {}).get("pages", {})
    if not isinstance(pages, dict):
        return None

    for page in pages.values():
        infos = page.get("imageinfo") if isinstance(page, dict) else None
        if not infos:
            continue
        info = infos[0]
        image_url = str(info.get("url") or "")
        suffix = Path(urllib.parse.urlparse(image_url).path).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            continue
        title = str(page.get("title") or query)
        dest_dir = ARCHIVAL_DIR / _slugify(cache_query or query)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{_slugify(title)}{suffix}"
        _download_file(image_url, dest)
        metadata = info.get("extmetadata") if isinstance(info.get("extmetadata"), dict) else {}
        return {
            "provider": "wikimedia_commons",
            "query": query,
            "asset_path": str(dest),
            "source_url": image_url,
            "source_page": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
            "license": _metadata_value(metadata, "LicenseShortName") or _metadata_value(metadata, "License") or "unknown",
            "artist": _strip_html(_metadata_value(metadata, "Artist") or ""),
            "credit": _strip_html(_metadata_value(metadata, "Credit") or ""),
        }
    return None


def _download_openverse_image(query: str, *, cache_query: str | None = None) -> dict[str, Any] | None:
    params = {
        "q": query,
        "license": "cc0,pdm,by,by-sa",
        "page_size": "20",
        "mature": "false",
    }
    api_url = "https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(params)
    payload = _read_json_url(api_url)
    results = payload.get("results")
    if not isinstance(results, list):
        return None

    for item in results:
        if not isinstance(item, dict):
            continue
        license_name = str(item.get("license") or "").lower()
        if not _is_safe_audio_license(license_name):
            continue
        image_url = str(item.get("url") or item.get("thumbnail") or "")
        suffix = Path(urllib.parse.urlparse(image_url).path).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            suffix = ".jpg"
        dest_dir = ARCHIVAL_DIR / _slugify(cache_query or query)
        dest_dir.mkdir(parents=True, exist_ok=True)
        title = str(item.get("title") or query)
        dest = dest_dir / f"{_slugify(title)}{suffix}"
        try:
            _download_file(image_url, dest)
        except Exception:
            continue
        return {
            "provider": "openverse",
            "query": query,
            "asset_path": str(dest),
            "source_url": image_url,
            "source_page": item.get("foreign_landing_url"),
            "license": item.get("license") or "unknown",
            "artist": item.get("creator") or "",
            "credit": item.get("attribution") or "",
        }
    return None


def _download_safe_audio(stem: str, root: Path, *, query: str) -> Path | None:
    for candidate_query in _sound_query_variants(stem, query):
        downloaded = _download_openverse_audio(stem, root, query=candidate_query)
        if downloaded:
            print(f"[assets] Downloaded audio asset: {downloaded}")
            return downloaded
        downloaded = _download_wikimedia_commons_audio(stem, root, query=candidate_query)
        if downloaded:
            print(f"[assets] Downloaded audio asset: {downloaded}")
            return downloaded
    print(f"[assets] No safe downloadable audio found for '{stem}'.")
    return None


def _download_openverse_audio(stem: str, root: Path, *, query: str) -> Path | None:
    root.mkdir(parents=True, exist_ok=True)
    params = {
        "q": query,
        "license": "cc0,pdm,by,by-sa",
        "page_size": "20",
        "mature": "false",
    }
    api_url = "https://api.openverse.org/v1/audio/?" + urllib.parse.urlencode(params)
    try:
        payload = _read_json_url(api_url)
    except Exception as exc:
        _write_sound_manifest(stem, {"available": False, "query": query, "reason_unavailable": f"api_failed:{type(exc).__name__}:{exc}"})
        return None

    results = payload.get("results")
    if not isinstance(results, list):
        _write_sound_manifest(stem, {"available": False, "query": query, "reason_unavailable": "bad_api_response"})
        return None

    for item in results:
        if not isinstance(item, dict):
            continue
        license_name = str(item.get("license") or "").lower()
        if not _is_safe_audio_license(license_name):
            continue
        audio_url = str(item.get("url") or "")
        suffix = Path(urllib.parse.urlparse(audio_url).path).suffix.lower()
        if suffix not in AUDIO_SUFFIXES:
            continue
        dest = root / f"{stem}{suffix}"
        try:
            _download_file(audio_url, dest)
        except Exception:
            continue
        _write_sound_manifest(
            stem,
            {
                "available": True,
                "query": query,
                "asset_path": str(dest),
                "cache_key": stem,
                "cache_policy": "saved_under_assets_and_reused_before_future_downloads",
                "provider": "openverse",
                "license": item.get("license"),
                "title": item.get("title"),
                "creator": item.get("creator"),
                "source_url": audio_url,
                "source_page": item.get("foreign_landing_url"),
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return dest

    _write_sound_manifest(stem, {"available": False, "query": query, "provider": "openverse", "reason_unavailable": "no_cc0_or_pdm_direct_audio"})
    return None


def _download_wikimedia_commons_audio(stem: str, root: Path, *, query: str) -> Path | None:
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6",
        "gsrlimit": "10",
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime",
        "format": "json",
    }
    api_url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)
    try:
        payload = _read_json_url(api_url)
    except Exception as exc:
        _write_sound_manifest(stem, {"available": False, "query": query, "provider": "wikimedia_commons", "reason_unavailable": f"api_failed:{type(exc).__name__}:{exc}"})
        return None

    pages = payload.get("query", {}).get("pages", {})
    if not isinstance(pages, dict):
        _write_sound_manifest(stem, {"available": False, "query": query, "provider": "wikimedia_commons", "reason_unavailable": "bad_api_response"})
        return None

    for page in pages.values():
        infos = page.get("imageinfo") if isinstance(page, dict) else None
        if not infos:
            continue
        info = infos[0]
        mime = str(info.get("mime") or "")
        if not mime.startswith("audio/"):
            continue
        metadata = info.get("extmetadata") if isinstance(info.get("extmetadata"), dict) else {}
        license_name = (_metadata_value(metadata, "LicenseShortName") or _metadata_value(metadata, "License") or "").lower()
        if not _is_public_domain_audio_license(license_name):
            continue
        audio_url = str(info.get("url") or "")
        suffix = Path(urllib.parse.urlparse(audio_url).path).suffix.lower()
        if suffix not in AUDIO_SUFFIXES:
            continue
        dest = root / f"{stem}{suffix}"
        try:
            _download_file(audio_url, dest)
        except Exception:
            continue
        title = str(page.get("title") or query)
        _write_sound_manifest(
            stem,
            {
                "available": True,
                "query": query,
                "asset_path": str(dest),
                "cache_key": stem,
                "cache_policy": "saved_under_assets_and_reused_before_future_downloads",
                "provider": "wikimedia_commons",
                "license": license_name,
                "title": title,
                "artist": _strip_html(_metadata_value(metadata, "Artist") or ""),
                "credit": _strip_html(_metadata_value(metadata, "Credit") or ""),
                "source_url": audio_url,
                "source_page": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return dest

    _write_sound_manifest(stem, {"available": False, "query": query, "provider": "wikimedia_commons", "reason_unavailable": "no_public_domain_audio"})
    return None


def _find_cached_archival_image(query: str) -> Path | None:
    root = ARCHIVAL_DIR / _slugify(query)
    if not root.exists():
        return None
    for path in root.iterdir():
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            return path
    return None


def _write_manifest(run_id: str, beat_index: int, payload: dict[str, Any]) -> None:
    manifest_dir = ARCHIVAL_DIR / "_manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data["run_id"] = run_id
    data["beat_index"] = beat_index
    data["resolved_at"] = datetime.now(timezone.utc).isoformat()
    path = manifest_dir / f"{_slugify(run_id)}_beat_{beat_index:02d}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_sound_manifest(stem: str, payload: dict[str, Any]) -> None:
    SOUND_MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data["resolved_at"] = datetime.now(timezone.utc).isoformat()
    path = SOUND_MANIFEST_DIR / f"{_slugify(stem)}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json_url(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "yt-auto-documentary-generator/0.1 (asset resolver; Wikimedia Commons API)"
        },
    )
    with urllib.request.urlopen(request, timeout=18) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_file(url: str, dest: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "yt-auto-documentary-generator/0.1 (asset resolver; Wikimedia Commons API)"
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        dest.write_bytes(response.read())


def _metadata_value(metadata: dict[str, Any], key: str) -> str | None:
    item = metadata.get(key)
    if isinstance(item, dict):
        value = item.get("value")
        if value is not None:
            return str(value)
    return None


def _is_public_domain_audio_license(value: str) -> bool:
    cleaned = value.lower().replace("-", " ")
    return "cc0" in cleaned or "public domain" in cleaned or cleaned.strip() in {"pd", "pdm"}


def _is_safe_audio_license(value: str) -> bool:
    cleaned = value.lower().replace("-", " ").replace("_", " ").strip()
    if not cleaned:
        return False
    if "nc" in cleaned or "noncommercial" in cleaned or "nd" in cleaned or "no derivatives" in cleaned:
        return False
    return (
        "cc0" in cleaned
        or "public domain" in cleaned
        or cleaned in {"pd", "pdm", "by", "by sa", "cc by", "cc by sa"}
        or cleaned.startswith("by ")
    )


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


def _first_existing(root: Path, stem: str) -> Path | None:
    for suffix in (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"):
        candidate = root / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _sound_search_query(stem: str, *, kind: str) -> str:
    words = stem.replace("_", " ")
    return words


def _sound_query_variants(stem: str, query: str) -> list[str]:
    base = query.strip() or stem.replace("_", " ")
    base = re.sub(r"\s+", " ", base.replace("_", " ")).strip()
    variants = [
        base,
        f"{base} sound effect",
        f"{base} ambience",
        f"{base} ambient sound",
        f"{base} field recording",
        f"{base} foley",
    ]
    seen = set()
    unique = []
    for item in variants:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _image_query_variants(query: str) -> list[str]:
    base = re.sub(r"\s+", " ", str(query or "").replace("_", " ")).strip()
    cleaned = re.sub(
        r"\b(wikimedia|commons|press photo|photo|portrait|screenshot|archive|official|image|picture)\b",
        " ",
        base,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(18|19|20)\d{2}\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    title_names = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b", base)
    variants = [base, cleaned, *title_names]
    seen = set()
    unique = []
    for item in variants:
        item = re.sub(r"\s+", " ", item).strip()
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _sound_key(value: str) -> str:
    return _slugify(value).replace("-", "_")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug[:80] or "asset"
