"""Pure functions that read facts out of a page's HTML. No network, no browser."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Tag

from site_scraper.urls import normalize, same_site

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}\b")
# International (+27 82 123 4567) or local (082 123 4567, (011) 123-4567) numbers.
PHONE_RE = re.compile(
    r"(?<![\w+])(?:\+\d{1,3}[\s.-]?)?(?:\(0?\d{1,4}\)|0\d{1,4})[\s.-]?\d{3}[\s.-]?\d{3,4}\b"
)
NOT_EMAIL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".js", ".css")

SOCIAL_HOSTS = {
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "instagram.com": "instagram",
    "linkedin.com": "linkedin",
    "twitter.com": "x",
    "x.com": "x",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "tiktok.com": "tiktok",
    "pinterest.com": "pinterest",
    "threads.net": "threads",
    "google.com/maps": "google_maps",
    "goo.gl/maps": "google_maps",
    "maps.app.goo.gl": "google_maps",
    "g.page": "google_business",
    "tripadvisor.com": "tripadvisor",
    "hellopeter.com": "hellopeter",
}

ANALYTICS_PATTERNS = {
    "google_analytics_4": re.compile(r"\bG-[A-Z0-9]{6,12}\b"),
    "universal_analytics": re.compile(r"\bUA-\d{4,10}-\d{1,4}\b"),
    "google_tag_manager": re.compile(r"\bGTM-[A-Z0-9]{4,9}\b"),
    "google_ads": re.compile(r"\bAW-\d{6,12}\b"),
    "meta_pixel": re.compile(r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d{10,20})['\"]"),
}

PLATFORM_SIGNS = [
    ("Wix", re.compile(r"static\.wixstatic\.com|wix\.com|_wixCIDX|wix-warmup-data", re.I)),
    ("Squarespace", re.compile(r"squarespace(-cdn)?\.com|static1\.squarespace", re.I)),
    ("Shopify", re.compile(r"cdn\.shopify\.com|Shopify\.theme", re.I)),
    ("Webflow", re.compile(r"webflow\.(com|io)|data-wf-site", re.I)),
    ("WordPress", re.compile(r"/wp-content/|/wp-includes/|wp-json", re.I)),
    ("Joomla", re.compile(r"/media/jui/|Joomla!", re.I)),
    ("Drupal", re.compile(r"/sites/default/files/|Drupal\.settings|drupal\.js", re.I)),
    ("GoDaddy Website Builder", re.compile(r"img1\.wsimg\.com|godaddy", re.I)),
    ("Weebly", re.compile(r"weebly\.com|editmysite\.com", re.I)),
    ("Duda", re.compile(r"multiscreensite\.com|dudaone|irp\.cdn-website\.com", re.I)),
    ("Framer", re.compile(r"framerusercontent\.com|framer\.com", re.I)),
    ("Next.js", re.compile(r"/_next/static/|__NEXT_DATA__", re.I)),
]

PAGE_BUILDERS = [
    ("Elementor", re.compile(r"elementor", re.I)),
    ("Divi", re.compile(r"et_pb_|Divi", re.I)),
    ("WPBakery", re.compile(r"vc_row|js_composer", re.I)),
    ("WooCommerce", re.compile(r"woocommerce", re.I)),
    ("Yoast SEO", re.compile(r"yoast", re.I)),
]


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "lxml")


def _text(node: Tag | None) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        node = soup.find("meta", attrs={"name": name}) or soup.find(
            "meta", attrs={"property": name}
        )
        if node and node.get("content"):
            return " ".join(str(node["content"]).split())
    return ""


# ---- Page metadata ----------------------------------------------------------------------------


def page_meta(soup: BeautifulSoup, url: str) -> dict:
    canonical = soup.find("link", rel=lambda v: v and "canonical" in v)
    html_tag = soup.find("html")
    og_image = _meta(soup, "og:image", "twitter:image")
    return {
        "title": _text(soup.title),
        "meta_description": _meta(soup, "description", "og:description"),
        "canonical": normalize(canonical.get("href", ""), url) if canonical else None,
        "h1": [_text(h) for h in soup.find_all("h1") if _text(h)],
        "headings": [
            {"level": int(h.name[1]), "text": _text(h)}
            for h in soup.find_all(["h1", "h2", "h3", "h4"])
            if _text(h)
        ],
        "og_image": normalize(og_image, url) if og_image else None,
        "lang": html_tag.get("lang", "") if html_tag else "",
        "robots": _meta(soup, "robots"),
        "generator": _meta(soup, "generator"),
    }


def links(soup: BeautifulSoup, url: str) -> list[dict]:
    """Every <a href> with its normalised URL and text, in page order."""
    found = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        found.append(
            {
                "href": href,
                "url": normalize(href, url),
                "text": _text(a) or a.get("aria-label", "") or a.get("title", ""),
            }
        )
    return found


def internal_links(soup: BeautifulSoup, url: str, root: str) -> list[str]:
    return list(
        dict.fromkeys(
            link["url"] for link in links(soup, url) if link["url"] and same_site(link["url"], root)
        )
    )


# ---- Structured data ----------------------------------------------------------------------------


def _flatten_jsonld(data) -> Iterable[dict]:
    if isinstance(data, list):
        for item in data:
            yield from _flatten_jsonld(item)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _flatten_jsonld(data["@graph"])
        else:
            yield data


def jsonld(soup: BeautifulSoup) -> list[dict]:
    blocks = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        blocks.extend(_flatten_jsonld(data))
    return blocks


def jsonld_types(blocks: list[dict]) -> set[str]:
    types: set[str] = set()
    for block in blocks:
        value = block.get("@type", [])
        types.update([value] if isinstance(value, str) else [str(v) for v in value])
    return types


BUSINESS_TYPES = re.compile(
    r"Organization|LocalBusiness|Store|Restaurant|Dentist|Physician|Attorney|LegalService|"
    r"ProfessionalService|HomeAndConstructionBusiness|AutomotiveBusiness|HealthAndBeautyBusiness|"
    r"FoodEstablishment|LodgingBusiness|MedicalBusiness|SportsActivityLocation|EducationalOrganization|"
    r"Corporation|NGO"
)


def _address_text(address) -> str:
    if isinstance(address, str):
        return address
    if isinstance(address, dict):
        keys = ("streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry")
        parts = []
        for key in keys:
            value = address.get(key)
            if isinstance(value, dict):
                value = value.get("name")
            if value:
                parts.append(str(value))
        return ", ".join(parts)
    if isinstance(address, list) and address:
        return _address_text(address[0])
    return ""


NOT_BUSINESS_TYPES = re.compile(
    r"^(WebSite|WebPage|BreadcrumbList|ImageObject|SearchAction|ListItem|Article|BlogPosting|"
    r"Product|Offer|Person|FAQPage|Question|Answer|VideoObject|ItemList)$"
)


def _is_business(block: dict) -> bool:
    types = jsonld_types([block])
    if BUSINESS_TYPES.search(" ".join(types)):
        return True
    # schema.org has hundreds of LocalBusiness subtypes (Plumber, Bakery, ...): accept any other
    # named thing that has contact details.
    return (
        bool(types)
        and not any(NOT_BUSINESS_TYPES.match(t) for t in types)
        and bool(block.get("name"))
        and bool(block.get("telephone") or block.get("address") or block.get("email"))
    )


def business_from_jsonld(blocks: list[dict]) -> dict:
    for block in blocks:
        if not _is_business(block):
            continue
        logo = block.get("logo")
        if isinstance(logo, dict):
            logo = logo.get("url")
        hours = block.get("openingHours") or block.get("openingHoursSpecification") or []
        return {
            "type": block.get("@type"),
            "name": block.get("name", ""),
            "legal_name": block.get("legalName", ""),
            "telephone": block.get("telephone", ""),
            "email": str(block.get("email", "")).removeprefix("mailto:"),
            "address": _address_text(block.get("address")),
            "opening_hours": hours if isinstance(hours, list) else [hours],
            "same_as": block.get("sameAs", []),
            "logo": logo or "",
            "price_range": block.get("priceRange", ""),
        }
    return {}


# ---- Contact details ----------------------------------------------------------------------------


def emails(soup: BeautifulSoup) -> list[str]:
    found = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if href.lower().startswith("mailto:"):
            address = unquote(href[7:].split("?")[0]).strip()
            if address:
                found.append(address.lower())
    for match in EMAIL_RE.findall(soup.get_text(" ")):
        if not match.lower().endswith(NOT_EMAIL_SUFFIXES):
            found.append(match.lower())
    return list(dict.fromkeys(found))


def phones(soup: BeautifulSoup) -> list[str]:
    found = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if href.lower().startswith("tel:"):
            found.append(unquote(href[4:]).strip())
    body = soup.body or soup
    for match in PHONE_RE.findall(_text(body)):
        if len(re.sub(r"\D", "", match)) >= 9:
            found.append(match.strip())
    return list(dict.fromkeys(found))


def whatsapp(soup: BeautifulSoup) -> list[str]:
    found = []
    for a in soup.find_all("a", href=True):
        parts = urlsplit(str(a["href"]))
        host = (parts.hostname or "").lower()
        if host in ("wa.me", "api.whatsapp.com", "web.whatsapp.com", "chat.whatsapp.com"):
            number = parts.path.strip("/") or dict(
                p.split("=", 1) for p in parts.query.split("&") if "=" in p
            ).get("phone", "")
            found.append(number or str(a["href"]))
        elif str(a["href"]).startswith("whatsapp:"):
            found.append(str(a["href"]))
    return list(dict.fromkeys(found))


def social_links(soup: BeautifulSoup) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        parts = urlsplit(href)
        host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
        target = f"{host}{parts.path}"
        for pattern, name in SOCIAL_HOSTS.items():
            if host == pattern or target.startswith(pattern) or host.endswith("." + pattern):
                if "sharer" in parts.path or "share" in parts.query or "intent/tweet" in parts.path:
                    break  # share buttons, not the business's own profile
                found.setdefault(name, [])
                if href not in found[name]:
                    found[name].append(href)
                break
    return found


# ---- Navigation and forms -----------------------------------------------------------------------


def _menu_items(ul: Tag, url: str, depth: int = 0) -> list[dict]:
    items = []
    for li in ul.find_all("li", recursive=False):
        child = li.find(["ul", "ol"])

        def own(node: Tag, child: Tag | None = child) -> bool:
            return child is None or child not in node.parents

        a = next((n for n in li.find_all("a", href=True) if own(n)), None)
        if a:
            label = _text(a)
        else:
            heading = next((n for n in li.find_all(["span", "button", "strong"]) if own(n)), None)
            label = (
                _text(heading)
                if heading
                else " ".join(li.find_all(string=True, recursive=False)).strip()
            )
        entry = {
            "text": label[:80],
            "url": normalize(str(a["href"]), url) if a else None,
            "children": _menu_items(child, url, depth + 1) if child and depth < 3 else [],
        }
        if entry["text"] or entry["children"]:
            items.append(entry)
    return items


def nav_menu(soup: BeautifulSoup, url: str) -> list[dict]:
    """The main menu as a tree. Prefers <nav> inside <header>, then any <nav>, then header links."""
    header = soup.find("header") or soup.find(attrs={"role": "banner"})
    candidates = []
    if header:
        candidates += header.find_all("nav") + header.find_all(attrs={"role": "navigation"})
    candidates += soup.find_all("nav") + soup.find_all(attrs={"role": "navigation"})
    for nav in candidates:
        ul = nav.find(["ul", "ol"])
        if ul:
            items = _menu_items(ul, url)
            if len(items) >= 2:
                return items
        anchors = nav.find_all("a", href=True)
        if len(anchors) >= 2:
            return [
                {"text": _text(a)[:80], "url": normalize(str(a["href"]), url), "children": []}
                for a in anchors
                if _text(a)
            ]
    if header:
        return [
            {"text": _text(a)[:80], "url": normalize(str(a["href"]), url), "children": []}
            for a in header.find_all("a", href=True)
            if _text(a)
        ]
    return []


def forms(soup: BeautifulSoup, url: str) -> list[dict]:
    found = []
    for form in soup.find_all("form"):
        fields = []
        for field in form.find_all(["input", "textarea", "select"]):
            kind = field.get("type", field.name).lower()
            if kind in ("hidden", "submit", "button", "image", "reset"):
                continue
            label = ""
            if field.get("id"):
                node = soup.find("label", attrs={"for": field["id"]})
                label = _text(node)
            if not label and field.find_parent("label"):
                label = _text(field.find_parent("label"))
            fields.append(
                {
                    "name": field.get("name", ""),
                    "type": kind,
                    "label": label or field.get("placeholder", "") or field.get("aria-label", ""),
                    "required": field.has_attr("required"),
                }
            )
        if not fields:
            continue
        names = " ".join(f"{f['name']} {f['label']}" for f in fields).lower()
        if any(f["type"] == "password" for f in fields):
            purpose = "login"
        elif "search" in names or any(f["type"] == "search" for f in fields):
            purpose = "search"
        elif len(fields) <= 2 and "mail" in names:
            purpose = "newsletter"
        elif "message" in names or any(f["type"] == "textarea" for f in fields):
            purpose = "contact"
        else:
            purpose = "other"
        found.append(
            {
                "purpose": purpose,
                "action": normalize(form.get("action", ""), url) or url,
                "method": form.get("method", "get").lower(),
                "fields": fields,
            }
        )
    return found


# ---- Third parties ------------------------------------------------------------------------------


def third_party(soup: BeautifulSoup, url: str, root: str) -> dict:
    """External hosts the page loads from (CSP hints), embeds and analytics IDs."""
    hosts: dict[str, set[str]] = {}

    def add(kind: str, src: str | None) -> None:
        absolute = normalize(src or "", url)
        if absolute and not same_site(absolute, root):
            hosts.setdefault(urlsplit(absolute).hostname or "", set()).add(kind)

    for node in soup.find_all("script", src=True):
        add("script", node["src"])
    for node in soup.find_all("link", href=True):
        rel = " ".join(node.get("rel", []))
        if "stylesheet" in rel or "preconnect" in rel or "font" in node.get("as", ""):
            add("style" if "stylesheet" in rel else "connect", node["href"])
    for node in soup.find_all("img", src=True):
        add("img", node["src"])
    embeds = []
    for node in soup.find_all(["iframe", "embed", "video"], src=True):
        add("frame", node["src"])
        absolute = normalize(str(node["src"]), url)
        if absolute:
            embeds.append({"type": node.name, "src": absolute, "title": node.get("title", "")})
    raw = str(soup)
    analytics = {}
    for name, pattern in ANALYTICS_PATTERNS.items():
        ids = sorted(set(pattern.findall(raw)))
        if ids:
            analytics[name] = ids
    return {
        "hosts": {host: sorted(kinds) for host, kinds in sorted(hosts.items()) if host},
        "embeds": embeds,
        "analytics": analytics,
    }


def detect_platform(html: str, generator: str = "", headers: dict | None = None) -> dict:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    haystack = " ".join([html[:400_000], generator, headers.get("x-powered-by", "")])
    platform = next((name for name, pattern in PLATFORM_SIGNS if pattern.search(haystack)), "")
    if generator and not platform:
        platform = generator.split()[0]
    return {
        "platform": platform or "unknown (hand-built or unrecognised)",
        "generator": generator,
        "plugins": [name for name, pattern in PAGE_BUILDERS if pattern.search(haystack)],
        "server": headers.get("server", ""),
    }


# ---- Images -------------------------------------------------------------------------------------

CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)""", re.I | re.S)
CSS_IMPORT_RE = re.compile(r"""@import\s+(?:url\(\s*)?['"]?([^'")\s;]+)""", re.I)
CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
NOT_IMAGE_EXTENSIONS = {".woff", ".woff2", ".ttf", ".otf", ".eot", ".css", ".htc", ".js"}
# Lazy-load plugins and page builders keep background images in these until JavaScript runs.
BACKGROUND_DATA_ATTRS = (
    "data-bg",
    "data-bg-image",
    "data-background",
    "data-background-image",
    "data-lazy-background",
    "data-image-src",
)
# Theme, plugin and core assets (icons, spinners, card logos): reported apart from site images.
FRAMEWORK_PATH_RE = re.compile(r"/wp-content/(plugins|themes)/|/wp-includes/", re.I)


def is_framework_asset(url: str) -> bool:
    return bool(FRAMEWORK_PATH_RE.search(urlsplit(url).path))


def css_urls(css: str, base: str) -> list[str]:
    """Absolute URLs of the images a piece of CSS uses through url(). Fonts and data: skipped."""
    from site_scraper.urls import extension

    found = []
    for _, raw in CSS_URL_RE.findall(CSS_COMMENT_RE.sub("", css or "")):
        raw = raw.strip()
        if not raw or raw.startswith(("data:", "#")):
            continue
        absolute = normalize(raw, base)
        if absolute and extension(absolute) not in NOT_IMAGE_EXTENSIONS:
            found.append(absolute)
    return list(dict.fromkeys(found))


def css_imports(css: str, base: str) -> list[str]:
    """Stylesheets pulled in with @import."""
    found = (normalize(m, base) for m in CSS_IMPORT_RE.findall(CSS_COMMENT_RE.sub("", css or "")))
    return list(dict.fromkeys(u for u in found if u))


def stylesheets(soup: BeautifulSoup, url: str) -> list[str]:
    """Linked stylesheets (rel=stylesheet, or preloaded as style), in page order."""
    found = []
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel", [])).lower()
        if "stylesheet" in rel or ("preload" in rel and link.get("as") == "style"):
            absolute = normalize(str(link["href"]), url)
            if absolute:
                found.append(absolute)
    return list(dict.fromkeys(found))


def _image_strings(data) -> Iterable[str]:
    """Every string in parsed JSON that looks like an image URL (page-builder settings)."""
    from site_scraper.urls import IMAGE_EXTENSIONS, extension

    if isinstance(data, dict):
        for value in data.values():
            yield from _image_strings(value)
    elif isinstance(data, list):
        for value in data:
            yield from _image_strings(value)
    elif isinstance(data, str) and extension(data.split("?")[0]) in IMAGE_EXTENSIONS:
        yield data


def _largest_from_srcset(srcset: str) -> str:
    best, best_width = "", -1.0
    for candidate in srcset.split(","):
        bits = candidate.strip().split()
        if not bits:
            continue
        width = 0.0
        if len(bits) > 1:
            try:
                width = float(bits[1].rstrip("wx"))
            except ValueError:
                width = 0.0
        if width > best_width:
            best, best_width = bits[0], width
    return best


def images(soup: BeautifulSoup, url: str) -> list[dict]:
    """Images on the page: <img>, <picture> sources, background images (style attributes,
    <style> blocks, lazy-load data attributes, page-builder settings), video posters, og:image
    and icons. Images in linked stylesheets are added later from the fetched CSS (styles.py)."""
    found: dict[str, dict] = {}

    def add(src: str | None, alt: str | None, kind: str) -> dict | None:
        src = (src or "").strip()
        if not src or src.startswith("data:"):
            return None
        absolute = normalize(src, url)
        if not absolute:
            return None
        entry = found.setdefault(absolute, {"url": absolute, "alt": "", "kind": kind})
        if alt and not entry["alt"]:
            entry["alt"] = " ".join(alt.split())
        return entry

    for img in soup.find_all("img"):
        alt = img.get("alt")
        srcset = img.get("srcset") or img.get("data-srcset")
        src = _largest_from_srcset(srcset) if srcset else ""
        src = src or img.get("data-src") or img.get("data-lazy-src") or img.get("src")
        entry = add(src, alt, "img")
        if entry is None:
            continue
        if alt is None:
            entry["missing_alt"] = True
        link = img.find_parent("a", href=True)
        target = normalize(str(link["href"]), url) if link else None
        if target and not entry.get("links_to"):
            entry["links_to"] = target  # logo strips and carousels: where each image points
    for source in soup.find_all("source"):
        srcset = source.get("srcset") or source.get("data-srcset")
        if srcset and "image" in source.get("type", "image"):
            add(_largest_from_srcset(srcset), "", "picture")
    for node in soup.find_all(style=True):
        for match in css_urls(str(node["style"]), url):
            add(match, "", "background")
    for style in soup.find_all("style"):
        for match in css_urls(style.get_text(), url):
            add(match, "", "css_background")
    for attr in BACKGROUND_DATA_ATTRS:
        for node in soup.find_all(attrs={attr: True}):
            value = str(node[attr])
            for match in css_urls(value, url) if "url(" in value else [value]:
                add(match, "", "background")
    # JSON in data-* attributes: Elementor slideshows (data-settings, used as backgrounds),
    # WooCommerce variation photos (data-product_variations), slider plugins.
    for node in soup.find_all(True):
        for attr, value in node.attrs.items():
            if not attr.startswith("data-") or not isinstance(value, str):
                continue
            if value.lstrip()[:1] not in ("{", "["):
                continue
            try:
                data = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                continue
            kind = "background" if attr == "data-settings" else "data_attribute"
            for match in _image_strings(data):
                add(match, "", kind)
    for video in soup.find_all("video", poster=True):
        add(str(video["poster"]), "", "video_poster")
    og = _meta(soup, "og:image")
    add(og, "", "og_image")
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel", [])).lower()
        if "icon" in rel:
            add(link["href"], "", "icon")
    return list(found.values())


def documents(soup: BeautifulSoup, url: str) -> list[dict]:
    from site_scraper.urls import is_document

    found = {}
    for link in links(soup, url):
        if link["url"] and is_document(link["url"]):
            found.setdefault(link["url"], {"url": link["url"], "text": link["text"]})
    return list(found.values())
