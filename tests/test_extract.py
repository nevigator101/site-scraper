from site_scraper import extract
from site_scraper.extract import soup_of
from tests.conftest import FIXTURE_SITE

BASE = "https://acme-plumbing.test/"
HOME = soup_of((FIXTURE_SITE / "index.html").read_text())
CONTACT = soup_of((FIXTURE_SITE / "contact.html").read_text())


def test_page_meta():
    meta = extract.page_meta(HOME, BASE)
    assert meta["title"] == "Acme Plumbing | Johannesburg plumbers"
    assert meta["meta_description"].startswith("Fast, friendly plumbers")
    assert meta["canonical"] == "https://acme-plumbing.test/"
    assert meta["h1"] == ["Johannesburg's friendly plumbers"]
    assert meta["og_image"] == "https://acme-plumbing.test/img/hero.png"
    assert meta["lang"] == "en-ZA"


def test_internal_links_are_deduped_normalised_and_same_site():
    found = extract.internal_links(HOME, BASE, BASE)
    assert "https://acme-plumbing.test/contact.html" in found  # utm_source stripped
    assert "https://acme-plumbing.test/about-us.html" in found
    assert not any("example.org" in url for url in found)
    assert len(found) == len(set(found))


def test_business_details_from_jsonld_graph():
    business = extract.business_from_jsonld(extract.jsonld(HOME))
    assert business["name"] == "Acme Plumbing"
    assert business["telephone"] == "+27 11 555 0100"
    assert business["address"] == "12 Main Road, Johannesburg, 2001, ZA"
    assert business["opening_hours"] == ["Mo-Fr 08:00-17:00"]


def test_contact_details():
    assert extract.emails(HOME) == ["info@acme-plumbing.test"]
    assert "+27115550100" in extract.phones(HOME)
    assert extract.whatsapp(HOME) == ["27825550100"]
    social = extract.social_links(HOME)
    assert social["facebook"] == ["https://www.facebook.com/acmeplumbing"]  # sharer link ignored
    assert social["instagram"] == ["https://www.instagram.com/acmeplumbing/"]


def test_nav_menu_keeps_nesting():
    menu = extract.nav_menu(HOME, BASE)
    labels = [item["text"] for item in menu]
    assert labels == ["Home", "About us", "Services", "Blog", "Contact"]
    services = menu[2]
    assert services["url"] is None
    assert services["children"][0]["url"] == "https://acme-plumbing.test/services/geysers/"


def test_forms_are_classified_with_labels():
    (form,) = extract.forms(CONTACT, BASE)
    assert form["purpose"] == "contact"
    assert form["method"] == "post"
    assert form["action"] == "https://acme-plumbing.test/send.php"
    assert [(f["name"], f["label"], f["required"]) for f in form["fields"]] == [
        ("name", "Your name", True),
        ("email", "Email", True),
        ("message", "Message", False),
    ]


def test_third_party_hosts_embeds_and_analytics():
    home = extract.third_party(HOME, BASE, BASE)
    assert home["hosts"]["www.googletagmanager.com"] == ["script"]
    assert home["analytics"]["google_analytics_4"] == ["G-ABC1234567"]
    contact = extract.third_party(CONTACT, BASE, BASE)
    assert contact["embeds"][0]["src"].startswith("https://www.google.com/maps/embed")


def test_platform_detection():
    assert (
        extract.detect_platform("<link href='/wp-content/themes/x.css'>")["platform"] == "WordPress"
    )
    wix = extract.detect_platform('<img src="https://static.wixstatic.com/media/a.jpg">')
    assert wix["platform"] == "Wix"
    assert extract.detect_platform("<p>hi</p>")["platform"].startswith("unknown")


def test_elementor_divider_is_not_divi():
    elementor = '<div class="elementor-widget-divider"><link id="elementor-divider-css">'
    assert extract.detect_platform(elementor)["plugins"] == ["Elementor"]
    divi = '<div class="et_pb_section"><link href="/wp-content/themes/Divi/style.css">'
    assert "Divi" in extract.detect_platform(divi)["plugins"]


def test_images_with_alt_and_missing_alt():
    found = {i["url"]: i for i in extract.images(HOME, BASE)}
    assert found["https://acme-plumbing.test/img/hero.png"]["alt"] == "A plumber fixing a geyser"
    assert found["https://acme-plumbing.test/img/team.png"].get("missing_alt") is True
    assert found["https://acme-plumbing.test/img/favicon.png"]["kind"] == "icon"


def test_srcset_picks_largest():
    soup = soup_of('<img srcset="a.jpg 480w, b.jpg 1600w, c.jpg 960w" alt="x">')
    assert extract.images(soup, BASE)[0]["url"] == "https://acme-plumbing.test/b.jpg"


def test_background_images_from_style_blocks_lazy_attrs_builders_and_posters():
    soup = soup_of(
        """<style>.hero{background-image:url('/img/hero-bg.jpg')}
        @font-face{src:url(/fonts/a.woff2)} /* url(/img/commented-out.png) */</style>
        <section style="--bg: url(&quot;/img/inline.png&quot;)"></section>
        <div data-bg="/img/lazy.jpg"></div>
        <div data-background-image="url(/img/lazy2.webp)"></div>
        <div data-settings='{"background_slideshow_gallery":[{"id":1,"url":"https:\\/\\/acme-plumbing.test\\/img\\/slide.jpg"}]}'></div>
        <video poster="/img/poster.jpg"></video>"""
    )
    found = {i["url"].removeprefix(BASE): i["kind"] for i in extract.images(soup, BASE)}
    assert found == {
        "img/hero-bg.jpg": "css_background",
        "img/inline.png": "background",
        "img/lazy.jpg": "background",
        "img/lazy2.webp": "background",
        "img/slide.jpg": "background",
        "img/poster.jpg": "video_poster",
    }


def test_images_in_json_data_attributes_such_as_woocommerce_variations():
    variations = (
        '[{"attributes":{"attribute_colour":"Silver"},'
        '"image":{"full_src":"https:\\/\\/acme-plumbing.test\\/img\\/silver.webp"}}]'
    )
    soup = soup_of(f"<form data-product_variations='{variations}'></form>")
    (image,) = extract.images(soup, BASE)
    assert image["url"] == BASE + "img/silver.webp"
    assert image["kind"] == "data_attribute"


def test_css_urls_resolve_against_the_stylesheet_and_skip_fonts():
    css = """@import "parts/extra.css"; .a{background:url(../img/a.png) no-repeat}
    .b{background:url("data:image/png;base64,xx")} @font-face{src:url(f.woff2) format("woff2")}"""
    sheet = "https://x.co.za/wp-content/themes/t/css/style.css?ver=2"
    assert extract.css_urls(css, sheet) == ["https://x.co.za/wp-content/themes/t/img/a.png"]
    assert extract.css_imports(css, sheet) == [
        "https://x.co.za/wp-content/themes/t/css/parts/extra.css"
    ]


def test_framework_assets_are_told_apart_from_site_images():
    assert extract.is_framework_asset("https://x.co.za/wp-content/plugins/woocommerce/a/visa.svg")
    assert extract.is_framework_asset("https://x.co.za/wp-includes/images/spinner.gif")
    assert not extract.is_framework_asset("https://x.co.za/wp-content/uploads/2025/12/Hero.png")
    assert not extract.is_framework_asset("https://x.co.za/assets/hero.jpg")


def test_stylesheets_and_image_links():
    soup = soup_of(
        """<link rel="stylesheet" href="/a.css"><link rel="preload" as="style" href="/b.css">
        <link rel="icon" href="/i.png">
        <a href="https://partner.test/"><img src="/logos/p.png" alt="Partner"></a>"""
    )
    assert extract.stylesheets(soup, BASE) == [BASE + "a.css", BASE + "b.css"]
    logo = next(i for i in extract.images(soup, BASE) if i["url"].endswith("p.png"))
    assert logo["links_to"] == "https://partner.test/"


def test_documents():
    docs = extract.documents(HOME, BASE)
    assert docs == [
        {"url": "https://acme-plumbing.test/files/price-list.pdf", "text": "Price list (PDF)"}
    ]
