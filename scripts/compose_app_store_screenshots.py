#!/usr/bin/env python3
"""Compose App Store screenshots only from captured EaselWall UI and output.

This intentionally does not draw controls, device frames, headlines, or other
mock UI. Every foreground is a native EaselWall window capture. Settings use
wallpaper PNGs produced by EaselWall's MatRenderer; the menu uses only the
wallpaper set rendered by that same menu process, or a neutral backdrop when a
paired menu wallpaper is unavailable. Every native window must have matching
readiness provenance bound to the exact PNG dimensions and hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = ROOT / "screenshots" / "real" / "source"
DEFAULT_OUTPUT_DIR = ROOT / "screenshots" / "real" / "for-upload"

SIZE = (1280, 800)
OUTPUTS = (
    ("01-customize-mats.png", "settings-appearance.png", "settings", "landscape", "left"),
    ("02-every-display.png", "settings-displays.png", "settings", "split", "left"),
    ("03-curated-gallery.png", "settings-gallery.png", "settings", "landscape", "right"),
    ("04-daily-schedule.png", "settings-schedule.png", "settings", "landscape", "left"),
    ("05-current-painting.png", "menu.png", "menu", "landscape", "right"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_wallpapers(source_dir: Path) -> Tuple[List[Path], List[Path]]:
    landscape: List[Path] = []
    portrait: List[Path] = []
    for path in sorted(source_dir.glob("wallpaper-*.png")):
        with Image.open(path) as image:
            if image.width >= image.height:
                landscape.append(path)
            else:
                portrait.append(path)

    if not landscape:
        raise ValueError("capture source has no landscape wallpaper-*.png")
    return landscape, portrait


def discover_menu_wallpapers(source_dir: Path) -> List[Path]:
    return sorted(source_dir.glob("menu-wallpaper-*.png"))


def readiness_path_for(path: Path) -> Path:
    return path.with_suffix(".ready.json")


def load_readiness(path: Path) -> Dict[str, object]:
    if not path.is_file():
        raise ValueError(f"missing native readiness provenance: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid native readiness provenance {path.name}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"native readiness provenance is not an object: {path.name}")
    return payload


def positive_number(payload: Dict[str, object], field: str, ready_path: Path) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(
            f"native readiness {ready_path.name} has invalid {field}: {value!r}"
        )
    return float(value)


def validate_ui_capture(path: Path, kind: str) -> Path:
    if not path.is_file():
        raise ValueError(f"missing native UI capture: {path.name}")

    with Image.open(path) as image:
        width, height = image.size

    if kind == "settings":
        if not (450 <= width <= 1600 and 300 <= height <= 1200):
            raise ValueError(
                f"{path.name} is not a plausible isolated Settings window capture: "
                f"{width}x{height}"
            )
    elif kind == "menu":
        if not (250 <= width <= 1000 and 180 <= height <= 1200):
            raise ValueError(
                f"{path.name} is not a plausible isolated menu capture: {width}x{height}"
            )
    else:
        raise ValueError(f"unsupported UI capture kind: {kind}")

    ready_path = readiness_path_for(path)
    readiness = load_readiness(ready_path)
    if readiness.get("kind") != kind:
        raise ValueError(
            f"native readiness {ready_path.name} has kind {readiness.get('kind')!r}, "
            f"expected {kind!r}"
        )
    positive_number(readiness, "pid", ready_path)
    positive_number(readiness, "windowID", ready_path)
    positive_number(readiness, "scale", ready_path)

    captured_width = positive_number(readiness, "capturePixelWidth", ready_path)
    captured_height = positive_number(readiness, "capturePixelHeight", ready_path)
    if (captured_width, captured_height) != (float(width), float(height)):
        raise ValueError(
            f"native readiness {ready_path.name} dimensions do not match {path.name}: "
            f"{captured_width:g}x{captured_height:g} vs {width}x{height}"
        )
    capture_hash = readiness.get("captureSha256")
    if capture_hash != sha256(path):
        raise ValueError(
            f"native readiness {ready_path.name} hash does not match {path.name}"
        )

    if kind == "settings":
        content_width = positive_number(readiness, "widthPoints", ready_path)
        content_height = positive_number(readiness, "heightPoints", ready_path)
        if not (450 <= content_width <= 1600 and 300 <= content_height <= 1200):
            raise ValueError(
                f"native readiness {ready_path.name} has implausible Settings dimensions: "
                f"{content_width:g}x{content_height:g}"
            )
    return ready_path


def fit_background(path: Path, size: Tuple[int, int] = SIZE) -> Image.Image:
    with Image.open(path) as source:
        return ImageOps.fit(
            source.convert("RGB"),
            size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )


def split_background(landscape: Path, portrait: Optional[Path]) -> Image.Image:
    if portrait is None:
        return fit_background(landscape)

    left_width = 855
    canvas = Image.new("RGB", SIZE)
    canvas.paste(fit_background(landscape, (left_width, SIZE[1])), (0, 0))
    canvas.paste(
        fit_background(portrait, (SIZE[0] - left_width, SIZE[1])),
        (left_width, 0),
    )
    return canvas


def neutral_menu_background() -> Image.Image:
    return Image.new("RGB", SIZE, "#202329")


def scaled_foreground(path: Path, kind: str) -> Image.Image:
    with Image.open(path) as source:
        foreground = source.convert("RGBA")

    max_size = (840, 635) if kind == "settings" else (535, 650)
    scale = min(max_size[0] / foreground.width, max_size[1] / foreground.height)
    foreground = foreground.resize(
        (
            max(1, round(foreground.width * scale)),
            max(1, round(foreground.height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    return foreground


def paste_foreground(
    canvas: Image.Image,
    foreground: Image.Image,
    placement: str,
    kind: str,
) -> None:
    if kind == "menu":
        x = SIZE[0] - foreground.width - 64
        y = 64
    else:
        if placement == "left":
            x = 42
        elif placement == "right":
            x = SIZE[0] - foreground.width - 42
        else:
            x = (SIZE[0] - foreground.width) // 2
        y = (SIZE[1] - foreground.height) // 2

    canvas.paste(foreground, (x, y), foreground)


def archive_existing(output_dir: Path) -> None:
    existing = sorted(output_dir.glob("*.png"))
    manifest = output_dir / "manifest.json"
    if manifest.exists():
        existing.append(manifest)
    if not existing:
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = output_dir / "archive" / timestamp
    suffix = 2
    while archive.exists():
        archive = output_dir / "archive" / f"{timestamp}-{suffix}"
        suffix += 1
    archive.mkdir(parents=True)
    for path in existing:
        shutil.move(str(path), archive / path.name)


def compose(source_dir: Path, output_dir: Path) -> Dict[str, object]:
    if not source_dir.is_dir():
        raise ValueError(f"capture source directory does not exist: {source_dir}")

    landscapes, portraits = discover_wallpapers(source_dir)
    menu_wallpapers = discover_menu_wallpapers(source_dir)
    readiness_paths: Dict[str, Path] = {}
    for _, source_name, kind, _, _ in OUTPUTS:
        readiness_paths[source_name] = validate_ui_capture(source_dir / source_name, kind)

    output_dir.mkdir(parents=True, exist_ok=True)
    archive_existing(output_dir)

    manifest_outputs: List[Dict[str, object]] = []
    for index, (output_name, source_name, kind, background_kind, placement) in enumerate(OUTPUTS):
        landscape = landscapes[index % len(landscapes)]
        portrait = portraits[0] if portraits else None
        if kind == "menu":
            if menu_wallpapers:
                canvas = fit_background(menu_wallpapers[0])
                background_sources = [menu_wallpapers[0].name]
                resolved_background_kind = "paired-menu-wallpaper"
            else:
                canvas = neutral_menu_background()
                background_sources = []
                resolved_background_kind = "neutral"
        elif background_kind == "split":
            canvas = split_background(landscape, portrait)
            background_sources = [landscape.name]
            if portrait is not None:
                background_sources.append(portrait.name)
            resolved_background_kind = "captured-wallpaper-split"
        else:
            canvas = fit_background(landscape)
            background_sources = [landscape.name]
            resolved_background_kind = "captured-wallpaper"

        foreground_path = source_dir / source_name
        foreground = scaled_foreground(foreground_path, kind)
        paste_foreground(canvas, foreground, placement, kind)

        output_path = output_dir / output_name
        canvas.convert("RGB").save(output_path, format="PNG", optimize=True)
        with Image.open(output_path) as result:
            if result.size != SIZE or result.mode != "RGB":
                raise AssertionError(
                    f"invalid output {output_name}: size={result.size}, mode={result.mode}"
                )

        manifest_outputs.append(
            {
                "filename": output_name,
                "width": SIZE[0],
                "height": SIZE[1],
                "mode": "RGB",
                "sha256": sha256(output_path),
                "uiCapture": source_name,
                "uiReadiness": readiness_paths[source_name].name,
                "uiReadinessSha256": sha256(readiness_paths[source_name]),
                "backgroundKind": resolved_background_kind,
                "wallpaperCaptures": background_sources,
            }
        )

    manifest: Dict[str, object] = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceDirectory": str(source_dir.resolve()),
        "policy": (
            "native EaselWall UI over paired EaselWall MatRenderer output or a neutral "
            "menu backdrop; no mock UI"
        ),
        "outputs": manifest_outputs,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        manifest = compose(args.source_dir, args.output_dir)
    except (OSError, ValueError) as error:
        raise SystemExit(f"Could not compose real App Store screenshots: {error}")

    for output in manifest["outputs"]:
        print(f"Wrote {args.output_dir / output['filename']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
