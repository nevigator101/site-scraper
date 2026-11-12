import io

from PIL import Image

from site_scraper.shots import WEBP_MAX_SIDE, to_webp


def _png(size, mode="RGB") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size).save(out, "PNG")
    return out.getvalue()


def _open(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def test_png_becomes_a_smaller_webp_of_the_same_size():
    png = _png((1280, 2000))
    webp = to_webp(png)
    im = _open(webp)
    assert (im.format, im.size) == ("WEBP", (1280, 2000))
    assert len(webp) < len(png)


def test_transparent_png_is_flattened():
    assert _open(to_webp(_png((50, 50), "RGBA"))).format == "WEBP"


def test_taller_than_the_webp_limit_is_cropped_from_the_top():
    im = _open(to_webp(_png((20, WEBP_MAX_SIDE + 500))))
    assert im.size == (20, WEBP_MAX_SIDE)
