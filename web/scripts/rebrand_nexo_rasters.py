#!/usr/bin/env python3
"""Patch only visible legacy-brand regions in landing-page raster assets."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from PIL import Image, ImageDraw, ImageFont, ImageOps


def trim_alpha(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    bbox = rgba.getchannel("A").getbbox()
    if bbox is None:
        raise ValueError("Logo export has no visible alpha content")
    return rgba.crop(bbox)


def paste_logo(canvas: Image.Image, logo: Image.Image, box: tuple[int, int, int, int]) -> None:
    fitted = ImageOps.contain(logo, (box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS)
    x = box[0] + (box[2] - box[0] - fitted.width) // 2
    y = box[1] + (box[3] - box[1] - fitted.height) // 2
    canvas.alpha_composite(fitted, (x, y))


def cover(canvas: Image.Image, rect: tuple[int, int, int, int], sample: tuple[int, int]) -> None:
    color = canvas.convert("RGB").getpixel(sample)
    ImageDraw.Draw(canvas).rectangle(rect, fill=color)


def save_webp(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, format="WEBP", lossless=True, method=6, exact=True)


def build(public: Path, brand: Path, staging: Path) -> None:
    source_dir = staging / "integration-source"
    output_dir = staging / "integration"
    source_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    sources = {
        "og.png": public / "og.png",
        "procurement-command-center-v3.webp": public / "assets/screens/procurement-command-center-v3.webp",
        "proposal-comparison-v2.webp": public / "assets/screens/proposal-comparison-v2.webp",
        "supplier-mobile-flow.webp": public / "assets/screens/supplier-mobile-flow.webp",
    }
    for name, source in sources.items():
        retained = source_dir / name
        if not retained.exists():
            shutil.copy2(source, retained)

    color_logo = trim_alpha(Image.open(brand / "png/high/nexo-logo-horizontal-color-2400.png"))
    white_logo = trim_alpha(Image.open(brand / "png/high/nexo-logo-horizontal-white-2400.png"))

    og = Image.open(source_dir / "og.png").convert("RGBA")
    cover(og, (54, 62, 720, 350), (860, 92))
    paste_logo(og, color_logo, (70, 130, 650, 265))
    og.convert("RGB").save(output_dir / "og.png", format="PNG", optimize=True)

    command = Image.open(source_dir / "procurement-command-center-v3.webp").convert("RGBA")
    cover(command, (0, 0, 278, 82), (310, 30))
    paste_logo(command, color_logo, (16, 14, 238, 68))
    save_webp(command, output_dir / "procurement-command-center-v3.webp")

    comparison = Image.open(source_dir / "proposal-comparison-v2.webp").convert("RGBA")
    cover(comparison, (0, 0, 360, 74), (410, 28))
    paste_logo(comparison, color_logo, (14, 10, 244, 64))
    cover(comparison, (36, 918, 300, 1002), (330, 940))
    paste_logo(comparison, color_logo, (42, 930, 204, 974))
    role_font = ImageFont.truetype(str(staging / "reference/UbuntuSans-650.ttf"), 16)
    ImageDraw.Draw(comparison).text((54, 976), "Comprador", fill="#526467", font=role_font)
    save_webp(comparison, output_dir / "proposal-comparison-v2.webp")

    supplier = Image.open(source_dir / "supplier-mobile-flow.webp").convert("RGBA")
    cover(supplier, (306, 0, 674, 68), (580, 34))
    paste_logo(supplier, white_logo, (328, 15, 500, 52))
    cover(supplier, (852, 112, 1220, 174), (1120, 140))
    paste_logo(supplier, white_logo, (876, 123, 1038, 162))
    save_webp(supplier, output_dir / "supplier-mobile-flow.webp")

    sheet = Image.new("RGB", (1600, 1320), "#F6F8F5")
    draw = ImageDraw.Draw(sheet)
    label_font = ImageFont.truetype(str(staging / "reference/UbuntuSans-650.ttf"), 26)
    rows = [
        ("OG social", source_dir / "og.png", output_dir / "og.png"),
        ("Cockpit", source_dir / "procurement-command-center-v3.webp", output_dir / "procurement-command-center-v3.webp"),
        ("Comparação", source_dir / "proposal-comparison-v2.webp", output_dir / "proposal-comparison-v2.webp"),
        ("Fornecedor", source_dir / "supplier-mobile-flow.webp", output_dir / "supplier-mobile-flow.webp"),
    ]
    for index, (label, before_path, after_path) in enumerate(rows):
        y = 24 + index * 320
        draw.text((32, y), f"{label} · antes", fill="#10262C", font=label_font)
        draw.text((816, y), f"{label} · Nexo", fill="#10262C", font=label_font)
        before = ImageOps.contain(Image.open(before_path).convert("RGB"), (752, 260), Image.Resampling.LANCZOS)
        after = ImageOps.contain(Image.open(after_path).convert("RGB"), (752, 260), Image.Resampling.LANCZOS)
        sheet.paste(before, (32, y + 44))
        sheet.paste(after, (816, y + 44))
    sheet.save(staging / "validation/integration-before-after.jpg", quality=92, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public", required=True, type=Path)
    parser.add_argument("--brand", required=True, type=Path)
    parser.add_argument("--staging", required=True, type=Path)
    args = parser.parse_args()
    build(args.public.resolve(), args.brand.resolve(), args.staging.resolve())


if __name__ == "__main__":
    main()
