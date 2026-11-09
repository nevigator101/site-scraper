"""URL helpers: normalising, same-site checks, file types and file names."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_PARAMS = re.compile(r"^(utm_\w+|fbclid|gclid|gclsid|dclid|msclkid|mc_cid|mc_eid|_ga|ref)$")

# Never crawled as pages.
ASSET_EXTENSIONS = {
    ".css", ".js", ".mjs", ".map", ".json", ".xml", ".rss", ".atom", ".txt",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico", ".bmp", ".tif", ".tiff",
    ".heic", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".webm", ".mov", ".avi", ".mp3", ".wav", ".ogg", ".m4a",
    ".zip", ".rar", ".7z", ".gz", ".tar", ".exe", ".dmg", ".apk",
}  # fmt: skip
# Downloaded into files/ instead of crawled.
DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".csv", ".rtf",
}  # fmt: skip
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".svg", ".ico", ".bmp", ".tif", ".tiff",
    ".heic",
}  # fmt: skip


def bare_host(host: str) -> str:
    host = (host or "").lower().split("@")[-1]
    host = host.split(":")[0] if host.count(":") == 1 else host
    return host[4:] if host.startswith("www.") else host


def site_key(url: str) -> str:
    """The site a URL belongs to: host without www, plus a non-default port."""
    parts = urlsplit(url)
    host = bare_host(parts.hostname or "")
    port = parts.port
    if port and not (
        (parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)
    ):
        return f"{host}:{port}"
    return host


def same_site(url: str, root: str) -> bool:
    return urlsplit(url).scheme in ("http", "https") and site_key(url) == site_key(root)


def normalize(url: str, base: str | None = None) -> str | None:
    """Absolute URL without fragment, tracking parameters or default port. None if not http(s)."""
    url = (url or "").strip()
    if not url or url.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "sms:")):
        return None
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    netloc = parts.hostname.lower()
    if parts.port and not (
        (parts.scheme == "http" and parts.port == 80)
        or (parts.scheme == "https" and parts.port == 443)
    ):
        netloc = f"{netloc}:{parts.port}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    query = sorted((k, v) for k, v in query if not TRACKING_PARAMS.match(k))
    path = parts.path or "/"
    return urlunsplit((parts.scheme, netloc, path, urlencode(query), ""))


INDEX_FILE_RE = re.compile(r"/(index|default)\.(html?|php|aspx?|jsp|shtml)$", re.I)


def dedupe_key(url: str) -> str:
    """Treat www/non-www, http/https, a trailing slash and /index.html as the same page."""
    parts = urlsplit(url)
    path = INDEX_FILE_RE.sub("/", parts.path).rstrip("/") or "/"
    return f"{site_key(url)}{path}?{parts.query}" if parts.query else f"{site_key(url)}{path}"


def extension(url: str) -> str:
    return PurePosixPath(urlsplit(url).path).suffix.lower()


def is_document(url: str) -> bool:
    return extension(url) in DOCUMENT_EXTENSIONS


def is_crawlable(url: str) -> bool:
    ext = extension(url)
    return ext not in ASSET_EXTENSIONS and ext not in DOCUMENT_EXTENSIONS


def path_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path or '/'}?{parts.query}" if parts.query else (parts.path or "/")


_SLUG_RE = re.compile(r"[^a-z0-9]+")
PAGE_SUFFIXES = {".html", ".htm", ".php", ".asp", ".aspx", ".jsp", ".shtml"}


def slugify(text: str, max_length: int = 80) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")[:max_length].strip("-")


def page_slug(url: str) -> str:
    """File name for a page: / → home, /services/web/ → services--web, queries get a hash."""
    parts = urlsplit(url)
    raw = [s for s in INDEX_FILE_RE.sub("/", parts.path).split("/") if s]
    if raw and PurePosixPath(raw[-1]).suffix.lower() in PAGE_SUFFIXES:
        raw[-1] = PurePosixPath(raw[-1]).stem
    segments = [s for s in (slugify(s) for s in raw) if s]
    slug = "--".join(segments)[:120] or "home"
    if parts.query:
        slug += "-" + hashlib.sha1(parts.query.encode()).hexdigest()[:8]  # noqa: S324
    return slug
