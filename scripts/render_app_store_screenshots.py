#!/usr/bin/env python3
"""Render deterministic 1280x800 Mac App Store screenshots for EaselWall.

Only tracked, public-facing product imagery is used. The renderer never reads
the raw desktop captures in ``screenshots/``; those may contain private UI.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "screenshots" / "for-upload"
ARCHIVE_DIR = ROOT / "screenshots" / "archive" / "for-upload"
MANIFEST = OUTPUT_DIR / "manifest.json"

WIDTH = 1280
HEIGHT = 800

PAPER = "#F2EBDD"
PAPER_LIGHT = "#FBF7EF"
INK = "#17130F"
INK_SOFT = "#403831"
BRASS = "#A97535"
BRASS_LIGHT = "#D5B177"
MOSS = "#53604D"
VERMILLION = "#A53D28"

DISPLAY_FONT = Path("/System/Library/Fonts/NewYork.ttf")
SANS_FONT = Path("/System/Library/Fonts/SFNS.ttf")
MONO_FONT = Path("/System/Library/Fonts/SFNSMono.ttf")

LANDSCAPE_ONE = ROOT / "website" / "assets" / "screen-landscape-1.png"
LANDSCAPE_TWO = ROOT / "website" / "assets" / "screen-landscape-2.png"
PORTRAIT = ROOT / "website" / "assets" / "screen-portrait.png"
ICON = ROOT / "website" / "assets" / "icon-512.png"
APPEARANCE_UI = ROOT / "website" / "assets" / "settings-appearance.png"
SCHEDULE_UI = ROOT / "website" / "assets" / "settings-schedule.png"
GALLERY_SUMMARY_UI = ROOT / "website" / "assets" / "settings-gallery-summary.png"

PAINTINGS = (
    ROOT / "website" / "assets" / "cliff-walk.jpg",
    ROOT / "website" / "assets" / "grande-jatte.jpg",
    ROOT / "website" / "assets" / "water-lilies.jpg",
    ROOT / "website" / "assets" / "bedroom.jpg",
    ROOT / "Resources" / "Paintings" / "images" / "aic_81558.jpg",
    ROOT / "Resources" / "Paintings" / "images" / "aic_80607.jpg",
)

OUTPUTS = (
    "01-daily-masterpiece.png",
    "02-every-display.png",
    "03-custom-museum-mats.png",
    "04-fifty-three-works.png",
    "05-simple-by-design.png",
)

def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if not path.is_file():
        raise SystemExit(f"Required system font not found: {path}")
    return ImageFont.truetype(str(path), size=size)


def require_assets() -> None:
    required = (
        LANDSCAPE_ONE,
        LANDSCAPE_TWO,
        PORTRAIT,
        ICON,
        APPEARANCE_UI,
        SCHEDULE_UI,
        GALLERY_SUMMARY_UI,
        *PAINTINGS,
    )
    missing = [path for path in required if not path.is_file()]
    if missing:
        formatted = "\n".join(f"  - {path.relative_to(ROOT)}" for path in missing)
        raise SystemExit(f"Missing required tracked assets:\n{formatted}")


def vertical_gradient(top: str, bottom: str) -> Image.Image:
    top_rgb = Image.new("RGB", (1, 1), top).getpixel((0, 0))
    bottom_rgb = Image.new("RGB", (1, 1), bottom).getpixel((0, 0))
    gradient = Image.new("RGB", (WIDTH, HEIGHT))
    pixels = gradient.load()
    for y in range(HEIGHT):
        amount = y / (HEIGHT - 1)
        color = tuple(round(a + (b - a) * amount) for a, b in zip(top_rgb, bottom_rgb))
        for x in range(WIDTH):
            pixels[x, y] = color
    return gradient.convert("RGBA")


def add_grain(canvas: Image.Image) -> None:
    """Add a deterministic paper grain without randomness."""
    grain = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(grain)
    for y in range(0, HEIGHT, 7):
        for x in range((y * 13) % 11, WIDTH, 11):
            shade = 10 if (x + y) % 3 else 18
            draw.point((x, y), fill=(255, 247, 226, shade))
    canvas.alpha_composite(grain)


def cover(path: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as source:
        return ImageOps.fit(source.convert("RGB"), size, method=Image.Resampling.LANCZOS)


def contain(path: Path, size: tuple[int, int], color: str = PAPER_LIGHT) -> Image.Image:
    with Image.open(path) as source:
        fitted = ImageOps.contain(source.convert("RGB"), size, method=Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, color)
    canvas.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return canvas


def rounded_paste(canvas: Image.Image, image: Image.Image, xy: tuple[int, int], radius: int) -> None:
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
    canvas.paste(image, xy, mask)


def shadow(canvas: Image.Image, box: tuple[int, int, int, int], radius: int = 22, blur: int = 22, alpha: int = 115) -> None:
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    x1, y1, x2, y2 = box
    ImageDraw.Draw(layer).rounded_rectangle((x1 + 7, y1 + 14, x2 + 7, y2 + 14), radius=radius, fill=(15, 10, 6, alpha))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def letterspaced_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    text_font: ImageFont.FreeTypeFont,
    fill: str,
    spacing: int = 2,
) -> int:
    x, y = xy
    for character in text:
        draw.text((x, y), character, font=text_font, fill=fill)
        bounds = draw.textbbox((x, y), character, font=text_font)
        x = bounds[2] + spacing
    return x


def brand_label(canvas: Image.Image, dark: bool = False) -> None:
    draw = ImageDraw.Draw(canvas)
    fill = PAPER if dark else INK
    muted = BRASS_LIGHT if dark else BRASS
    letterspaced_text(draw, (58, 42), "EASELWALL", font(MONO_FONT, 18), fill, 3)
    draw.ellipse((178, 50, 184, 56), fill=muted)
    draw.text((197, 42), "FOR MAC", font=font(MONO_FONT, 18), fill=muted)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, text_font: ImageFont.FreeTypeFont, max_width: int) -> str:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if draw.textbbox((0, 0), candidate, font=text_font)[2] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return "\n".join(lines)


def display_mockup(
    canvas: Image.Image,
    image_path: Path,
    box: tuple[int, int, int, int],
    *,
    portrait: bool = False,
) -> None:
    x1, y1, x2, y2 = box
    shadow(canvas, box, radius=28, blur=18, alpha=145)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(box, radius=28, fill="#151515", outline=(255, 255, 255, 65), width=2)

    bezel = 13 if portrait else 15
    screen_box = (x1 + bezel, y1 + bezel, x2 - bezel, y2 - 24)
    screen = contain(image_path, (screen_box[2] - screen_box[0], screen_box[3] - screen_box[1]), "#F8F6F0")
    rounded_paste(canvas, screen, (screen_box[0], screen_box[1]), 15)
    draw.ellipse(((x1 + x2) // 2 - 3, y2 - 15, (x1 + x2) // 2 + 3, y2 - 9), fill="#3A3A3A")


def art_card(
    canvas: Image.Image,
    painting: Path,
    box: tuple[int, int, int, int],
    *,
    mat_color: str = PAPER_LIGHT,
    mat: int = 18,
    radius: int = 12,
) -> None:
    x1, y1, x2, y2 = box
    shadow(canvas, box, radius=radius, blur=13, alpha=75)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(box, radius=radius, fill=mat_color)
    inner = (x1 + mat, y1 + mat, x2 - mat, y2 - mat)
    picture = contain(
        painting,
        (inner[2] - inner[0], inner[3] - inner[1]),
        mat_color,
    )
    rounded_paste(canvas, picture, (inner[0], inner[1]), max(3, radius // 3))


def settings_capture(
    canvas: Image.Image,
    capture: Path,
    box: tuple[int, int, int, int],
    *,
    radius: int = 20,
) -> None:
    """Place a genuine, privacy-reviewed EaselWall Settings capture."""
    x1, y1, x2, y2 = box
    shadow(canvas, box, radius=radius, blur=18, alpha=135)
    image = contain(capture, (x2 - x1, y2 - y1), "#242526")
    rounded_paste(canvas, image, (x1, y1), radius)


def screenshot_daily_masterpiece() -> Image.Image:
    canvas = vertical_gradient("#191A1A", "#28251F")
    add_grain(canvas)
    brand_label(canvas, dark=True)
    draw = ImageDraw.Draw(canvas)

    headline = font(DISPLAY_FONT, 64)
    body = font(SANS_FONT, 27)
    mono = font(MONO_FONT, 17)
    draw.multiline_text((58, 125), "A new\nmasterpiece.\nEvery day.", font=headline, fill=PAPER, spacing=-2)
    draw.multiline_text(
        (61, 374),
        "EaselWall rotates museum-quality\nImpressionist art automatically.",
        font=body,
        fill="#D9D0C0",
        spacing=8,
    )
    draw.rounded_rectangle((61, 500, 331, 546), radius=23, fill=(169, 117, 53, 55), outline=BRASS_LIGHT, width=1)
    draw.text((83, 513), "CHANGES AT MIDNIGHT", font=mono, fill=BRASS_LIGHT)

    display_mockup(canvas, LANDSCAPE_ONE, (506, 128, 1215, 586))
    draw.rounded_rectangle((629, 586, 1092, 610), radius=12, fill="#1A1A1A")
    draw.rounded_rectangle((704, 607, 1016, 621), radius=7, fill="#0F0F0F")

    icon = contain(ICON, (66, 66), "#28251F").convert("RGBA")
    rounded_paste(canvas, icon, (60, 684), 15)
    draw.text((144, 683), "EaselWall", font=font(SANS_FONT, 28), fill=PAPER)
    draw.text((144, 719), "Your desktop, curated.", font=font(SANS_FONT, 20), fill="#BDB4A5")
    return canvas


def screenshot_every_display() -> Image.Image:
    canvas = vertical_gradient("#F8F2E6", "#E8DDCB")
    add_grain(canvas)
    brand_label(canvas)
    draw = ImageDraw.Draw(canvas)

    headline = font(DISPLAY_FONT, 58)
    draw.text((58, 114), "The right art for every display.", font=headline, fill=INK)
    draw.text(
        (62, 184),
        "Landscape paintings for wide screens. Portrait works for tall ones.",
        font=font(SANS_FONT, 24),
        fill=INK_SOFT,
    )

    display_mockup(canvas, LANDSCAPE_TWO, (64, 292, 735, 670))
    draw.rounded_rectangle((205, 670, 594, 690), radius=10, fill="#151515")
    draw.rounded_rectangle((275, 688, 524, 700), radius=6, fill="#090909")

    display_mockup(canvas, PORTRAIT, (864, 245, 1157, 704), portrait=True)
    draw.rounded_rectangle((926, 704, 1095, 720), radius=8, fill="#151515")

    draw.line((792, 284, 792, 710), fill="#B79D76", width=2)
    draw.text((761, 454), "+", font=font(DISPLAY_FONT, 58), fill=BRASS)

    draw.rounded_rectangle((60, 735, 537, 773), radius=19, fill="#DFD0B9")
    draw.text((82, 745), "LANDSCAPE + PORTRAIT  ·  MULTI-DISPLAY READY", font=font(MONO_FONT, 15), fill=INK_SOFT)
    return canvas


def screenshot_catalog() -> Image.Image:
    canvas = vertical_gradient("#34291F", "#181713")
    add_grain(canvas)
    brand_label(canvas, dark=True)
    draw = ImageDraw.Draw(canvas)

    draw.text((57, 115), "53", font=font(DISPLAY_FONT, 170), fill=PAPER)
    draw.multiline_text((65, 300), "works in the\ncurated catalog.", font=font(DISPLAY_FONT, 53), fill=PAPER, spacing=2)
    draw.text((65, 438), "30 bundled for offline use.", font=font(SANS_FONT, 27), fill="#D7CBBB")
    draw.line((65, 500, 375, 500), fill=BRASS_LIGHT, width=2)
    draw.multiline_text(
        (65, 528),
        "Monet · Degas · Renoir\nVan Gogh · Cassatt · Cézanne · Seurat",
        font=font(SANS_FONT, 20),
        fill="#BAAE9F",
        spacing=8,
    )

    settings_capture(canvas, GALLERY_SUMMARY_UI, (456, 95, 1214, 350), radius=14)
    boxes = (
        (477, 390, 711, 613),
        (728, 390, 962, 613),
        (979, 390, 1213, 613),
    )
    for painting, box in zip(PAINTINGS, boxes):
        art_card(canvas, painting, box, mat_color="#EDE4D5", mat=11, radius=9)

    draw.rounded_rectangle((477, 650, 1214, 751), radius=13, fill="#2A251F", outline="#806A49", width=1)
    draw.text((511, 671), "NO REPEATS WITHIN EACH ORIENTATION CYCLE", font=font(MONO_FONT, 17), fill=BRASS_LIGHT)
    draw.text((511, 706), "A fresh exhibition without the feed.", font=font(SANS_FONT, 22), fill=PAPER)
    return canvas


def screenshot_mats() -> Image.Image:
    canvas = vertical_gradient("#F8F4EA", "#E7DECF")
    add_grain(canvas)
    brand_label(canvas)
    draw = ImageDraw.Draw(canvas)

    draw.text((58, 109), "Make the gallery yours.", font=font(DISPLAY_FONT, 62), fill=INK)
    draw.text((62, 184), "Choose your museum mat color and spacing—or turn it off.", font=font(SANS_FONT, 25), fill=INK_SOFT)

    cards = (
        (69, 318, 218, 521, "#F2EBDD", "CREAM"),
        (243, 318, 392, 521, "#53604D", "SAGE"),
        (417, 318, 566, 521, "#29323A", "SLATE"),
    )
    painting = ROOT / "website" / "assets" / "cliff-walk.jpg"
    for x1, y1, x2, y2, mat_color, label in cards:
        art_card(canvas, painting, (x1, y1, x2, y2), mat_color=mat_color, mat=34, radius=14)
        label_width = draw.textbbox((0, 0), label, font=font(MONO_FONT, 16))[2]
        draw.text(((x1 + x2 - label_width) // 2, 673), label, font=font(MONO_FONT, 16), fill=INK_SOFT)

    settings_capture(canvas, APPEARANCE_UI, (600, 248, 1212, 710), radius=18)
    draw.rounded_rectangle((125, 585, 511, 625), radius=20, fill="#D6C3A4")
    draw.text((155, 595), "MAT  ·  SHADOW  ·  SPACING", font=font(MONO_FONT, 16), fill=INK)
    return canvas


def screenshot_simple_by_design() -> Image.Image:
    canvas = vertical_gradient("#1D1915", "#2A241D")
    add_grain(canvas)
    brand_label(canvas, dark=True)
    draw = ImageDraw.Draw(canvas)

    with Image.open(ICON) as source:
        icon = source.convert("RGBA").resize((188, 188), Image.Resampling.LANCZOS)
    shadow(canvas, (65, 134, 253, 322), radius=38, blur=22, alpha=155)
    rounded_paste(canvas, icon, (65, 134), 38)

    draw.text((302, 132), "Simple by design.", font=font(DISPLAY_FONT, 72), fill=PAPER)
    draw.text((308, 224), "A quieter, more beautiful Mac every day.", font=font(SANS_FONT, 27), fill="#D4C9B9")

    benefits = (
        ("NO ACCOUNT", "Open it and enjoy."),
        ("AUTOMATIC DAILY ART", "Set it once. Tomorrow changes itself."),
        ("NO ADS OR TRACKING", "Your desktop stays private."),
    )
    for index, (title, description) in enumerate(benefits):
        y = 380 + index * 105
        draw.ellipse((72, y + 2, 96, y + 26), outline=BRASS_LIGHT, width=2)
        draw.line((79, y + 14, 85, y + 20), fill=BRASS_LIGHT, width=2)
        draw.line((85, y + 20, 92, y + 9), fill=BRASS_LIGHT, width=2)
        draw.text((119, y), title, font=font(MONO_FONT, 19), fill=BRASS_LIGHT)
        draw.text((119, y + 35), description, font=font(SANS_FONT, 23), fill=PAPER)

    settings_capture(canvas, SCHEDULE_UI, (570, 280, 1214, 765), radius=20)
    draw.rounded_rectangle((750, 697, 1207, 753), radius=28, fill=PAPER)
    cta = "GET EASELWALL ON THE MAC APP STORE"
    text_bounds = draw.textbbox((0, 0), cta, font=font(MONO_FONT, 15))
    draw.text(((750 + 1207 - (text_bounds[2] - text_bounds[0])) // 2, 715), cta, font=font(MONO_FONT, 15), fill=INK)
    return canvas


def save_and_validate(image: Image.Image, name: str) -> tuple[Path, str]:
    if image.size != (WIDTH, HEIGHT):
        raise RuntimeError(f"Unexpected canvas size for {name}: {image.size}")
    output = OUTPUT_DIR / name
    image.convert("RGB").save(output, format="PNG", optimize=True, compress_level=9)
    with Image.open(output) as rendered:
        rendered.load()
        if rendered.size != (WIDTH, HEIGHT) or rendered.mode != "RGB" or rendered.format != "PNG":
            raise RuntimeError(
                f"Invalid output {output}: format={rendered.format}, mode={rendered.mode}, size={rendered.size}"
            )
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return output, digest


def archive_stale_uploads() -> list[tuple[Path, Path]]:
    """Move unrecognized PNGs out of the upload directory without deleting them."""
    if not OUTPUT_DIR.is_dir():
        return []

    stale = sorted(
        path
        for path in OUTPUT_DIR.glob("*.png")
        if path.name not in OUTPUTS
    )
    if not stale:
        return []

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination_dir = ARCHIVE_DIR / timestamp
    suffix = 2
    while destination_dir.exists():
        destination_dir = ARCHIVE_DIR / f"{timestamp}-{suffix}"
        suffix += 1
    destination_dir.mkdir(parents=True, exist_ok=False)

    archived: list[tuple[Path, Path]] = []
    for source in stale:
        destination = destination_dir / source.name
        shutil.move(str(source), str(destination))
        archived.append((source, destination))
    return archived


def write_manifest(rendered: list[tuple[Path, str]]) -> None:
    payload = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "uploadFiles": [
            {
                "filename": path.name,
                "width": WIDTH,
                "height": HEIGHT,
                "colorMode": "RGB",
                "format": "PNG",
                "sha256": digest,
            }
            for path, digest in rendered
        ],
    }
    MANIFEST.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    require_assets()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for source, destination in archive_stale_uploads():
        print(
            "Archived stale upload candidate "
            f"{source.relative_to(ROOT)} -> {destination.relative_to(ROOT)}"
        )
    renderers = (
        screenshot_daily_masterpiece,
        screenshot_every_display,
        screenshot_mats,
        screenshot_catalog,
        screenshot_simple_by_design,
    )
    rendered: list[tuple[Path, str]] = []
    for name, renderer in zip(OUTPUTS, renderers):
        output, digest = save_and_validate(renderer(), name)
        rendered.append((output, digest))
        print(f"Rendered {output.relative_to(ROOT)}  {WIDTH}x{HEIGHT} RGB PNG  sha256={digest[:12]}")
    write_manifest(rendered)
    print(f"Upload manifest: {MANIFEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
