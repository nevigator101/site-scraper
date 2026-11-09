"""Download a site's images and documents, keeping the best available original."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx
from PIL import Image

from site_scraper.urls import slugify

log = logging.getLogger(__name__)

MAX_BYTES = 30 * 1024 * 1024
IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/bmp": ".bmp",
    "image/tiff": ".tif",
    "image/heic": ".heic",
}
WP_SIZE_SUFFIX = re.compile(r"-\d{2,5}x\d{2,5}(?=\.(jpe?g|png|gif|webp)$)", re.I)


def original_candidates(url: str) -> list[str]:
    """The full-size original first (CDN resize parameters removed), then the URL as seen."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    candidates: list[str] = []
    if host.endswith("wixstatic.com") and "/v1/" in parts.path:
        # /media/abc~mv2.jpg/v1/fill/w_400,h_300/abc~mv2.jpg → /media/abc~mv2.jpg
        candidates.append(
            urlunsplit((parts.scheme, parts.netloc, parts.path.split("/v1/")[0], "", ""))
        )
    elif "squarespace" in host:
        query = [(k, v) for k, v in parse_qsl(parts.query) if k != "format"]
        query.append(("format", "original"))
        candidates.append(
            urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
        )
    elif "/wp-content/uploads/" in parts.path and WP_SIZE_SUFFIX.search(parts.path):
        path = WP_SIZE_SUFFIX.sub("", parts.path)
        candidates.append(urlunsplit((parts.scheme, parts.netloc, path, parts.query, "")))
    elif host.endswith("cdn.shopify.com") and parts.query:
        query = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("width", "height", "crop")]
        candidates.append(
            urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
        )
    candidates.append(url)
    return list(dict.fromkeys(candidates))


@dataclass
class Asset:
    url: str
    kind: str  # image | document
    alt: str = ""
    pages: list[str] = field(default_factory=list)
    file: str = ""
    downloaded_from: str = ""
    content_type: str = ""
    bytes: int = 0
    width: int | None = None
    height: int | None = None
    sha256: str = ""
    error: str = ""
    missing_alt_on: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)


def _file_name(url: str, content_type: str, kind: str) -> str:
    name = PurePosixPath(unquote(urlsplit(url).path)).name or "file"
    if "~mv2" in name:  # Wix names: 1a2b3c_4d5e~mv2.jpg
        name = name.replace("~mv2", "")
    stem, suffix = PurePosixPath(name).stem, PurePosixPath(name).suffix.lower()
    if kind == "image" and content_type in IMAGE_TYPES:
        suffix = IMAGE_TYPES[content_type] if suffix not in (".jpeg", ".jpg") else suffix
    return f"{slugify(stem, 60) or 'file'}{suffix[:6]}"


def _measure(data: bytes) -> tuple[int | None, int | None]:
    try:
        with Image.open(BytesIO(data)) as img:
            return img.width, img.height
    except Exception:  # noqa: BLE001 (SVG, ICO variants, corrupt files: size is optional)
        return None, None


async def _fetch(client: httpx.AsyncClient, url: str) -> tuple[str, bytes] | None:
    """(content type, body) or None. Stops reading past MAX_BYTES."""
    try:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                return None
            if int(response.headers.get("content-length") or 0) > MAX_BYTES:
                return None
            chunks, total = [], 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_BYTES:
                    return None
                chunks.append(chunk)
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            return content_type, b"".join(chunks)
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("download failed %s: %s", url, exc)
        return None


async def download(
    assets: list[Asset],
    out_dir: Path,
    client: httpx.AsyncClient,
    concurrency: int = 4,
) -> list[Asset]:
    """Download every asset into out_dir/images or out_dir/files. Dedupes by content hash."""
    semaphore = asyncio.Semaphore(concurrency)
    by_hash: dict[str, str] = {}
    used_names: set[str] = set()
    lock = asyncio.Lock()

    async def one(asset: Asset) -> None:
        async with semaphore:
            fetched = None
            candidates = original_candidates(asset.url) if asset.kind == "image" else [asset.url]
            for candidate in candidates:
                fetched = await _fetch(client, candidate)
                if fetched is not None and (
                    asset.kind != "image" or fetched[0].startswith("image/")
                ):
                    asset.downloaded_from = candidate
                    break
                fetched = None
        if fetched is None:
            asset.error = "download failed or not an image"
            return
        content_type, data = fetched
        digest = hashlib.sha256(data).hexdigest()
        asset.content_type, asset.bytes, asset.sha256 = content_type, len(data), digest
        if asset.kind == "image":
            asset.width, asset.height = _measure(data)
        async with lock:
            if digest in by_hash:
                asset.file = by_hash[digest]
                return
            folder = "images" if asset.kind == "image" else "files"
            name = _file_name(asset.downloaded_from, content_type, asset.kind)
            if name in used_names:
                stem, suffix = PurePosixPath(name).stem, PurePosixPath(name).suffix
                name = f"{stem}-{digest[:8]}{suffix}"
            used_names.add(name)
            relative = f"{folder}/{name}"
            by_hash[digest] = relative
            asset.file = relative
        target = out_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    await asyncio.gather(*(one(a) for a in assets))
    return assets
