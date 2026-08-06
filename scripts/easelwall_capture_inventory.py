#!/usr/bin/env python3
"""Validate one fresh MatRenderer output for every backed-up display."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence


WALLPAPER_NAME = re.compile(r"wallpaper_([0-9]+)_([0-9]+)\.png")


class RenderSetError(ValueError):
    """The render set is unsafe and capture must stop immediately."""


class IncompleteRenderSet(RenderSetError):
    """The expected render set has not finished appearing yet."""


@dataclass(frozen=True)
class RenderedWallpaper:
    display_id: int
    path: Path
    size: int
    modified_ns: int


def expected_display_ids(manifest_path: Path) -> tuple[int, ...]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or not manifest:
        raise RenderSetError("wallpaper backup manifest must be a non-empty array")

    display_ids: list[int] = []
    for index, record in enumerate(manifest):
        display_id = record.get("displayID") if isinstance(record, dict) else None
        if isinstance(display_id, bool) or not isinstance(display_id, int):
            raise RenderSetError(
                f"wallpaper backup record {index + 1} has no integer displayID"
            )
        if display_id < 0 or display_id > 0xFFFFFFFF:
            raise RenderSetError(f"displayID {display_id} is outside the UInt32 range")
        display_ids.append(display_id)

    duplicates = sorted(
        display_id for display_id in set(display_ids) if display_ids.count(display_id) > 1
    )
    if duplicates:
        raise RenderSetError(
            "wallpaper backup has duplicate display IDs: "
            + ", ".join(str(display_id) for display_id in duplicates)
        )
    return tuple(sorted(display_ids))


def discover_render_set(
    rendered_dir: Path,
    marker: Path,
    expected_ids: Iterable[int],
) -> tuple[RenderedWallpaper, ...]:
    expected = tuple(sorted(expected_ids))
    if not expected or len(expected) != len(set(expected)):
        raise RenderSetError("expected display IDs must be non-empty and unique")

    marker_modified_ns = marker.stat().st_mtime_ns
    by_display: dict[int, list[RenderedWallpaper]] = {}
    if rendered_dir.is_dir():
        for path in sorted(rendered_dir.glob("wallpaper_*.png")):
            stat = path.stat()
            if stat.st_mtime_ns <= marker_modified_ns:
                continue
            match = WALLPAPER_NAME.fullmatch(path.name)
            if match is None:
                raise RenderSetError(f"unexpected fresh MatRenderer filename: {path.name}")
            display_id = int(match.group(1))
            by_display.setdefault(display_id, []).append(
                RenderedWallpaper(
                    display_id=display_id,
                    path=path,
                    size=stat.st_size,
                    modified_ns=stat.st_mtime_ns,
                )
            )

    expected_set = set(expected)
    extra = sorted(set(by_display) - expected_set)
    if extra:
        raise RenderSetError(
            "fresh MatRenderer output contains unexpected display IDs: "
            + ", ".join(str(display_id) for display_id in extra)
        )

    duplicates = sorted(
        display_id for display_id, files in by_display.items() if len(files) > 1
    )
    if duplicates:
        raise RenderSetError(
            "fresh MatRenderer output contains duplicate display IDs: "
            + ", ".join(str(display_id) for display_id in duplicates)
        )

    missing = [display_id for display_id in expected if display_id not in by_display]
    empty = [
        display_id
        for display_id, files in by_display.items()
        if files[0].size <= 0
    ]
    if missing or empty:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(str(display_id) for display_id in missing))
        if empty:
            details.append("empty " + ", ".join(str(display_id) for display_id in empty))
        raise IncompleteRenderSet("render set is incomplete: " + "; ".join(details))

    return tuple(by_display[display_id][0] for display_id in expected)


def inventory_lines(wallpapers: Iterable[RenderedWallpaper]) -> tuple[str, ...]:
    return tuple(
        f"{wallpaper.display_id}\t{wallpaper.size}\t{wallpaper.modified_ns}\t{wallpaper.path}"
        for wallpaper in wallpapers
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--render-dir", type=Path, required=True)
    parser.add_argument("--marker", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        display_ids = expected_display_ids(args.manifest)
        wallpapers = discover_render_set(args.render_dir, args.marker, display_ids)
    except IncompleteRenderSet as error:
        print(error, file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, RenderSetError) as error:
        print(f"unsafe MatRenderer output: {error}", file=sys.stderr)
        return 1

    for line in inventory_lines(wallpapers):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
