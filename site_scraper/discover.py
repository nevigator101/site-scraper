"""robots.txt and sitemap discovery."""

from __future__ import annotations

import logging
import zlib
from dataclasses import dataclass, field
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET  # noqa: S405 (parsed with entity expansion off, below)

import httpx

from site_scraper.assets import _fetch
from site_scraper.urls import is_crawlable, normalize, same_site

log = logging.getLogger(__name__)

MAX_SITEMAPS = 50
MAX_SITEMAP_BYTES = 20 * 1024 * 1024
MAX_ROBOTS_BYTES = 512 * 1024


def gunzip_bounded(data: bytes, limit: int = MAX_SITEMAP_BYTES) -> bytes:
    """Decompress at most `limit` bytes: a small gzip file cannot expand into gigabytes."""
    return zlib.decompressobj(wbits=16 + zlib.MAX_WBITS).decompress(data, limit)


@dataclass
class Robots:
    """robots.txt rules. allow_all when there is no robots.txt or it is ignored."""

    parser: RobotFileParser | None = None
    sitemaps: list[str] = field(default_factory=list)
    crawl_delay: float | None = None
    found: bool = False

    def allowed(self, url: str, user_agent: str) -> bool:
        return True if self.parser is None else self.parser.can_fetch(user_agent, url)


def parse_robots(text: str, user_agent: str) -> Robots:
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    delay = parser.crawl_delay(user_agent)
    return Robots(
        parser=parser,
        sitemaps=list(parser.site_maps() or []),
        crawl_delay=float(delay) if delay else None,
        found=True,
    )


async def fetch_robots(client: httpx.AsyncClient, root: str, user_agent: str) -> Robots:
    fetched = await _fetch(client, urljoin(root, "/robots.txt"), max_bytes=MAX_ROBOTS_BYTES)
    if fetched is None or "html" in fetched[0]:
        log.info("robots.txt not found, too large or not reachable")
        return Robots()
    return parse_robots(fetched[1].decode("utf-8", errors="replace"), user_agent)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(content: bytes) -> tuple[list[str], list[str]]:
    """Return (page URLs, child sitemap URLs) from a sitemap or sitemap index."""
    if content[:2] == b"\x1f\x8b":
        try:
            content = gunzip_bounded(content)
        except zlib.error:
            return [], []
    if b"<!DOCTYPE" in content[:500] or b"<!ENTITY" in content:
        return [], []  # refuse DTDs: no entity expansion tricks
    try:
        root = ET.fromstring(content)  # noqa: S314 (DTDs refused above)
    except ET.ParseError:
        return [], []
    pages: list[str] = []
    children: list[str] = []
    for node in root:
        loc = next((c.text for c in node if _local(c.tag) == "loc" and c.text), None)
        if not loc:
            continue
        (children if _local(node.tag) == "sitemap" else pages).append(loc.strip())
    return pages, children


async def fetch_sitemap_urls(client: httpx.AsyncClient, root: str, robots: Robots) -> list[str]:
    """Every same-site page URL listed in the site's sitemaps."""
    queue = list(robots.sitemaps) or [
        urljoin(root, "/sitemap.xml"),
        urljoin(root, "/sitemap_index.xml"),
    ]
    seen: set[str] = set()
    found: list[str] = []
    while queue and len(seen) < MAX_SITEMAPS:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        fetched = await _fetch(client, url, max_bytes=MAX_SITEMAP_BYTES)
        if fetched is None:
            continue
        pages, children = parse_sitemap(fetched[1])
        queue.extend(c for c in children if same_site(c, root))
        for page in pages:
            normalized = normalize(page)
            if normalized and same_site(normalized, root) and is_crawlable(normalized):
                found.append(normalized)
    return list(dict.fromkeys(found))
