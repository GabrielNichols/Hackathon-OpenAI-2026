#!/usr/bin/env python3
"""Build the deterministic Nexo SVG masters and brand-kit configuration.

The ImageGen PNG is retained as visual evidence only. Symbol geometry below is
authored explicitly on a 64 x 64 grid; no bitmap tracing is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image


INK = "#10262C"
TEAL = "#0E7C72"
LIME = "#C8F45D"
CANVAS = "#F6F8F5"
BLACK = "#000000"
WHITE = "#FFFFFF"

# Three intentional foreground components, zero holes:
# evidence route in -> human checkpoint -> evidence route out.
LEFT_ROUTE = (
    "M10 10H20L28.5 22L22.5 28.5L19 24V50"
    "Q19 54 15 54H10Q6 54 6 50V14Q6 10 10 10Z"
)
RIGHT_ROUTE = (
    "M41.5 36L45 40V14Q45 10 49 10H54"
    "Q58 10 58 14V50Q58 54 54 54H44L36 42Z"
)
NODE = '<rect x="27" y="28" width="10" height="10" rx="2"/>'

REFERENCE_CROP = (280, 290, 644, 597)

IMAGEGEN_PROMPT = """Use case: logo-brand
Asset type: selected identity reference for deterministic vector reconstruction
Primary request: Create one single, centered horizontal logo direction for an enterprise procurement product named Nexo. The symbol is a bold geometric capital N constructed as a continuous evidence route: two upright rails connected by one decisive diagonal path, with a small square checkpoint embedded at the exact center. The checkpoint represents explicit human approval; the uninterrupted route represents traceable evidence from demand to supplier acceptance. Make the mark distinctive and balanced, but simple enough to reconstruct manually on a 64 by 64 grid and remain recognizable at 16 px.
Scene/backdrop: flat warm mineral white background, generous clear space, no presentation mockup and no surrounding objects
Subject: isolated symbol on the left and the exact wordmark on the right
Style/medium: precise flat 2D vector-like brand reference; sober enterprise design; humanist geometric sans wordmark similar in spirit to Manrope, not futuristic
Composition/framing: logo centered on a landscape canvas; one concept only; symbol and wordmark aligned optically on one baseline
Color palette: Ink #10262C for the main geometry and wordmark, Signal Teal #0E7C72 only as a restrained secondary detail if needed, Signal Lime #C8F45D only for the central square checkpoint
Text (verbatim): "Nexo"
Constraints: spell the wordmark exactly N-e-x-o with capital N and lowercase exo; clean straight geometry; consistent weights; flat colors; visible clear space; no additional tagline or letters; no raster texture
Avoid: generic AI symbols, brains, stars, sparkles, neural networks, node clouds, chain-link stock symbols, infinity symbols, robots, circuit boards, shields, handshakes, 3D, bevels, shadows, glow, gradients, decorative transparency, futuristic typography, mockups, multiple logo options, watermarks
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def svg_document(title: str, view_box: str, body: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{view_box}" role="img" aria-labelledby="title">\n'
        f'  <title id="title">{title}</title>\n'
        f'{body}\n'
        '</svg>\n'
    )


def symbol_body(mode: str, transform: str | None = None) -> str:
    route_fill = {
        "color": INK,
        "black": BLACK,
        "white": WHITE,
        "reverse": WHITE,
    }[mode]
    node_fill = {
        "color": LIME,
        "black": BLACK,
        "white": WHITE,
        "reverse": LIME,
    }[mode]
    open_group = f'  <g transform="{transform}">\n' if transform else '  <g>\n'
    return (
        open_group
        + f'    <path d="{LEFT_ROUTE}" fill="{route_fill}"/>\n'
        + f'    <path d="{RIGHT_ROUTE}" fill="{route_fill}"/>\n'
        + f'    {NODE[:-2]} fill="{node_fill}"/>\n'
        + '  </g>'
    )


def outlined_wordmark_paths(wordmark_svg: Path) -> list[str]:
    root = ET.parse(wordmark_svg).getroot()
    paths: list[str] = []
    for node in root.iter():
        if node.tag.endswith("path"):
            data = node.attrib.get("d")
            if data:
                paths.append(data)
    if len(paths) != 4:
        raise ValueError(f"Expected four outlined glyph paths for Nexo, found {len(paths)}")
    return paths


def wordmark_body(paths: list[str], mode: str, transform: str) -> str:
    fill = WHITE if mode in {"white", "reverse"} else BLACK if mode == "black" else INK
    glyphs = "\n".join(f'    <path d="{data}" fill="{fill}"/>' for data in paths)
    return f'  <g transform="{transform}" aria-label="Nexo">\n{glyphs}\n  </g>'


def write_svg(path: Path, title: str, view_box: str, body: str) -> None:
    path.write_text(svg_document(title, view_box, body), encoding="utf-8", newline="\n")


def export(source: str, output: str, width: int, height: int, background: str = "transparent") -> dict:
    return {
        "source": source,
        "output": output,
        "width": width,
        "height": height,
        "background": background,
        "required": True,
    }


def build(root: Path) -> None:
    root = root.resolve()
    source = root / "reference" / "source.png"
    wordmark_source = root / "svg" / "nexo-wordmark.svg"
    if not source.is_file() or not wordmark_source.is_file():
        raise FileNotFoundError("Scaffolded reference and outlined wordmark are required")

    svg_dir = root / "svg"
    svg_dir.mkdir(parents=True, exist_ok=True)
    paths = outlined_wordmark_paths(wordmark_source)

    symbol_variants = {
        "nexo-symbol.svg": ("Símbolo Nexo colorido", "color"),
        "nexo-symbol-small.svg": ("Símbolo Nexo otimizado para tamanhos pequenos", "color"),
        "nexo-symbol-black.svg": ("Símbolo Nexo totalmente preto", "black"),
        "nexo-symbol-white.svg": ("Símbolo Nexo totalmente branco", "white"),
        "nexo-symbol-reversed.svg": ("Símbolo Nexo reverso para fundo escuro", "reverse"),
    }
    for filename, (title, mode) in symbol_variants.items():
        write_svg(svg_dir / filename, title, "0 0 64 64", symbol_body(mode))

    horizontal_variants = {
        "nexo-logo-horizontal.svg": ("Logo horizontal Nexo colorido", "color"),
        "nexo-logo-horizontal-black.svg": ("Logo horizontal Nexo totalmente preto", "black"),
        "nexo-logo-horizontal-white.svg": ("Logo horizontal Nexo totalmente branco", "white"),
        "nexo-logo-horizontal-reversed.svg": ("Logo horizontal Nexo reverso para fundo escuro", "reverse"),
    }
    for filename, (title, mode) in horizontal_variants.items():
        body = symbol_body(mode) + "\n" + wordmark_body(paths, mode, "translate(70 12) scale(.72)")
        write_svg(svg_dir / filename, title, "0 0 200 64", body)

    vertical_variants = {
        "nexo-signature-vertical.svg": ("Assinatura vertical Nexo colorida", "color"),
        "nexo-signature-vertical-black.svg": ("Assinatura vertical Nexo totalmente preta", "black"),
        "nexo-signature-vertical-white.svg": ("Assinatura vertical Nexo totalmente branca", "white"),
        "nexo-signature-vertical-reversed.svg": ("Assinatura vertical Nexo reversa para fundo escuro", "reverse"),
    }
    for filename, (title, mode) in vertical_variants.items():
        body = symbol_body(mode, "translate(58 4)") + "\n" + wordmark_body(paths, mode, "translate(16 88) scale(.9)")
        write_svg(svg_dir / filename, title, "0 0 180 150", body)

    # The selected comparison is the exact symbol region from the retained PNG.
    with Image.open(source) as image:
        image.crop(REFERENCE_CROP).save(root / "reference" / "selected-mark.png")

    (root / "reference" / "imagegen-prompt.md").write_text(
        "# Nexo ImageGen reference prompt\n\n" + IMAGEGEN_PROMPT,
        encoding="utf-8",
        newline="\n",
    )
    (root / "brand-tokens.css").write_text(
        ":root {\n"
        f"  --nexo-ink: {INK};\n"
        f"  --nexo-teal: {TEAL};\n"
        f"  --nexo-signal: {LIME};\n"
        f"  --nexo-canvas: {CANVAS};\n"
        "}\n",
        encoding="utf-8",
        newline="\n",
    )

    masters = {
        "symbol": "svg/nexo-symbol.svg",
        "symbolSmall": "svg/nexo-symbol-small.svg",
        "symbolBlack": "svg/nexo-symbol-black.svg",
        "symbolWhite": "svg/nexo-symbol-white.svg",
        "symbolReversed": "svg/nexo-symbol-reversed.svg",
        "horizontal": "svg/nexo-logo-horizontal.svg",
        "horizontalMonochrome": "svg/nexo-logo-horizontal-black.svg",
        "horizontalWhite": "svg/nexo-logo-horizontal-white.svg",
        "horizontalReversed": "svg/nexo-logo-horizontal-reversed.svg",
        "vertical": "svg/nexo-signature-vertical.svg",
        "verticalMonochrome": "svg/nexo-signature-vertical-black.svg",
        "verticalWhite": "svg/nexo-signature-vertical-white.svg",
        "verticalReversed": "svg/nexo-signature-vertical-reversed.svg",
    }

    exports: list[dict] = []
    symbol_modes = {
        "color": (masters["symbol"], "transparent"),
        "black": (masters["symbolBlack"], "transparent"),
        "white": (masters["symbolWhite"], "transparent"),
        "reversed": (masters["symbolReversed"], INK),
    }
    for name, (src, bg) in symbol_modes.items():
        exports.append(export(src, f"png/high/nexo-symbol-{name}-2048.png", 2048, 2048, bg))
        exports.append(export(src, f"png/low/nexo-symbol-{name}-256.png", 256, 256, bg))

    horizontal_modes = {
        "color": (masters["horizontal"], "transparent"),
        "black": (masters["horizontalMonochrome"], "transparent"),
        "white": (masters["horizontalWhite"], "transparent"),
        "reversed": (masters["horizontalReversed"], INK),
    }
    for name, (src, bg) in horizontal_modes.items():
        exports.append(export(src, f"png/high/nexo-logo-horizontal-{name}-2400.png", 2400, 768, bg))
        exports.append(export(src, f"png/low/nexo-logo-horizontal-{name}-600.png", 600, 192, bg))

    vertical_modes = {
        "color": (masters["vertical"], "transparent"),
        "black": (masters["verticalMonochrome"], "transparent"),
        "white": (masters["verticalWhite"], "transparent"),
        "reversed": (masters["verticalReversed"], INK),
    }
    for name, (src, bg) in vertical_modes.items():
        exports.append(export(src, f"png/high/nexo-signature-vertical-{name}-1600.png", 1600, 1600, bg))
        exports.append(export(src, f"png/low/nexo-signature-vertical-{name}-512.png", 512, 512, bg))

    config = {
        "schemaVersion": 1,
        "brand": {"name": "Nexo", "slug": "nexo", "wordmark": "Nexo"},
        "concept": "Rota de evidência interrompida por um checkpoint humano obrigatório.",
        "palette": {
            "primary": INK,
            "secondary": TEAL,
            "signal": LIME,
            "surface": CANVAS,
            "allowedColors": [INK, TEAL, LIME, CANVAS, BLACK, WHITE],
        },
        "references": [{
            "path": "reference/source.png",
            "sha256": sha256(source),
            "role": "imagegen-identity-reference",
            "width": 1672,
            "height": 941,
            "selectedCrop": list(REFERENCE_CROP),
            "selectedPath": "reference/selected-mark.png",
        }],
        "selectedReference": "reference/selected-mark.png",
        "symbolRoi": "reference/selected-mark.png",
        "masters": masters,
        "requiredMasters": list(masters),
        "exports": exports,
        "favicon": {
            "source": masters["symbolSmall"],
            "fallbackSource": masters["symbol"],
            "sizes": [16, 32, 48, 64, 128, 256],
            "directory": "favicon",
            "ico": "favicon/favicon.ico",
        },
        "validation": {
            "candidate": masters["symbol"],
            "reference": "reference/selected-mark.png",
            "scaleAxis": "height",
            "anchor": "left-center",
            "thresholds": {
                "minimumIou": 0.68,
                "maximumBboxDeltaPercent": 8.0,
                "maximumCentroidDeltaPercent": 4.0,
            },
            "report": "validation/fidelity-report.json",
            "humanReview": "validation/human-review.json",
        },
        "topologyInvariants": {
            "foregroundComponents": 3,
            "holes": 0,
            "entries": 1,
            "exits": 1,
            "requiredCheckpoint": 1,
            "crossings": 0,
        },
        "rules": {
            "noEmbeddedRaster": True,
            "noLiveText": True,
            "noExternalReferences": True,
            "noGradients": True,
            "noFilters": True,
            "noTransparencyEffects": True,
        },
    }
    (root / "brand.config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand-root", required=True, type=Path)
    args = parser.parse_args()
    build(args.brand_root)


if __name__ == "__main__":
    main()
