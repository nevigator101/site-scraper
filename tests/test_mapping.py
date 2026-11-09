import pytest

from site_scraper.mapping import needs_redirect, suggest


@pytest.mark.parametrize(
    ("url", "kind", "new_path"),
    [
        ("https://x.co.za/", "home", "/"),
        ("https://x.co.za/index.html", "home", "/"),
        ("https://x.co.za/about-us.html", "about", "/about/"),
        ("https://x.co.za/Who-We-Are/", "about", "/about/"),
        ("https://x.co.za/contact-us", "contact", "/contact/"),
        ("https://x.co.za/popia-notice/", "privacy", "/privacy/"),
        ("https://x.co.za/terms-and-conditions/", "terms", "/terms/"),
        ("https://x.co.za/news/", "blog_index", "/blog/"),
        ("https://x.co.za/blog/page/2/", "blog_index", "/blog/"),
        ("https://x.co.za/blog/Fix-A-Tap/", "blog_post", "/blog/fix-a-tap/"),
        ("https://x.co.za/2023/04/12/winter-tips/", "blog_post", "/blog/winter-tips/"),
        ("https://x.co.za/product/blue-mug/", "shop_product", "/shop/product/blue-mug/"),
        ("https://x.co.za/products/blue-mug", "shop_product", "/shop/product/blue-mug/"),
        ("https://x.co.za/product-category/mugs/", "shop_category", "/shop/category/mugs/"),
        ("https://x.co.za/collections/all", "shop_index", "/shop/"),
        ("https://x.co.za/cart/", "shop_cart", "/shop/cart/"),
        ("https://x.co.za/services/geysers/", "other", ""),
    ],
)
def test_suggest_by_path(url, kind, new_path):
    result = suggest(url)
    assert (result.kind, result.new_path) == (kind, new_path)


def test_suggest_uses_structured_data_and_title():
    assert suggest("https://x.co.za/tips-for-winter/", jsonld_types={"BlogPosting"}).new_path == (
        "/blog/tips-for-winter/"
    )
    assert suggest("https://x.co.za/mug", jsonld_types={"Product"}).kind == "shop_product"
    assert suggest("https://x.co.za/our-people", title="About us | Acme").new_path == "/about/"
    assert suggest("https://x.co.za/services/plumbing", title="About us").kind == "other"


def test_needs_redirect():
    assert needs_redirect("https://x.co.za/about-us.html", "/about/")
    assert needs_redirect("https://x.co.za/about.html", "/about/")
    assert needs_redirect("https://x.co.za/?page_id=2", "/")
    assert not needs_redirect("https://x.co.za/about", "/about/")
    assert not needs_redirect("https://x.co.za/", "/")
    assert not needs_redirect("https://x.co.za/anything", "")
