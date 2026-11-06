"""Textura procedurală de fațadă — ferestre fără shader și fără assets externe.

Generăm o singură celulă de textură (128×128) care acoperă exact un modul de fațadă
(`geom.MODULE_U` × `geom.FLOOR_H` = 3,2 m × 3,1 m). UV-urile din `geom.extrude()` sunt deja
`u = distanță/modul`, `v = etaj`, iar sampler-ul din GLB e REPEAT — deci textura se repetă
singură pe toată fațada, la orice înălțime, fără să calculăm nimic per clădire.

Textura e **albă pe perete** și **închisă pe geam**, pentru că în glTF (și în three.js/Unity/
Godot/Blender) `baseColorTexture` se înmulțește cu `COLOR_0` (culoarea per vertex). Adică:
culoarea per clădire vine din vertex colors, iar geamurile din textură — un material pentru tot orașul.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

TILE = 128

# geam: rece, cu o urmă de cer în partea de sus (reflexie falsă)
GLASS_TOP = (108, 126, 148)
GLASS_BOT = (54, 66, 82)
FRAME = (196, 196, 196)


def facade_image(tile: int = TILE) -> Image.Image:
    """O celulă de fațadă: perete alb + geam cu ramă. Colțurile rămân perete curat,
    ca orice UV implicit (0,0) să nu nimerească în geam."""
    img = Image.new("RGB", (tile, tile), (255, 255, 255))
    dr = ImageDraw.Draw(img)

    # fereastra: 40% lățime, 50% înălțime, centrată (puțin peste centru)
    w = int(tile * 0.40)
    h = int(tile * 0.50)
    x0 = (tile - w) // 2
    y0 = int(tile * 0.22)
    # ramă
    f = max(1, tile // 40)
    dr.rectangle([x0 - f, y0 - f, x0 + w + f, y0 + h + f], fill=FRAME)
    # geam cu gradient vertical (cer sus, cameră jos)
    for i in range(h):
        t = i / max(1, h - 1)
        col = tuple(int(GLASS_TOP[k] * (1 - t) + GLASS_BOT[k] * t) for k in range(3))
        dr.line([(x0, y0 + i), (x0 + w - 1, y0 + i)], fill=col)
    # montant vertical (două canate)
    if w > 8:
        mx = x0 + w // 2
        dr.line([(mx, y0), (mx, y0 + h)], fill=FRAME)
    # glaf
    dr.line([(x0 - f, y0 + h + f), (x0 + w + f, y0 + h + f)], fill=(178, 178, 180))
    return img


def facade_png(tile: int = TILE) -> bytes:
    buf = io.BytesIO()
    facade_image(tile).save(buf, format="PNG", optimize=True)
    return buf.getvalue()
