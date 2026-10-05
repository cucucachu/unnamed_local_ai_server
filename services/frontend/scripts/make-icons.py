#!/usr/bin/env python3
"""Render the Home AI app icons into assets/images (needs Pillow).

    python3 scripts/make-icons.py

A house outline with an AI sparkle inside, on the app's dark theme
(lib/theme.ts). Everything is drawn at 4x and downsampled for smooth
edges. Outputs: icon.png (iOS/web, full bleed, opaque), the Android
adaptive foreground/background/monochrome layers, splash-icon.png and
favicon.png.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "assets" / "images"
SS = 4

BG_TOP = (28, 40, 66)
BG_BOTTOM = (14, 17, 22)
HOUSE = (230, 237, 243)
SPARK = (79, 140, 255)

STROKE = 0.075
ROOF = [(0.17, 0.52), (0.50, 0.21), (0.83, 0.52)]
BODY = [(0.28, 0.45), (0.28, 0.80), (0.72, 0.80), (0.72, 0.45)]
SPARK_CENTER = (0.50, 0.605)
SPARK_RADIUS = 0.15


def _gradient(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / (size - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM))
        draw.line([(0, y), (size, y)], fill=(*color, 255))
    return img


def _polyline(draw: ImageDraw.ImageDraw, points, box, width, fill) -> None:
    x0, y0, side = box
    pts = [(x0 + px * side, y0 + py * side) for px, py in points]
    draw.line(pts, fill=fill, width=round(width), joint="curve")
    r = width / 2
    for x, y in (pts[0], pts[-1]):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def _sparkle(box, n: int = 240):
    x0, y0, side = box
    cx, cy = x0 + SPARK_CENTER[0] * side, y0 + SPARK_CENTER[1] * side
    radius = SPARK_RADIUS * side
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        c, s = math.cos(t), math.sin(t)
        pts.append((cx + radius * math.copysign(abs(c) ** 3, c), cy + radius * math.copysign(abs(s) ** 3, s)))
    return pts


def glyph(size: int, scale: float, *, mono: bool = False, glow: bool = True) -> Image.Image:
    """The house + sparkle on transparent, filling `scale` of a `size` square."""
    big = size * SS
    side = big * scale
    box = ((big - side) / 2, (big - side) / 2, side)
    house_fill = (255, 255, 255, 255) if mono else (*HOUSE, 255)
    spark_fill = (255, 255, 255, 255) if mono else (*SPARK, 255)

    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    _polyline(draw, ROOF, box, STROKE * side, house_fill)
    _polyline(draw, BODY, box, STROKE * side, house_fill)

    spark = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(spark).polygon(_sparkle(box), fill=spark_fill)
    if glow and not mono:
        halo = spark.filter(ImageFilter.GaussianBlur(side * 0.04))
        layer = Image.alpha_composite(layer, halo)
    layer = Image.alpha_composite(layer, spark)
    return layer.resize((size, size), Image.LANCZOS)


def full_icon(size: int, scale: float = 0.78) -> Image.Image:
    img = _gradient(size)
    return Image.alpha_composite(img, glyph(size, scale)).convert("RGB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    full_icon(1024).save(OUT / "icon.png")
    full_icon(48, 0.86).save(OUT / "favicon.png")
    # Adaptive icons: 108dp canvas, launchers show roughly the middle 66dp,
    # so the glyph stays inside ~60% of the layer.
    glyph(512, 0.58).save(OUT / "android-icon-foreground.png")
    _gradient(512).convert("RGB").save(OUT / "android-icon-background.png")
    glyph(432, 0.58, mono=True).save(OUT / "android-icon-monochrome.png")
    glyph(512, 1.0).save(OUT / "splash-icon.png")


if __name__ == "__main__":
    main()
