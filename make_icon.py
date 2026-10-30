#!/usr/bin/env python3
"""Gera icon.png a partir de icon-source.jpg: fundo branco vira transparente, a fatia fica opaca por inteiro.
A fatia é convexa, então a máscara é o fecho convexo dos pixels que não são fundo."""
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def hull(points):
    """Fecho convexo (cadeia monótona de Andrew)."""
    pts = sorted(set(points))
    cross = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def make(src, dst, size=512, ss=4):
    im = Image.open(src).convert("RGB")
    a = np.asarray(im).astype(int)
    fg = (a.min(-1) < 225) | (a.max(-1) - a.min(-1) > 40)  # não-branco
    ys, xs = np.nonzero(fg)
    poly = hull(list(zip(xs.tolist(), ys.tolist())))
    mask = Image.new("L", (im.width * ss, im.height * ss), 0)
    ImageDraw.Draw(mask).polygon([(x * ss + ss / 2, y * ss + ss / 2) for x, y in poly], fill=255)
    mask = mask.filter(ImageFilter.MinFilter(2 * ss - 1))  # encolhe ~1 px: some o halo branco do JPEG
    mask = mask.resize(im.size, Image.LANCZOS)
    out = im.convert("RGBA")
    out.putalpha(mask)
    out = out.crop(out.getbbox())
    side = max(out.size)
    pad = round(side * 0.04)
    canvas = Image.new("RGBA", (side + 2 * pad,) * 2, (0, 0, 0, 0))
    canvas.paste(out, ((canvas.width - out.width) // 2, (canvas.height - out.height) // 2))
    canvas.resize((size, size), Image.LANCZOS).save(dst)


if __name__ == "__main__":
    make(*(sys.argv[1:3] or ("icon-source.jpg", "icon.png")))
