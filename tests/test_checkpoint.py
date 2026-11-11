"""Pages are saved as they are fetched, and a stopped crawl can be resumed."""

from __future__ import annotations

from datetime import date

import pytest

from site_scraper.checkpoint import Checkpoint, key_of
from site_scraper.cli import build_parser, run
from site_scraper.crawl import CrawlOptions, Page, crawl
from site_scraper.discover import Robots


def test_save_and_load_round_trip_survives_a_line_cut_short(tmp_path):
    cp = Checkpoint(tmp_path / "_crawl")
    page = Page("https://example.co.za/about-us/", 1, "https://example.co.za/", "link",
                final_url="https://example.co.za/about-us/", status=200, ok=True,
                html="<h1>About</h1>", markdown="# About\n\nWe fix pipes.")  # fmt: skip
    cp.save(page)
    with open(cp.index, "a", encoding="utf-8") as handle:
        handle.write('{"url": "https://example.co.za/cut')  # the crash happened mid-write
    [saved] = cp.load()
    assert saved["url"] == page.url and saved["ok"] and saved["status"] == 200
    assert saved["html"] == "<h1>About</h1>"
    assert saved["markdown"] == "# About\n\nWe fix pipes."
    assert (cp.md / f"{key_of(page.url)}.md").read_text(encoding="utf-8").startswith("<!-- ")
    assert key_of(page.url).startswith("about-us-")


def test_later_save_of_the_same_url_wins(tmp_path):
    cp = Checkpoint(tmp_path / "_crawl")
    cp.save(Page("https://example.co.za/x/", 1, None, "link", error="timeout"))
    cp.save(Page("https://example.co.za/x/", 1, None, "link", status=200, ok=True, html="<p>"))
    [saved] = cp.load()
    assert saved["ok"]


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_resume_carries_on_without_fetching_saved_pages_again(fixture_site, tmp_path):
    start = fixture_site + "/"
    ckpt = str(tmp_path / "_crawl")
    first = await crawl(start, Robots(), [], CrawlOptions(max_pages=3, delay=0,
                                                          screenshots=False, checkpoint_dir=ckpt))  # fmt: skip
    done_first = {p.url for p in first.pages}
    assert len(done_first) == 3

    fetched: list[str] = []
    second = await crawl(
        start, Robots(), [],
        CrawlOptions(delay=0, screenshots=False, checkpoint_dir=ckpt, resume=True),
        progress=lambda page, _n: fetched.append(page.url),
    )  # fmt: skip
    assert not done_first & set(fetched)  # saved pages were not fetched again
    assert done_first < {p.url for p in second.pages}
    assert len(second.pages) == len(done_first) + len(fetched)


@pytest.mark.asyncio
async def test_fresh_run_refuses_to_mix_with_a_stopped_crawl(tmp_path):
    (tmp_path / f"example.co.za-{date.today():%Y%m%d}" / "_crawl").mkdir(parents=True)
    with pytest.raises(SystemExit, match="--resume"):
        await run(build_parser().parse_args(["https://example.co.za", "--out", str(tmp_path)]))


@pytest.mark.asyncio
async def test_resume_with_nothing_saved_says_so(tmp_path):
    with pytest.raises(SystemExit, match="Nothing to resume"):
        await run(build_parser().parse_args(["https://example.co.za", "--out", str(tmp_path),
                                             "--resume"]))  # fmt: skip
