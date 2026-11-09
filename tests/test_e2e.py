"""Full run against the local fixture site, with a real Chromium. The slowest test (~30 s)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import pytest_asyncio

from site_scraper.cli import build_parser, run

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def kit(fixture_site, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("out")
    args = build_parser().parse_args([fixture_site + "/", "--out", str(out), "--delay", "0"])
    return await run(args)


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.asyncio(loop_scope="module")
async def test_writes_the_whole_kit(kit):
    for name in ("REPORT.md", "inventory.csv", "redirects.csv", "images.csv", "brand.json",
                 "business.json", "site.json"):  # fmt: skip
        assert (kit / name).is_file(), name
    assert (kit / "pages" / "home.md").is_file()
    assert (kit / "pages" / "home.html").is_file()
    assert (kit / "screenshots" / "home-desktop.png").stat().st_size > 1000
    assert (kit / "screenshots" / "home-mobile.png").stat().st_size > 1000


@pytest.mark.asyncio(loop_scope="module")
async def test_crawl_follows_links_and_sitemap_and_obeys_robots(kit):
    paths = {r["url"].split("/", 3)[-1] for r in _rows(kit / "inventory.csv")}
    assert {"", "about-us.html", "contact.html", "services/geysers/", "blog/",
            "blog/fix-a-dripping-tap/", "privacy-policy/", "specials/winter.html",
            "missing-page/"} <= paths  # fmt: skip
    assert not any(p.startswith("private/") for p in paths)  # robots.txt Disallow
    assert not any("other-site.test" in r["url"] for r in _rows(kit / "inventory.csv"))
    winter = next(r for r in _rows(kit / "inventory.csv") if r["url"].endswith("winter.html"))
    assert winter["found_via"] == "sitemap"
    missing = next(r for r in _rows(kit / "inventory.csv") if r["url"].endswith("missing-page/"))
    assert missing["status"] == "404"


@pytest.mark.asyncio(loop_scope="module")
async def test_markdown_has_content_including_javascript_rendered_text(kit):
    home = (kit / "pages" / "home.md").read_text(encoding="utf-8")
    assert home.startswith("---\n")
    assert 'suggested_new_path: "/"' in home
    assert "burst pipes" in home
    geysers = (kit / "pages" / "services--geysers.md").read_text(encoding="utf-8")
    assert "Rendered by JavaScript" in geysers


@pytest.mark.asyncio(loop_scope="module")
async def test_redirect_map(kit):
    redirects = {r["old_path"]: r for r in _rows(kit / "redirects.csv")}
    assert redirects["/about-us.html"]["new_path"] == "/about/"
    assert redirects["/contact.html"]["new_path"] == "/contact/"
    assert redirects["/privacy-policy/"]["new_path"] == "/privacy/"
    assert redirects["/services/geysers/"]["new_path"] == ""  # a person decides
    assert redirects["/files/price-list.pdf"]["page_kind"] == "document"
    assert "/" not in redirects  # home stays home
    assert "/blog/fix-a-dripping-tap/" not in redirects  # already at its new path


@pytest.mark.asyncio(loop_scope="module")
async def test_business_details(kit):
    business = json.loads((kit / "business.json").read_text(encoding="utf-8"))
    assert business["from_structured_data"]["name"] == "Acme Plumbing"
    assert "info@acme-plumbing.test" in business["emails"]
    assert business["whatsapp"] == ["27825550100"]
    assert business["platform"]["platform"] == "WordPress"
    assert [i["text"] for i in business["nav_menu"]][:2] == ["Home", "About us"]
    assert business["forms"][0]["purpose"] == "contact"
    assert "G-ABC1234567" in business["third_party"]["analytics"]["google_analytics_4"]


@pytest.mark.asyncio(loop_scope="module")
async def test_brand_tokens(kit):
    brand = json.loads((kit / "brand.json").read_text(encoding="utf-8"))
    tokens = brand["suggested_tokens"]
    assert tokens["heading"] == "#0a5cad"
    assert tokens["button_background"] == "#f28c28"
    assert tokens["accent"] == "#f28c28"
    assert "Georgia" in tokens["font_body"]
    assert brand["css_variables"]["--brand"] == "#0a5cad"
    assert brand["logos"][0]["src"].endswith("/img/logo.png")


@pytest.mark.asyncio(loop_scope="module")
async def test_images_and_documents_downloaded_and_deduped(kit):
    images = {Path(r["source_url"]).name: r for r in _rows(kit / "images.csv")}
    hero = images["hero.png"]
    assert (hero["width"], hero["height"], hero["alt"]) == (
        "1600",
        "900",
        "A plumber fixing a geyser",
    )
    assert (kit / hero["file"]).is_file()
    team = images["team.png"]
    assert team["alt"] == "The Acme team outside the workshop"  # alt found on the about page
    assert team["missing_alt_on"].split("/", 3)[3] == ""  # only the home page lacks its alt
    assert (kit / "files" / "price-list.pdf").is_file()
    assert len(list((kit / "images").iterdir())) == 4


@pytest.mark.asyncio(loop_scope="module")
async def test_report(kit):
    report = (kit / "REPORT.md").read_text(encoding="utf-8")
    assert "# Rebuild kit:" in report
    assert "Platform: **WordPress**" in report
    assert "Blog: **yes**" in report
    assert "/missing-page/" in report  # broken link listed
    assert "| `/about-us.html` |" in report
    assert "Delete this folder" in report
