from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

MANIFEST_VERSION = 1
PHASE0_JSON_FILES = (
    "origin.json",
    "prelabel.json",
    "label.json",
    "multi-page-final.json",
)


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = _canonical_json(payload).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_or_create_manifest(
    path: Path,
    *,
    fingerprint_payload: Mapping[str, Any],
    jobs: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], bool]:
    """Load a compatible batch manifest or create the first one.

    The boolean in the return value is true only when this call creates the
    manifest.  Job order is part of the manifest so a changed source list
    cannot silently reuse another batch's progress.
    """

    path = Path(path)
    jobs_payload = [dict(job) for job in jobs]
    expected = {
        "version": MANIFEST_VERSION,
        "fingerprint": _fingerprint(fingerprint_payload),
        "fingerprint_payload": dict(fingerprint_payload),
        "jobs": jobs_payload,
    }

    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid batch manifest: {path}") from exc
        if not isinstance(existing, dict):
            raise ValueError(f"invalid batch manifest: {path}")
        if (
            existing.get("version") != expected["version"]
            or existing.get("fingerprint") != expected["fingerprint"]
            or existing.get("jobs") != expected["jobs"]
        ):
            raise ValueError(f"batch manifest mismatch: {path}")
        return existing, False

    write_json_atomic(path, expected)
    return expected, True


def load_latest_progress(path: Path) -> dict[str, dict[str, Any]]:
    """Fold progress events by job id.

    A process can be interrupted while appending the last line.  That one
    incomplete final line is ignored; malformed earlier lines are reported
    because they indicate a damaged progress log.
    """

    path = Path(path)
    if not path.exists():
        return {}

    lines = path.read_text(encoding="utf-8").splitlines()
    non_empty_indexes = [index for index, line in enumerate(lines) if line.strip()]
    final_non_empty = non_empty_indexes[-1] if non_empty_indexes else None
    latest: dict[str, dict[str, Any]] = {}

    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            if index == final_non_empty:
                break
            raise ValueError(f"invalid progress event at line {index + 1}: {path}") from exc
        if not isinstance(event, dict) or not str(event.get("job_id", "")).strip():
            raise ValueError(f"invalid progress event at line {index + 1}: {path}")
        latest[str(event["job_id"])] = event
    return latest


def append_progress(path: Path, event: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(event), ensure_ascii=False, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def output_is_complete(out_dir: Path, seq: int) -> bool:
    """Return whether an output directory satisfies the phase-0 contract."""

    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        return False
    if any(not (out_dir / name).is_file() for name in PHASE0_JSON_FILES):
        return False

    try:
        origin = _read_json(out_dir / "origin.json")
        for name in PHASE0_JSON_FILES[1:]:
            _read_json(out_dir / name)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False

    if not isinstance(origin, dict):
        return False
    if str(origin.get("task_id", "")) != f"synth_task_{seq:03d}":
        return False

    raw_images_path = origin.get("images_path")
    if not isinstance(raw_images_path, str) or not raw_images_path.strip():
        return False
    images_path = Path(raw_images_path)
    if not images_path.is_absolute():
        images_path = out_dir / images_path
    if not images_path.is_dir():
        return False
    return any(path.is_file() for path in images_path.glob("raw-page-*.png"))


def find_recoverable_output(output_root: Path, seq: int) -> Path | None:
    output_root = Path(output_root)
    if not output_root.is_dir():
        return None
    candidates = sorted(
        path
        for path in output_root.glob(f"synth_{seq:03d}_*")
        if path.is_dir() and output_is_complete(path, seq)
    )
    return candidates[0] if len(candidates) == 1 else None


def _atomic_write(path: Path, content: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(path: Path, payload: Any) -> None:
    _atomic_write(
        Path(path),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def write_text_atomic(path: Path, content: str) -> None:
    _atomic_write(Path(path), content)
