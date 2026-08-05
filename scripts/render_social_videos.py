#!/usr/bin/env python3
"""Render deterministic vertical social-video masters for EaselWall.

The renderer deliberately creates silent masters. Licensed platform audio and
the commercial-content disclosure are added during the human publishing step.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
CONTENT_FILE = ROOT / "marketing" / "videos.json"
OUTPUT_DIR = ROOT / "marketing" / "renders"

WIDTH = 1080
HEIGHT = 1920
FPS = 30
SCENE_DURATIONS = (2.6, 3.4, 2.4)
CROSSFADE = 0.35

PAPER = "#F1E9D7"
INK = "#1A120B"
BRASS = "#A87433"
VERMILLION = "#B5381C"

DISPLAY_FONT = Path("/System/Library/Fonts/NewYork.ttf")
SANS_FONT = Path("/System/Library/Fonts/SFNS.ttf")
MONO_FONT = Path("/System/Library/Fonts/SFNSMono.ttf")

HASHTAGS = "#MacApps #DeskSetup #Wallpaper #DigitalArt #EaselWall"


def require_tool(name: str) -> str:
    tool = shutil.which(name)
    if not tool:
        raise SystemExit(f"Required tool not found: {name}")
    return tool


def load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if not path.exists():
        raise SystemExit(f"Required system font not found: {path}")
    return ImageFont.truetype(str(path), size=size)


def relative_asset(raw_path: str) -> Path:
    path = (ROOT / raw_path).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError(f"Asset escapes repository root: {raw_path}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"Missing asset: {raw_path}")
    return path


def cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)


def contain(image: Image.Image, size: tuple[int, int], color: str = PAPER) -> Image.Image:
    canvas = Image.new("RGB", size, color)
    fitted = ImageOps.contain(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)
    x = (size[0] - fitted.width) // 2
    y = (size[1] - fitted.height) // 2
    canvas.paste(fitted, (x, y))
    return canvas


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return "\n".join(lines)


def letterspaced_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: str,
    spacing: int,
) -> None:
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        bbox = draw.textbbox((x, y), char, font=font)
        x = bbox[2] + spacing


def make_background(asset: Path) -> Image.Image:
    with Image.open(asset) as source:
        background = cover(source, (WIDTH, HEIGHT))
    background = background.filter(ImageFilter.GaussianBlur(42))
    background = ImageEnhance.Brightness(background).enhance(0.42)
    tint = Image.new("RGBA", (WIDTH, HEIGHT), (26, 18, 11, 90))
    return Image.alpha_composite(background.convert("RGBA"), tint)


def card_shadow(canvas: Image.Image, box: tuple[int, int, int, int], radius: int = 38) -> None:
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    offset_box = (box[0] + 8, box[1] + 18, box[2] + 8, box[3] + 18)
    shadow_draw.rounded_rectangle(offset_box, radius=radius, fill=(0, 0, 0, 145))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))


def product_composite(asset_paths: list[Path], size: tuple[int, int]) -> Image.Image:
    if len(asset_paths) == 1:
        with Image.open(asset_paths[0]) as image:
            return contain(image, size)

    panel = Image.new("RGB", size, PAPER)
    gap = 14
    if len(asset_paths) == 3:
        portrait_index: int | None = None
        for index, asset_path in enumerate(asset_paths):
            with Image.open(asset_path) as image:
                if image.height > image.width:
                    portrait_index = index
                    break

        if portrait_index is not None:
            portrait_path = asset_paths[portrait_index]
            landscape_paths = [
                path for index, path in enumerate(asset_paths) if index != portrait_index
            ]
            cell_width = (size[0] - gap) // 2
            landscape_height = (size[1] - gap) // 2
            for index, asset_path in enumerate(landscape_paths):
                with Image.open(asset_path) as image:
                    tile = cover(image, (cell_width, landscape_height))
                panel.paste(tile, (0, index * (landscape_height + gap)))
            with Image.open(portrait_path) as image:
                portrait_tile = cover(image, (size[0] - cell_width - gap, size[1]))
            panel.paste(portrait_tile, (cell_width + gap, 0))
            return panel

    if len(asset_paths) <= 4:
        columns = 2
        rows = 2
    else:
        columns = 3
        rows = 2
    cell_width = (size[0] - gap * (columns - 1)) // columns
    cell_height = (size[1] - gap * (rows - 1)) // rows
    for index, asset_path in enumerate(asset_paths[: columns * rows]):
        with Image.open(asset_path) as image:
            tile = cover(image, (cell_width, cell_height))
        x = (index % columns) * (cell_width + gap)
        y = (index // columns) * (cell_height + gap)
        panel.paste(tile, (x, y))
    return panel


def draw_product_card(canvas: Image.Image, asset_paths: list[Path]) -> None:
    # Keep the proof copy above TikTok's bottom caption/audio overlay and leave
    # the right edge as expendable visual space for the platform action rail.
    x1, y1, x2, y2 = 70, 590, WIDTH - 70, 1325
    card_shadow(canvas, (x1, y1, x2, y2))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((x1, y1, x2, y2), radius=40, fill=INK, outline=(255, 255, 255, 75), width=2)
    inner = (x1 + 22, y1 + 54, x2 - 22, y2 - 24)
    composite = product_composite(asset_paths, (inner[2] - inner[0], inner[3] - inner[1]))
    mask = Image.new("L", composite.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, composite.width, composite.height), radius=22, fill=255)
    canvas.paste(composite, (inner[0], inner[1]), mask)
    for color, x in (("#FF5F57", x1 + 30), ("#FEBC2E", x1 + 58), ("#28C840", x1 + 86)):
        draw.ellipse((x, y1 + 20, x + 14, y1 + 34), fill=color)


def draw_header(canvas: Image.Image) -> None:
    draw = ImageDraw.Draw(canvas)
    label_font = load_font(MONO_FONT, 26)
    letterspaced_text(draw, (70, 72), "EASELWALL  ·  FOR MAC", label_font, PAPER, 2)
    draw.line((70, 118, WIDTH - 70, 118), fill=(241, 233, 215, 105), width=2)


def create_visual_scene(video: dict[str, Any], assets: list[str], proof_scene: bool) -> Image.Image:
    asset_paths = [relative_asset(path) for path in assets]
    canvas = make_background(asset_paths[0])
    draw_header(canvas)
    draw = ImageDraw.Draw(canvas)

    hook_font = load_font(DISPLAY_FONT, 86 if video["hook"].count("\n") < 3 else 75)
    proof_font = load_font(SANS_FONT, 40)
    hook = wrap_text(draw, video["hook"], hook_font, WIDTH - 140)
    draw.multiline_text((70, 175), hook, font=hook_font, fill=PAPER, spacing=3)

    draw_product_card(canvas, asset_paths)

    if proof_scene:
        proof = wrap_text(draw, video["proof"], proof_font, WIDTH - 260)
        draw.multiline_text((70, 1380), proof, font=proof_font, fill=PAPER, spacing=8)
    else:
        eyebrow = load_font(MONO_FONT, 24)
        letterspaced_text(draw, (70, 1380), "A DAILY EXHIBITION", eyebrow, "#D7B77F", 2)
        subhead_font = load_font(SANS_FONT, 36)
        draw.text((70, 1432), "Public-domain art, composed for your display.", font=subhead_font, fill=PAPER)
    return canvas


def create_cta_scene(video: dict[str, Any], cta: str) -> Image.Image:
    icon_path = relative_asset("website/assets/icon-512.png")
    with Image.open(icon_path) as icon_source:
        icon = icon_source.convert("RGBA").resize((280, 280), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), INK)
    draw = ImageDraw.Draw(canvas)
    draw_header(canvas)

    icon_x = (WIDTH - icon.width) // 2
    canvas.alpha_composite(icon, (icon_x, 365))

    display = load_font(DISPLAY_FONT, 94)
    sans = load_font(SANS_FONT, 42)
    mono = load_font(MONO_FONT, 27)
    title = "Tomorrow deserves\na better desktop."
    title_box = draw.multiline_textbbox((0, 0), title, font=display, spacing=5, align="center")
    draw.multiline_text(((WIDTH - (title_box[2] - title_box[0])) // 2, 730), title, font=display, fill=PAPER, spacing=5, align="center")

    cta_box = (110, 1140, WIDTH - 110, 1275)
    draw.rounded_rectangle(cta_box, radius=12, fill=PAPER)
    cta_bbox = draw.textbbox((0, 0), cta, font=sans)
    draw.text(((WIDTH - (cta_bbox[2] - cta_bbox[0])) // 2, 1180), cta, font=sans, fill=INK)

    letterspaced_text(draw, (165, 1370), "MAC APP STORE  ·  LINK IN BIO", mono, "#D7B77F", 1)
    footer = "ONE-TIME PURCHASE  ·  NO ACCOUNT  ·  NO TRACKING"
    footer_bbox = draw.textbbox((0, 0), footer, font=mono)
    draw.text(((WIDTH - (footer_bbox[2] - footer_bbox[0])) // 2, 1460), footer, font=mono, fill=(241, 233, 215, 180))
    return canvas


def run_ffmpeg(scene_paths: list[Path], output: Path) -> None:
    ffmpeg = require_tool("ffmpeg")
    d1, d2, d3 = SCENE_DURATIONS
    offset1 = d1 - CROSSFADE
    offset2 = d1 + d2 - (2 * CROSSFADE)
    total = d1 + d2 + d3 - (2 * CROSSFADE)

    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for scene, duration in zip(scene_paths, SCENE_DURATIONS):
        command.extend(["-loop", "1", "-framerate", str(FPS), "-t", str(duration), "-i", str(scene)])
    command.extend(["-f", "lavfi", "-t", str(total), "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])

    filters = (
        f"[0:v]scale={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p,trim=duration={d1},setpts=PTS-STARTPTS[v0];"
        f"[1:v]scale={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p,trim=duration={d2},setpts=PTS-STARTPTS[v1];"
        f"[2:v]scale={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p,trim=duration={d3},setpts=PTS-STARTPTS[v2];"
        f"[v0][v1]xfade=transition=fade:duration={CROSSFADE}:offset={offset1}[x1];"
        f"[x1][v2]xfade=transition=fade:duration={CROSSFADE}:offset={offset2}[outv]"
    )
    command.extend(
        [
            "-filter_complex",
            filters,
            "-map",
            "[outv]",
            "-map",
            "3:a",
            "-t",
            str(total),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    subprocess.run(command, check=True)


def inspect_video(path: Path) -> dict[str, Any]:
    ffprobe = require_tool("ffprobe")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(completed.stdout)
    video_stream = next(stream for stream in data["streams"] if stream["codec_type"] == "video")
    duration = float(data["format"]["duration"])
    if (video_stream.get("width"), video_stream.get("height")) != (WIDTH, HEIGHT):
        raise RuntimeError(f"Unexpected dimensions for {path.name}: {video_stream}")
    if video_stream.get("codec_name") != "h264" or video_stream.get("pix_fmt") != "yuv420p":
        raise RuntimeError(f"Unexpected video encoding for {path.name}: {video_stream}")
    if not 7.5 <= duration <= 8.0:
        raise RuntimeError(f"Unexpected duration for {path.name}: {duration}")
    return {
        "file": path.name,
        "width": video_stream["width"],
        "height": video_stream["height"],
        "codec": video_stream["codec_name"],
        "pixelFormat": video_stream["pix_fmt"],
        "frameRate": video_stream["r_frame_rate"],
        "durationSeconds": round(duration, 3),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def render_video(video: dict[str, Any], default_cta: str) -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / f"{video['id']}.mp4"
    with tempfile.TemporaryDirectory(prefix=f"easelwall-{video['id']}-") as temp_dir:
        temporary = Path(temp_dir)
        scenes = [
            create_visual_scene(video, video["assets"][0], proof_scene=False),
            create_visual_scene(video, video["assets"][1], proof_scene=True),
            create_cta_scene(video, video.get("cta", default_cta)),
        ]
        scene_paths: list[Path] = []
        for index, scene in enumerate(scenes):
            scene_path = temporary / f"scene-{index}.png"
            scene.convert("RGB").save(scene_path, format="PNG", optimize=True)
            scene_paths.append(scene_path)
        run_ffmpeg(scene_paths, output)
    result = inspect_video(output)
    result["id"] = video["id"]
    print(f"Rendered {output.relative_to(ROOT)} ({result['durationSeconds']}s, {result['bytes'] / 1_000_000:.1f} MB)")
    return result


def write_ledgers(videos: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    plan_path = OUTPUT_DIR / "posting-plan.csv"
    with plan_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["order", "file", "caption", "hashtags", "disclosure", "audio", "profile_link"],
        )
        writer.writeheader()
        for order, video in enumerate(videos, start=1):
            writer.writerow(
                {
                    "order": order,
                    "file": f"{video['id']}.mp4",
                    "caption": video["caption"],
                    "hashtags": HASHTAGS,
                    "disclosure": "Promotional content / Your brand",
                    "audio": "Add a licensed TikTok Commercial Music Library track before publishing",
                    "profile_link": "https://easelwall.com/tiktok",
                }
            )
    manifest_path = OUTPUT_DIR / "render-manifest.json"
    manifest_path.write_text(json.dumps({"version": 1, "renders": results}, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {plan_path.relative_to(ROOT)}")
    print(f"Wrote {manifest_path.relative_to(ROOT)}")


def create_contact_sheet(results: list[dict[str, Any]]) -> Path:
    """Create a compact visual-QA sheet with hook and proof frames."""
    ffmpeg = require_tool("ffmpeg")
    thumb_width, thumb_height = 270, 480
    columns = 4
    rows = max(1, (len(results) * 2 + columns - 1) // columns)
    sheet = Image.new("RGB", (columns * thumb_width, rows * thumb_height), INK)
    label_font = load_font(MONO_FONT, 18)

    with tempfile.TemporaryDirectory(prefix="easelwall-contact-sheet-") as temp_dir:
        temporary = Path(temp_dir)
        index = 0
        for result in results:
            video_path = OUTPUT_DIR / result["file"]
            for timestamp, phase in ((0.8, "HOOK"), (4.0, "PROOF")):
                frame_path = temporary / f"{result['id']}-{phase.lower()}.png"
                subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-ss",
                        str(timestamp),
                        "-i",
                        str(video_path),
                        "-frames:v",
                        "1",
                        str(frame_path),
                    ],
                    check=True,
                )
                with Image.open(frame_path) as frame:
                    thumb = cover(frame, (thumb_width, thumb_height))
                overlay = Image.new("RGBA", thumb.size, (0, 0, 0, 0))
                overlay_draw = ImageDraw.Draw(overlay)
                overlay_draw.rectangle((0, 0, thumb_width, 38), fill=(0, 0, 0, 180))
                overlay_draw.text((10, 9), f"{result['id']} · {phase}", font=label_font, fill=PAPER)
                thumb = Image.alpha_composite(thumb.convert("RGBA"), overlay).convert("RGB")
                x = (index % columns) * thumb_width
                y = (index // columns) * thumb_height
                sheet.paste(thumb, (x, y))
                index += 1

    output = OUTPUT_DIR / "contact-sheet.jpg"
    sheet.save(output, format="JPEG", quality=90, optimize=True)
    print(f"Wrote {output.relative_to(ROOT)}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", help="Render every video in the manifest")
    selection.add_argument("--id", help="Render one video by id")
    selection.add_argument("--contact-sheet", action="store_true", help="Rebuild visual QA from existing renders")
    args = parser.parse_args()

    if args.id is not None and not args.id.strip():
        parser.error("--id requires a non-empty video id")

    require_tool("ffmpeg")
    require_tool("ffprobe")
    content = json.loads(CONTENT_FILE.read_text(encoding="utf-8"))
    all_videos: list[dict[str, Any]] = content["videos"]
    if args.contact_sheet:
        manifest_path = OUTPUT_DIR / "render-manifest.json"
        if not manifest_path.is_file():
            parser.error("No render manifest found; run --all first")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        create_contact_sheet(manifest["renders"])
        return 0
    selected_videos = all_videos
    if args.id is not None:
        selected_videos = [video for video in all_videos if video["id"] == args.id]
        if not selected_videos:
            parser.error(f"Unknown video id: {args.id}")

    rendered = [
        render_video(video, content["defaultCta"]) for video in selected_videos
    ]
    results_by_id = {result["id"]: result for result in rendered}

    # A targeted render should refresh one master without erasing the ledger for
    # the rest of an existing batch. Re-inspect every available output so the
    # manifest remains a verified description of what is actually on disk.
    if args.id is not None:
        for video in all_videos:
            if video["id"] in results_by_id:
                continue
            path = OUTPUT_DIR / f"{video['id']}.mp4"
            if path.is_file():
                existing = inspect_video(path)
                existing["id"] = video["id"]
                results_by_id[video["id"]] = existing

    available_videos = [
        video for video in all_videos if video["id"] in results_by_id
    ]
    results = [results_by_id[video["id"]] for video in available_videos]
    write_ledgers(available_videos, results)
    create_contact_sheet(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
