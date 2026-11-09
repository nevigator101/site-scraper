"""Breadth-first crawl of one site, rendering each page in Chromium through Crawl4AI.

Crawl4AI renders JavaScript, scrolls to load lazy content, takes the screenshot and turns the page
into Markdown (with menus, footers and other boilerplate pruned). The crawl order, same-site
rules, robots.txt and politeness are handled here so they are predictable and tested.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    PruningContentFilterLXML,
)
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

from site_scraper.discover import Robots
from site_scraper.extract import documents, internal_links, soup_of
from site_scraper.urls import dedupe_key, is_crawlable, normalize, same_site

log = logging.getLogger(__name__)
MIN_FIT_MARKDOWN = 200  # below this the pruned Markdown probably lost the content: use the raw one
STRUCTURAL_BLOCK = "Blocked by anti-bot protection: Structural"


@dataclass
class CrawlOptions:
    max_pages: int = 300
    max_depth: int = 5
    delay: float = 1.0
    concurrency: int = 2
    screenshots: bool = True
    user_agent: str = ""
    insecure: bool = False
    page_timeout_ms: int = 60_000


@dataclass
class Page:
    url: str
    depth: int
    found_on: str | None
    source: str  # start | link | sitemap
    final_url: str = ""
    status: int | None = None
    ok: bool = False
    error: str = ""
    html: str = ""
    markdown: str = ""
    raw_markdown: str = ""
    screenshot: bytes | None = None
    headers: dict = field(default_factory=dict)


@dataclass
class CrawlResult:
    root: str
    pages: list[Page]
    skipped_robots: list[str]
    documents: dict[str, dict]
    hit_page_cap: bool


def _markdown_of(result) -> tuple[str, str]:
    md = getattr(result, "markdown", None)
    if md is None:
        return "", ""
    raw = getattr(md, "raw_markdown", None) or str(md)
    fit = getattr(md, "fit_markdown", None) or ""
    return (fit if len(fit.strip()) >= MIN_FIT_MARKDOWN else raw), raw


def _screenshot_of(result) -> bytes | None:
    shot = getattr(result, "screenshot", None)
    if not shot:
        return None
    if isinstance(shot, bytes):
        return shot
    try:
        return base64.b64decode(shot)
    except ValueError:
        return None


def run_config(options: CrawlOptions) -> CrawlerRunConfig:
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilterLXML(threshold=0.45, threshold_type="dynamic"),
            options={"ignore_images": False, "body_width": 0},
        ),
        screenshot=options.screenshots,
        scan_full_page=True,
        scroll_delay=0.2,
        wait_until="load",
        delay_before_return_html=1.0,
        page_timeout=options.page_timeout_ms,
        remove_overlay_elements=True,
        verbose=False,
    )


async def crawl(
    start_url: str,
    robots: Robots,
    sitemap_urls: list[str],
    options: CrawlOptions,
    progress: Callable[[Page, int], None] | None = None,
) -> CrawlResult:
    root = start_url
    delay = max(options.delay, robots.crawl_delay or 0)
    frontier: list[Page] = [Page(start_url, 0, None, "start")]
    seen = {dedupe_key(start_url)}
    pages: list[Page] = []
    skipped: list[str] = []
    docs: dict[str, dict] = {}
    sitemap_queued = False

    browser = BrowserConfig(
        headless=True,
        user_agent=options.user_agent,
        viewport_width=1280,
        viewport_height=900,
        ignore_https_errors=options.insecure,
        verbose=False,
    )
    config = run_config(options)

    async with AsyncWebCrawler(config=browser) as crawler:
        while frontier and len(pages) < options.max_pages:
            batch: list[Page] = []
            while frontier and len(batch) < options.concurrency:
                candidate = frontier.pop(0)
                if robots.allowed(candidate.url, options.user_agent):
                    batch.append(candidate)
                else:
                    skipped.append(candidate.url)
            batch = batch[: options.max_pages - len(pages)]
            if not batch:
                continue
            results = await asyncio.gather(
                *(crawler.arun(url=page.url, config=config) for page in batch),
                return_exceptions=True,
            )
            for page, result in zip(batch, results, strict=True):
                _fill(page, result)
                pages.append(page)
                if progress:
                    progress(page, len(pages))
                if page is pages[0] and page.final_url and not same_site(page.final_url, root):
                    # The start URL redirected to another domain (e.g. old.co.za → new.co.za).
                    log.warning("start URL redirected to %s; crawling that site", page.final_url)
                    root = page.final_url
                if not page.html or not same_site(page.final_url or page.url, root):
                    continue
                soup = soup_of(page.html)
                base = page.final_url or page.url
                for doc in documents(soup, base):
                    if same_site(doc["url"], root):
                        entry = docs.setdefault(doc["url"], {**doc, "pages": []})
                        entry["pages"].append(page.url)
                if page.depth >= options.max_depth:
                    continue
                for link in internal_links(soup, base, root):
                    key = dedupe_key(link)
                    if key in seen or not is_crawlable(link):
                        continue
                    seen.add(key)
                    frontier.append(Page(link, page.depth + 1, page.url, "link"))
            if not frontier and not sitemap_queued:
                # Pages only the sitemap knows about (orphans) are crawled after the link graph.
                sitemap_queued = True
                for url in sitemap_urls:
                    key = dedupe_key(url)
                    if key not in seen and same_site(url, root):
                        seen.add(key)
                        frontier.append(Page(url, 1, None, "sitemap"))
            if frontier and len(pages) < options.max_pages:
                await asyncio.sleep(delay)

    return CrawlResult(
        root=root,
        pages=pages,
        skipped_robots=skipped,
        documents=docs,
        hit_page_cap=bool(frontier) and len(pages) >= options.max_pages,
    )


def _fill(page: Page, result) -> None:
    if isinstance(result, BaseException):
        page.error = f"{type(result).__name__}: {result}"[:500]
        return
    page.status = getattr(result, "status_code", None)
    error = getattr(result, "error_message", "") or ""
    html = getattr(result, "html", "") or ""
    # Crawl4AI's anti-bot check also flags small, plain pages (a short blog index, a thank-you
    # page) as "Structural" blocks. With a good status and real HTML they are real pages.
    small_page = error.startswith(STRUCTURAL_BLOCK) and bool(html)
    success = bool(getattr(result, "success", False)) or small_page
    page.ok = success and (page.status or 200) < 400
    page.final_url = normalize(getattr(result, "redirected_url", None) or "") or page.url
    page.error = error[:500] if not page.ok else ""
    page.headers = dict(getattr(result, "response_headers", None) or {})
    if page.ok:
        page.html = html
        page.markdown, page.raw_markdown = _markdown_of(result)
        page.screenshot = _screenshot_of(result)
