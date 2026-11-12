"""Full-page screenshots with a height cap, saved as WebP.

Blog archives with endless lists can be 100,000+ px tall. A full screenshot of one is a ~500 MB
image in the browser, the Playwright driver and Python at once, and Crawl4AI's own method also
stitches it from tiles, so a couple of such pages ran the scraper out of memory. The top
MAX_HEIGHT px is plenty as a design reference; shorter pages are captured whole.

Playwright only captures PNG or JPEG, so each screenshot is taken as PNG and converted to WebP
(quality 80): about a sixth of the size, with text still sharp. Kits stay small enough to keep.
To get a PNG back: `Image.open("x.webp").save("x.png")` with Pillow.
"""

from __future__ import annotations

import asyncio
import io

from PIL import Image

MAX_HEIGHT = 15_000  # CSS px
WEBP_QUALITY = 80
WEBP_MAX_SIDE = 16_383  # px; the WebP format cannot store a larger image


async def capped_screenshot(page, max_height: int = MAX_HEIGHT, **kwargs) -> bytes:
    size = await page.evaluate(
        "() => ({w: document.documentElement.clientWidth || window.innerWidth,"
        " h: Math.max(document.documentElement.scrollHeight, document.body"
        " ? document.body.scrollHeight : 0)})"
    )
    width = max(1, int(size.get("w") or 1))
    height = max(1, min(int(size.get("h") or 1), max_height))
    return await page.screenshot(
        full_page=True, clip={"x": 0, "y": 0, "width": width, "height": height}, **kwargs
    )


def to_webp(image: bytes) -> bytes:
    """Convert a PNG (or any image Pillow reads) to WebP, cropping from the top-left to the
    format's 16,383 px limit if it is bigger. Pillow's decompression-bomb limit still applies."""
    with Image.open(io.BytesIO(image)) as im:
        rgb = im.convert("RGB")
    if rgb.width > WEBP_MAX_SIDE or rgb.height > WEBP_MAX_SIDE:
        rgb = rgb.crop((0, 0, min(rgb.width, WEBP_MAX_SIDE), min(rgb.height, WEBP_MAX_SIDE)))
    out = io.BytesIO()
    rgb.save(out, "WEBP", quality=WEBP_QUALITY, method=4)
    return out.getvalue()


async def webp_screenshot(page, max_height: int = MAX_HEIGHT, **kwargs) -> bytes:
    """A capped full-page screenshot as WebP. The conversion runs in a thread so a tall page
    does not stall the other crawl slots."""
    png = await capped_screenshot(page, max_height, type="png", **kwargs)
    return await asyncio.to_thread(to_webp, png)
