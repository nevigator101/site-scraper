"""Fetch the site's own stylesheets and read the images they use.

Page builders (Elementor, Divi, WPBakery) and many themes put hero and section backgrounds in
CSS files, e.g. /wp-content/uploads/elementor/css/post-70.css, not in the HTML. Those images are
invisible to an HTML-only scan, so the stylesheets each page links are fetched (following
@import) and their url() images are added to that page's images.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

import httpx

from site_scraper.assets import _fetch
from site_scraper.extract import css_imports, css_urls
from site_scraper.urls import same_site

log = logging.getLogger(__name__)
MAX_SHEETS = 200
MAX_IMPORT_DEPTH = 3


async def stylesheet_images(
    client: httpx.AsyncClient,
    sheet_urls: Iterable[str],
    root: str,
    concurrency: int = 4,
    max_sheets: int = MAX_SHEETS,
) -> dict[str, list[str]]:
    """{stylesheet URL: image URLs it uses, including through @import}. Same-site sheets only:
    CDN and font stylesheets (Google Fonts, icon fonts) hold no site images."""
    semaphore = asyncio.Semaphore(concurrency)
    texts: dict[str, str] = {}
    imports: dict[str, list[str]] = {}

    async def fetch(url: str) -> str:
        async with semaphore:
            fetched = await _fetch(client, url)
        if fetched is None:
            log.debug("stylesheet not fetched: %s", url)
            return ""
        return fetched[1].decode("utf-8", errors="replace")

    requested = [u for u in dict.fromkeys(sheet_urls) if same_site(u, root)]
    pending = requested
    for _ in range(MAX_IMPORT_DEPTH + 1):
        todo = [u for u in dict.fromkeys(pending) if u not in texts]
        todo = todo[: max(0, max_sheets - len(texts))]
        if not todo:
            break
        bodies = await asyncio.gather(*(fetch(u) for u in todo))
        pending = []
        for url, body in zip(todo, bodies, strict=True):
            texts[url] = body
            imports[url] = [i for i in css_imports(body, url) if same_site(i, root)]
            pending += imports[url]

    def images_of(url: str, seen: set[str]) -> list[str]:
        if url in seen:
            return []
        seen.add(url)
        found = css_urls(texts.get(url, ""), url)
        for imported in imports.get(url, []):
            found += images_of(imported, seen)
        return found

    return {u: list(dict.fromkeys(images_of(u, set()))) for u in requested if u in texts}
