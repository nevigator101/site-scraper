import gzip

from site_scraper.assets import original_candidates
from site_scraper.discover import parse_robots, parse_sitemap

SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc> https://x.co.za/ </loc></url>
  <url><loc>https://x.co.za/about/</loc></url>
</urlset>"""

INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://x.co.za/page-sitemap.xml</loc></sitemap>
</sitemapindex>"""


def test_parse_sitemap_and_index_and_gzip():
    assert parse_sitemap(SITEMAP) == (["https://x.co.za/", "https://x.co.za/about/"], [])
    assert parse_sitemap(INDEX) == ([], ["https://x.co.za/page-sitemap.xml"])
    assert parse_sitemap(gzip.compress(SITEMAP))[0][1] == "https://x.co.za/about/"


def test_parse_sitemap_refuses_dtd_and_garbage():
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset><url><loc>&a;</loc></url></urlset>'
    assert parse_sitemap(evil) == ([], [])
    assert parse_sitemap(b"<html>not a sitemap") == ([], [])


def test_parse_robots():
    robots = parse_robots(
        "User-agent: *\nDisallow: /private/\nCrawl-delay: 3\nSitemap: https://x.co.za/s.xml\n",
        "site-scraper",
    )
    assert robots.sitemaps == ["https://x.co.za/s.xml"]
    assert robots.crawl_delay == 3.0
    assert robots.allowed("https://x.co.za/about/", "site-scraper")
    assert not robots.allowed("https://x.co.za/private/a.html", "site-scraper")


def test_original_image_candidates():
    wix = "https://static.wixstatic.com/media/ab12_cd~mv2.jpg/v1/fill/w_400,h_300/ab12_cd~mv2.jpg"
    assert original_candidates(wix)[0] == "https://static.wixstatic.com/media/ab12_cd~mv2.jpg"
    wp = "https://x.co.za/wp-content/uploads/2024/01/team-300x200.jpg"
    assert original_candidates(wp) == ["https://x.co.za/wp-content/uploads/2024/01/team.jpg", wp]
    sq = "https://images.squarespace-cdn.com/content/a/b.jpg?format=500w"
    assert original_candidates(sq)[0].endswith("b.jpg?format=original")
    plain = "https://x.co.za/img/logo.png"
    assert original_candidates(plain) == [plain]
