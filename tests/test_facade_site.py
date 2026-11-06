import io
import json

from PIL import Image

from terragen.facade import facade_image, facade_png
from terragen.site import build_static


def test_facade_corners_are_wall():
    im = facade_image()
    w, h = im.size
    for xy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        assert im.getpixel(xy) == (255, 255, 255)


def test_facade_center_is_glass():
    im = facade_image()
    px = im.getpixel((im.size[0] // 2, im.size[1] // 2))
    assert sum(px) < 400  # geam închis la culoare


def test_facade_png_is_valid():
    img = Image.open(io.BytesIO(facade_png()))
    assert img.format == "PNG" and img.size == facade_image().size


def test_build_static_copies_viewer(built, tmp_path):
    site_dir, manifest = built
    dest = build_static(site_dir, tmp_path / "dist")
    assert (dest / "index.html").exists()
    assert (dest / "vendor" / "three.module.js").exists()
    assert (dest / "manifest.json").exists()
    assert (dest / "glb" / "buildings.glb").exists()
    assert (tmp_path / "dist" / "index.html").exists()  # index cu lista de orașe
    mf = json.loads((dest / "manifest.json").read_text())
    assert mf["layers"] == manifest["layers"]
