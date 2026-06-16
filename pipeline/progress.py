from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal


WorkerStatus = Literal["waiting", "running", "done", "failed"]
ProgressCallback = Callable[["ProgressEvent"], None]


@dataclass(frozen=True)
class ProgressEvent:
    worker: str
    status: WorkerStatus
    message: str
    progress: float | None = None
    artifact_path: str | None = None
    preview_path: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def emit(
    callback: ProgressCallback | None,
    worker: str,
    status: WorkerStatus,
    message: str,
    *,
    progress: float | None = None,
    artifact_path: str | Path | None = None,
    preview_path: str | Path | None = None,
    error: str | None = None,
) -> None:
    if callback is None:
        return
    callback(
        ProgressEvent(
            worker=worker,
            status=status,
            message=message,
            progress=_clamp_progress(progress),
            artifact_path=str(artifact_path) if artifact_path else None,
            preview_path=str(preview_path) if preview_path else None,
            error=error,
        )
    )


def _clamp_progress(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))
