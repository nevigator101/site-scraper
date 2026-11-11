"""Full-page screenshots with a height cap.

Blog archives with endless lists can be 100,000+ px tall. A full screenshot of one is a ~500 MB
image in the browser, the Playwright driver and Python at once, and Crawl4AI's own method also
stitches it from tiles, so a couple of such pages ran the scraper out of memory. The top
MAX_HEIGHT px is plenty as a design reference; shorter pages are captured whole.
"""

from __future__ import annotations

MAX_HEIGHT = 15_000  # CSS px


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
