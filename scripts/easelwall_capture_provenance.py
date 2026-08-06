#!/usr/bin/env python3
"""Bind a native readiness record to the exact captured window PNG."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from PIL import Image


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finalize_readiness(
    ready_path: Path,
    capture_path: Path,
    *,
    expected_kind: str,
    expected_pid: int,
    window_id: int,
) -> Dict[str, Any]:
    try:
        payload = json.loads(ready_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read native readiness {ready_path}: {error}") from error

    if not isinstance(payload, dict):
        raise ValueError(f"native readiness is not a JSON object: {ready_path}")
    if payload.get("kind") != expected_kind:
        raise ValueError(
            f"native readiness kind is {payload.get('kind')!r}, expected {expected_kind!r}"
        )
    if payload.get("pid") != expected_pid:
        raise ValueError(
            f"native readiness PID is {payload.get('pid')!r}, expected {expected_pid}"
        )
    if window_id <= 0:
        raise ValueError(f"captured window ID must be positive, got {window_id}")
    native_window_id = payload.get("windowID")
    if native_window_id not in (None, 0, window_id):
        raise ValueError(
            f"native readiness window ID is {native_window_id!r}, expected {window_id}"
        )

    try:
        with Image.open(capture_path) as image:
            image.load()
            width, height = image.size
    except OSError as error:
        raise ValueError(f"could not read captured window PNG {capture_path}: {error}") from error
    if width <= 0 or height <= 0:
        raise ValueError(f"captured window PNG is empty: {capture_path}")

    payload.update(
        {
            "windowID": window_id,
            "capturePixelWidth": width,
            "capturePixelHeight": height,
            "captureSha256": sha256(capture_path),
        }
    )

    ready_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{ready_path.name}.",
        dir=ready_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, ready_path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise

    return payload


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--kind", choices=("menu", "settings"), required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--window-id", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        finalize_readiness(
            args.ready,
            args.capture,
            expected_kind=args.kind,
            expected_pid=args.pid,
            window_id=args.window_id,
        )
    except (OSError, ValueError) as error:
        raise SystemExit(f"Could not finalize native capture provenance: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
