"""Suggest where each old URL should live on the new site (a draft 301 redirect map).

Default routes follow a common small-business layout (and the Py_Django_WebstackV1 template):
/  /about/  /contact/  /privacy/  /terms/  /blog/<slug>/  /shop/  /shop/product/<slug>/
/shop/category/<slug>/. Anything else is left for a person to decide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from site_scraper.urls import PAGE_SUFFIXES, slugify


@dataclass(frozen=True)
class Suggestion:
    kind: str
    new_path: str
    confidence: str  # high | medium | low | none


ROUTES = {
    "home": "/",
    "about": "/about/",
    "contact": "/contact/",
    "privacy": "/privacy/",
    "terms": "/terms/",
    "blog_index": "/blog/",
    "blog_post": "/blog/{slug}/",
    "shop_index": "/shop/",
    "shop_product": "/shop/product/{slug}/",
    "shop_category": "/shop/category/{slug}/",
    "shop_cart": "/shop/cart/",
}


# Whole-path rules, checked against the cleaned path (lower case, no extension, no trailing slash).
def _one_of(*alternatives: str) -> re.Pattern[str]:
    return re.compile(r"^/(" + "|".join(alternatives) + r")$")


PATH_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("home", re.compile(r"^(/|/home|/index|/default|/welcome|/home-?page)$")),
    ("about", _one_of(r"about(-us|-me)?", "who-we-are", "our-story", "our-team", "team",
                      "company", "meet-the-team", "profile")),
    ("contact", _one_of(r"contact(-us|-me)?", "get-in-touch", r"enquir(y|ies)", r"inquir(y|ies)",
                        "reach-us", "find-us", "location")),
    ("privacy", _one_of(r"privacy(-policy|-notice|-statement)?", r"popia(-policy|-notice)?",
                        "gdpr", "data-protection", r"paia(-manual)?")),
    ("terms", _one_of(r"terms(-and-conditions|-of-use|-of-service|-conditions)?", r"t-?and-?c",
                      "tcs", "legal", "disclaimer")),
    ("blog_index", _one_of("blog", "news", "articles", "insights", "journal", "posts", "updates",
                           "stories")),
    ("shop_index", _one_of("shop", "store", "products", r"catalog(ue)?", r"collections(/all)?")),
    ("shop_cart", _one_of("cart", "basket", "bag", "checkout")),
]  # fmt: skip

BLOG_PREFIX = re.compile(r"^/(blog|news|articles|insights|journal|posts|updates|stories)/(.+)$")
DATED_POST = re.compile(r"^/(\d{4})/(\d{1,2})(/\d{1,2})?/([^/]+)$")
PRODUCT_PREFIX = re.compile(r"^/(shop/|store/)?(product|products|item|p)/([^/]+)$")
COLLECTION_PREFIX = re.compile(
    r"^/(shop/|store/)?(product-category|category|categories|collections|collection)/(.+)$"
)
BLOG_TYPES = {"BlogPosting", "Article", "NewsArticle"}
PRODUCT_TYPES = {"Product", "ProductGroup"}


def clean_path(url_or_path: str) -> str:
    path = urlsplit(url_or_path).path if "://" in url_or_path else url_or_path.split("?")[0]
    path = re.sub(r"/{2,}", "/", path.lower()) or "/"
    stem = PurePosixPath(path)
    if stem.suffix in PAGE_SUFFIXES:
        path = str(stem.with_suffix(""))
    path = path.rstrip("/") or "/"
    return path


def _last_slug(path: str) -> str:
    return slugify(path.rstrip("/").rsplit("/", 1)[-1]) or "post"


def suggest(url: str, title: str = "", jsonld_types: set[str] | None = None) -> Suggestion:
    """Best guess for one old URL. jsonld_types (from extract.jsonld_types) sharpen the guess."""
    types = jsonld_types or set()
    path = clean_path(url)
    for kind, pattern in PATH_RULES:
        if pattern.match(path):
            return Suggestion(kind, ROUTES[kind], "high")
    if match := PRODUCT_PREFIX.match(path):
        return Suggestion(
            "shop_product", ROUTES["shop_product"].format(slug=slugify(match[3])), "high"
        )
    if match := COLLECTION_PREFIX.match(path):
        slug = _last_slug(match[3])
        return Suggestion("shop_category", ROUTES["shop_category"].format(slug=slug), "medium")
    if match := BLOG_PREFIX.match(path):
        rest = match[2]
        if re.fullmatch(r"(page|category|tag|author)/.+|\d+", rest):
            return Suggestion("blog_index", ROUTES["blog_index"], "medium")
        return Suggestion("blog_post", ROUTES["blog_post"].format(slug=_last_slug(rest)), "high")
    if match := DATED_POST.match(path):
        return Suggestion("blog_post", ROUTES["blog_post"].format(slug=slugify(match[4])), "high")
    if types & PRODUCT_TYPES:
        return Suggestion(
            "shop_product", ROUTES["shop_product"].format(slug=_last_slug(path)), "medium"
        )
    if types & BLOG_TYPES:
        return Suggestion("blog_post", ROUTES["blog_post"].format(slug=_last_slug(path)), "medium")
    # A single-segment path whose title reads like a core page.
    lowered = title.lower()
    if path.count("/") == 1:
        for kind, words in (
            ("about", ("about us", "about me", "our story", "who we are")),
            ("contact", ("contact us", "contact me", "get in touch")),
            ("privacy", ("privacy policy", "privacy notice", "popia")),
            ("terms", ("terms and conditions", "terms of use", "terms & conditions")),
        ):
            if any(w in lowered for w in words):
                return Suggestion(kind, ROUTES[kind], "medium")
    return Suggestion("other", "", "none")


def needs_redirect(old_url: str, new_path: str) -> bool:
    """True when the old path differs from the suggested new one (ignoring a trailing slash).

    /about.html → /about/ needs one; /about → /about/ does not (the new site adds the slash).
    """
    if not new_path:
        return False
    parts = urlsplit(old_url) if "://" in old_url else urlsplit("http://x" + old_url)
    old = parts.path.rstrip("/") or "/"
    return bool(parts.query) or old != (new_path.rstrip("/") or "/")
