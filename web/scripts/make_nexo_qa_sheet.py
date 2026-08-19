#!/usr/bin/env python3
"""Compose brand-variant evidence on intentional light and dark backgrounds."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


INK = "#10262C"
CANVAS = "#F6F8F5"
WHITE = "#FFFFFF"


def font(size: int) -> ImageFont.ImageFont:
    candidates = [
        Path(r"C:\Windows\Fonts\segoeuib.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def place(canvas: Image.Image, source: Path, box: tuple[int, int, int, int]) -> None:
    image = Image.open(source).convert("RGBA")
    fitted = ImageOps.contain(image, (box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS)
    x = box[0] + (box[2] - box[0] - fitted.width) // 2
    y = box[1] + (box[3] - box[1] - fitted.height) // 2
    canvas.alpha_composite(fitted, (x, y))


def build(root: Path) -> None:
    root = root.resolve()
    out = root / "validation" / "variant-contact-sheet.png"
    sheet = Image.new("RGBA", (1400, 980), CANVAS)
    draw = ImageDraw.Draw(sheet)
    title_font = font(38)
    label_font = font(22)
    draw.text((52, 34), "Nexo · variantes de produção", fill=INK, font=title_font)

    cards = [
        ("Colorida · fundo claro", "png/low/nexo-logo-horizontal-color-600.png", WHITE),
        ("Preta · fundo claro", "png/low/nexo-logo-horizontal-black-600.png", WHITE),
        ("Totalmente branca · fundo escuro", "png/low/nexo-logo-horizontal-white-600.png", INK),
        ("Reversa · fundo escuro", "png/low/nexo-logo-horizontal-reversed-600.png", INK),
    ]
    for index, (label, source, background) in enumerate(cards):
        column = index % 2
        row = index // 2
        x = 52 + column * 658
        y = 112 + row * 330
        draw.rounded_rectangle((x, y, x + 620, y + 282), radius=22, fill=background, outline="#D8E2DF", width=2)
        label_color = WHITE if background == INK else INK
        draw.text((x + 28, y + 24), label, fill=label_color, font=label_font)
        place(sheet, root / source, (x + 28, y + 72, x + 592, y + 246))

    symbols = [
        ("16", "favicon/favicon-16.png"),
        ("32", "favicon/favicon-32.png"),
        ("48", "favicon/favicon-48.png"),
        ("64", "favicon/favicon-64.png"),
        ("128", "favicon/favicon-128.png"),
        ("256", "favicon/favicon-256.png"),
    ]
    draw.text((52, 790), "Escala do símbolo", fill=INK, font=label_font)
    for index, (label, source) in enumerate(symbols):
        x = 52 + index * 210
        draw.rounded_rectangle((x, 832, x + 164, 944), radius=16, fill=WHITE, outline="#D8E2DF", width=2)
        image = Image.open(root / source).convert("RGBA")
        preview = ImageOps.contain(image, (72, 72), Image.Resampling.NEAREST)
        sheet.alpha_composite(preview, (x + 18, 852))
        draw.text((x + 106, 872), f"{label}px", fill=INK, font=label_font)

    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(out, quality=95)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand-root", required=True, type=Path)
    args = parser.parse_args()
    build(args.brand_root)


if __name__ == "__main__":
    main()
