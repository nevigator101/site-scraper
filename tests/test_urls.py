from site_scraper.urls import (
    dedupe_key,
    is_crawlable,
    is_document,
    normalize,
    page_slug,
    same_site,
    site_key,
)


def test_normalize_strips_fragment_tracking_and_default_port():
    assert (
        normalize("HTTPS://Example.co.za:443/About/?utm_source=x&b=2&a=1#team")
        == "https://example.co.za/About/?a=1&b=2"
    )


def test_normalize_resolves_relative_and_rejects_non_http():
    assert (
        normalize("../contact", "https://example.co.za/a/b/") == "https://example.co.za/a/contact"
    )
    for href in ("mailto:x@y.z", "tel:+2711", "javascript:void(0)", "#top", "", "ftp://x.y/z"):
        assert normalize(href, "https://example.co.za/") is None


def test_same_site_treats_www_and_scheme_as_one_site_but_not_subdomains_or_ports():
    root = "https://example.co.za/"
    assert same_site("http://www.example.co.za/about", root)
    assert not same_site("https://shop.example.co.za/", root)
    assert not same_site("https://example.co.za.evil.test/", root)
    assert site_key("http://127.0.0.1:8000/") == "127.0.0.1:8000"
    assert not same_site("http://127.0.0.1:9000/", "http://127.0.0.1:8000/")


def test_dedupe_key_treats_index_files_as_the_folder():
    assert dedupe_key("https://x.co.za/index.html") == dedupe_key("https://x.co.za/")
    assert dedupe_key("https://x.co.za/blog/Default.aspx") == dedupe_key("https://x.co.za/blog/")
    assert dedupe_key("https://x.co.za/indexes.html") != dedupe_key("https://x.co.za/")


def test_dedupe_key_ignores_trailing_slash_and_www():
    assert dedupe_key("https://www.example.co.za/about/") == dedupe_key(
        "http://example.co.za/about"
    )


def test_file_types():
    assert is_document("https://x.co.za/files/Price-List.PDF")
    assert not is_crawlable("https://x.co.za/img/logo.png")
    assert not is_crawlable("https://x.co.za/files/menu.pdf")
    assert is_crawlable("https://x.co.za/about-us.html")


def test_page_slug():
    assert page_slug("https://x.co.za/") == "home"
    assert page_slug("https://x.co.za/About-Us.html") == "about-us"
    assert page_slug("https://x.co.za/services/geysers/") == "services--geysers"
    assert page_slug("https://x.co.za/index.html") == "home"
    assert page_slug("https://x.co.za/books_1/index.html") == "books-1"
    with_query = page_slug("https://x.co.za/index.php?page_id=5")
    assert with_query.startswith("home-") and len(with_query) == len("home-") + 8
