"""Breadth-first crawl of one site, rendering each page in Chromium through Crawl4AI.

Crawl4AI renders JavaScript, scrolls to load lazy content, takes the screenshot and turns the page
into Markdown (with menus, footers and other boilerplate pruned). The crawl order, same-site
rules, robots.txt and politeness are handled here so they are predictable and tested.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    PruningContentFilterLXML,
)
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

from site_scraper.checkpoint import FIELDS, Checkpoint
from site_scraper.discover import Robots
from site_scraper.extract import documents, internal_links, soup_of
from site_scraper.guard import HostPolicy
from site_scraper.urls import dedupe_key, is_crawlable, normalize, same_site, site_key

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
    # Chromium's memory grows over a long crawl; a fresh browser every N pages keeps it flat.
    restart_browser_every: int = 100
    # Desktop screenshots are written here as they are taken instead of being held in memory:
    # a full-page PNG can be several MB, so a few hundred pages filled the container's RAM.
    spool_dir: str = ""
    # Every page is saved here as soon as it is fetched (checkpoint.py); screenshots too.
    checkpoint_dir: str = ""
    resume: bool = False  # carry on from checkpoint_dir instead of starting again
    policy: HostPolicy | None = None  # SSRF guard and budget (guard.py); None only in old callers


class OffsiteRedirect(SystemExit):
    """The start URL redirected to another site that is not on the allowlist."""


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
    screenshot_file: str = ""  # PNG in CrawlOptions.spool_dir, moved into the kit at the end
    headers: dict = field(default_factory=dict)


@dataclass
class CrawlResult:
    root: str
    pages: list[Page]
    skipped_robots: list[str]
    documents: dict[str, dict]
    hit_page_cap: bool
    budget_hit: str = ""


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

    policy = options.policy
    browser = BrowserConfig(
        headless=True,
        user_agent=options.user_agent,
        viewport_width=1280,
        viewport_height=900,
        ignore_https_errors=options.insecure,
        verbose=False,
        # Through the egress proxy when compose.yaml provides one (guard.py).
        proxy_config=policy.browser_proxy if policy is not None else None,
    )
    config = run_config(options)
    checkpoint = Checkpoint(Path(options.checkpoint_dir)) if options.checkpoint_dir else None
    if checkpoint is not None:
        spool = checkpoint.shots
    else:
        spool = Path(options.spool_dir or tempfile.mkdtemp(prefix="site-scraper-shots-"))
        spool.mkdir(parents=True, exist_ok=True)
    retried = False
    since_restart = 0

    def discover(page: Page) -> None:
        """Note the page's documents and queue its unseen same-site links."""
        if not page.html or not same_site(page.final_url or page.url, root):
            return
        soup = soup_of(page.html)
        base = page.final_url or page.url
        for doc in documents(soup, base):
            if same_site(doc["url"], root):
                entry = docs.setdefault(doc["url"], {**doc, "pages": []})
                entry["pages"].append(page.url)
        if page.depth >= options.max_depth:
            return
        for link in internal_links(soup, base, root):
            key = dedupe_key(link)
            if key in seen or not is_crawlable(link):
                continue
            seen.add(key)
            frontier.append(Page(link, page.depth + 1, page.url, "link"))

    def queue_sitemap() -> bool:
        """Pages only the sitemap knows about (orphans) are crawled after the link graph."""
        for url in sitemap_urls:
            key = dedupe_key(url)
            if key not in seen and same_site(url, root):
                seen.add(key)
                frontier.append(Page(url, 1, None, "sitemap"))
        return True

    if checkpoint is not None and options.resume:
        root = checkpoint.load_meta().get("root") or root
        if policy is not None:
            policy.root = root
        saved = checkpoint.load()
        frontier.clear()
        for entry in saved:
            page = Page(**{k: entry[k] for k in FIELDS}, html=entry["html"])
            page.markdown = entry["markdown"]
            seen.add(dedupe_key(page.url))
            if _retryable(page):
                frontier.append(Page(page.url, page.depth, page.found_on, page.source))
                continue
            pages.append(page)
        for page in list(pages):
            discover(page)
        if dedupe_key(start_url) not in {dedupe_key(p["url"]) for p in saved}:
            frontier.insert(0, Page(start_url, 0, None, "start"))
        if not frontier:  # the link graph was done: carry on with the sitemap-only pages
            sitemap_queued = queue_sitemap()
        print(f"resuming: {len(pages)} page(s) already saved, {len(frontier)} queued", flush=True)
    if checkpoint is not None:
        checkpoint.save_meta(start_url=start_url, root=root)

    crawler = await _start_crawler(browser, policy)
    try:
        while frontier and len(pages) < options.max_pages:
            if since_restart >= options.restart_browser_every:
                await crawler.close()
                crawler = await _start_crawler(browser, policy)
                since_restart = 0
            if policy is not None and policy.budget.exhausted:
                log.warning("stopping the crawl: budget reached (%s)", policy.budget.hit)
                break
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
            since_restart += len(batch)
            for page, result in zip(batch, results, strict=True):
                _fill(page, result, spool)
                if checkpoint is not None:
                    checkpoint.save(page)
                pages.append(page)
                if progress:
                    progress(page, len(pages))
                if page is pages[0] and page.final_url and not same_site(page.final_url, root):
                    # The start URL redirected to another domain (e.g. old.co.za → new.co.za).
                    # Only follow it when that site is allowed (standard §8: authorised hosts).
                    if policy is not None and not policy.on_allowlist(page.final_url):
                        raise OffsiteRedirect(
                            f"The start URL redirected to another site: {page.final_url}\n"
                            f"Run again with that URL if you are authorised to copy it, or add "
                            f"--allow-host {site_key(page.final_url)}"
                        )
                    log.warning("start URL redirected to %s; crawling that site", page.final_url)
                    root = page.final_url
                    if policy is not None:
                        policy.root = root
                    if checkpoint is not None:
                        checkpoint.save_meta(start_url=start_url, root=root)
                discover(page)
            if not frontier and not sitemap_queued:
                sitemap_queued = queue_sitemap()
            if not frontier and sitemap_queued and not retried:
                # Pages that failed in the browser (crash, timeout) or with a 5xx get one more try
                # at the end, in a fresh browser. 4xx answers are real and are not retried.
                retried = True
                failed = {id(p) for p in pages if _retryable(p)}
                if failed:
                    log.warning("retrying %d failed page(s) in a fresh browser", len(failed))
                    frontier.extend(
                        Page(p.url, p.depth, p.found_on, p.source) for p in pages if id(p) in failed
                    )
                    pages = [p for p in pages if id(p) not in failed]
                    since_restart = options.restart_browser_every
            if frontier and len(pages) < options.max_pages:
                await asyncio.sleep(delay)
    finally:
        await crawler.close()

    return CrawlResult(
        root=root,
        pages=pages,
        skipped_robots=skipped,
        documents=docs,
        hit_page_cap=bool(frontier) and len(pages) >= options.max_pages,
        budget_hit=policy.budget.hit if policy is not None else "",
    )


async def _start_crawler(browser: BrowserConfig, policy: HostPolicy | None) -> AsyncWebCrawler:
    crawler = AsyncWebCrawler(config=browser)
    await crawler.start()
    if policy is not None:
        guarded: set[int] = set()

        async def guard_context(page, context=None, **_kwargs):
            context = context or page.context
            if id(context) not in guarded:  # one route handler per browser context
                guarded.add(id(context))
                await policy.install_browser_guard(context)
            return page

        crawler.crawler_strategy.set_hook("on_page_context_created", guard_context)
    return crawler


def _retryable(page: Page) -> bool:
    return not page.ok and (page.status is None or page.status >= 500)


def _fill(page: Page, result, spool: Path | None = None) -> None:
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
        shot = _screenshot_of(result)
        if shot and spool is not None:
            target = spool / f"{hashlib.sha256(page.url.encode()).hexdigest()[:24]}.png"
            target.write_bytes(shot)
            page.screenshot_file = str(target)
