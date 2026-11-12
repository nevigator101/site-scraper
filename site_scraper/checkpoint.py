"""Save every crawled page to `<kit>/_crawl/` the moment it is fetched, so a crash loses nothing.

    _crawl/pages.jsonl         one line per fetched page (a later line for the same URL wins)
    _crawl/html/<key>.html     rendered HTML
    _crawl/md/<key>.md         page Markdown, readable straight away
    _crawl/shots/<key>.webp    desktop screenshot
    _crawl/meta.json           start URL and crawl root

`--resume` reads this back and carries on where the crawl stopped. When the kit is written
successfully the folder is deleted: everything in it is then in pages/ and screenshots/.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

FOLDER = "_crawl"
FIELDS = (
    "url", "depth", "found_on", "source", "final_url", "status", "ok", "error", "headers",
    "screenshot_file",
)  # fmt: skip


def key_of(url: str) -> str:
    """Readable, unique file name for a URL: `about-us-1a2b3c4d`."""
    path = re.sub(r"[^a-z0-9]+", "-", urlsplit(url).path.lower()).strip("-")[:80] or "home"
    return f"{path}-{hashlib.sha256(url.encode()).hexdigest()[:8]}"


class Checkpoint:
    def __init__(self, folder: Path):
        self.folder = folder
        self.html = folder / "html"
        self.md = folder / "md"
        self.shots = folder / "shots"
        self.index = folder / "pages.jsonl"
        for d in (self.html, self.md, self.shots):
            d.mkdir(parents=True, exist_ok=True)

    def save(self, page) -> None:
        """Write one page. Call it right after the page is fetched."""
        key = key_of(page.url)
        if page.html:
            (self.html / f"{key}.html").write_text(page.html, encoding="utf-8")
        if page.ok:
            (self.md / f"{key}.md").write_text(
                f"<!-- {page.final_url or page.url} -->\n\n{page.markdown}", encoding="utf-8"
            )
        line = {f: getattr(page, f) for f in FIELDS} | {"key": key}
        with open(self.index, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")

    def load(self) -> list[dict]:
        """Saved pages as dicts with `html` and `markdown` filled in, in crawl order."""
        if not self.index.exists():
            return []
        by_url: dict[str, dict] = {}
        for raw in self.index.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(raw)
            except ValueError:  # a line cut short by the crash
                continue
            by_url.pop(entry["url"], None)
            by_url[entry["url"]] = entry
        saved = []
        for entry in by_url.values():
            html = self.html / f"{entry['key']}.html"
            md = self.md / f"{entry['key']}.md"
            entry["html"] = html.read_text(encoding="utf-8") if html.exists() else ""
            entry["markdown"] = (
                md.read_text(encoding="utf-8").split("\n\n", 1)[-1] if md.exists() else ""
            )
            if entry.get("screenshot_file") and not Path(entry["screenshot_file"]).exists():
                entry["screenshot_file"] = ""
            saved.append(entry)
        return saved

    def save_meta(self, **meta) -> None:
        (self.folder / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def load_meta(self) -> dict:
        path = self.folder / "meta.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
