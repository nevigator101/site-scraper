"""Command line: python -m site_scraper https://example.co.za"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import httpx

from site_scraper import USER_AGENT, __version__
from site_scraper.assets import download
from site_scraper.brand import collect, summarise
from site_scraper.crawl import CrawlOptions, Page, crawl
from site_scraper.discover import Robots, fetch_robots, fetch_sitemap_urls
from site_scraper.report import (
    add_stylesheet_images,
    all_stylesheets,
    analyse,
    collect_assets,
    write_data,
    write_pages,
    write_report,
)
from site_scraper.styles import stylesheet_images
from site_scraper.urls import normalize, same_site, site_key

log = logging.getLogger("site_scraper")
MOBILE_SHOT_LIMIT = 10
BRAND_PAGE_KINDS = ("about", "contact")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="site-scraper",
        description="Crawl an existing website (one you own or are authorised to copy) and write "
        "a rebuild kit: Markdown per page, screenshots, images, URL inventory, draft redirects, "
        "brand colours and fonts, business details and REPORT.md.",
    )
    parser.add_argument("url", help="Start URL, e.g. https://example.co.za")
    parser.add_argument("--out", default="output", help="Output folder (default: output)")
    parser.add_argument("--max-pages", type=int, default=300, help="Page cap (default: 300)")
    parser.add_argument("--max-depth", type=int, default=5, help="Link depth (default: 5)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between batches (1.0)")
    parser.add_argument("--concurrency", type=int, default=2, help="Pages at once (default: 2)")
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="Ignore robots.txt. Only when the site owner asked you to.",
    )
    parser.add_argument("--no-images", action="store_true", help="Do not download images")
    parser.add_argument("--no-files", action="store_true", help="Do not download PDFs/documents")
    parser.add_argument("--no-screenshots", action="store_true", help="Skip all screenshots")
    parser.add_argument("--no-brand", action="store_true", help="Skip colour/font extraction")
    parser.add_argument("--no-sitemap", action="store_true", help="Only follow links")
    parser.add_argument(
        "--insecure", action="store_true", help="Accept broken or expired HTTPS certificates"
    )
    parser.add_argument("--user-agent", default=USER_AGENT, help="User-Agent header")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    parser.add_argument("--version", action="version", version=f"site-scraper {__version__}")
    return parser


def _progress(page: Page, count: int) -> None:
    state = page.status or ("ok" if page.ok else "failed")
    print(f"  [{count:>3}] {state} {page.url}", flush=True)


async def run(args: argparse.Namespace) -> Path:
    start = normalize(args.url if "://" in args.url else f"https://{args.url}")
    if not start:
        raise SystemExit(f"Not a web address: {args.url}")
    out = Path(args.out) / f"{site_key(start).replace(':', '_')}-{date.today():%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    options = CrawlOptions(
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        delay=args.delay,
        concurrency=max(1, args.concurrency),
        screenshots=not args.no_screenshots,
        user_agent=args.user_agent,
        insecure=args.insecure,
    )
    headers = {"User-Agent": args.user_agent}
    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True, timeout=30, verify=not args.insecure
    ) as client:
        print(f"site-scraper {__version__}: {start} → {out}")
        robots = (
            Robots() if args.ignore_robots else await fetch_robots(client, start, args.user_agent)
        )
        if robots.found:
            print(f"robots.txt found ({len(robots.sitemaps)} sitemap(s) listed)")
        sitemap_urls = [] if args.no_sitemap else await fetch_sitemap_urls(client, start, robots)
        print(f"sitemap: {len(sitemap_urls)} URLs. Crawling (max {options.max_pages} pages)...")

        result = await crawl(start, robots, sitemap_urls, options, progress=_progress)
        site = analyse(result)
        write_pages(out, site)

        brand: dict = {}
        ok = [r for r in site["records"] if r["ok"] and same_site(r["final_url"], result.root)]
        if ok and not (args.no_brand and args.no_screenshots):
            brand_urls = [ok[0]["final_url"]]
            brand_urls += [
                r["final_url"] for r in ok[1:] if r["suggestion"]["kind"] in BRAND_PAGE_KINDS
            ][:2]
            nav_urls = [item["url"] for item in site["business"]["nav_menu"] if item.get("url")]
            by_url = {r["final_url"]: r for r in ok} | {r["url"]: r for r in ok}
            shot_records = [ok[0]] + [by_url[u] for u in nav_urls if u in by_url]
            shots = {}
            if not args.no_screenshots:
                for record in shot_records:
                    target = out / "screenshots" / f"{record['slug']}-mobile.png"
                    shots.setdefault(record["final_url"], target)
                    if len(shots) >= MOBILE_SHOT_LIMIT:
                        break
            print(
                f"reading brand from {len(brand_urls)} page(s), {len(shots)} mobile screenshot(s)"
            )
            per_page, _ = await collect([] if args.no_brand else brand_urls, shots, args.user_agent)
            brand = summarise(per_page)

        if not args.no_images:
            sheets = all_stylesheets(site)
            sheet_images = await stylesheet_images(client, sheets, result.root)
            added = add_stylesheet_images(site, sheet_images)
            print(f"read {len(sheet_images)} stylesheet(s): {added} background image(s) found")

        assets = collect_assets(site, [] if args.no_files else site["documents"])
        if args.no_images:
            assets = [a for a in assets if a.kind != "image"]
        if assets:
            print(f"downloading {len(assets)} images/documents...")
            await download(assets, out, client)

        run_options = {**asdict(options), "url": start, "ignore_robots": args.ignore_robots}
        write_data(out, site, assets, brand, run_options)
        write_report(out, site, assets, brand, run_options)

    saved = sum(1 for r in site["records"] if r["ok"])
    print(f"done: {saved} pages, {sum(1 for a in assets if a.file)} files → {out / 'REPORT.md'}")
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
        return 130
    return 0
