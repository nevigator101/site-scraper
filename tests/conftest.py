"""Fixture site: copied to a temp folder, images and a PDF generated, served on localhost."""

from __future__ import annotations

import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

FIXTURE_SITE = Path(__file__).parent / "fixtures" / "site"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep test output clean
        pass


def _png(path: Path, size: tuple[int, int], colour: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path, "PNG")


@pytest.fixture(scope="session")
def fixture_site(tmp_path_factory) -> str:
    """Serve the fixture site and return its base URL, e.g. http://127.0.0.1:54321."""
    root = tmp_path_factory.mktemp("site")
    shutil.copytree(FIXTURE_SITE, root, dirs_exist_ok=True)
    _png(root / "img" / "logo.png", (240, 80), (10, 92, 173))
    _png(root / "img" / "hero.png", (1600, 900), (242, 140, 40))
    _png(root / "img" / "team.png", (800, 600), (34, 34, 34))
    _png(root / "img" / "favicon.png", (32, 32), (10, 92, 173))
    _png(root / "img" / "bg-hero.png", (1920, 800), (20, 40, 60))  # only in style.css
    _png(root / "img" / "bg-band.png", (1200, 400), (60, 40, 20))  # only in extra.css (@import)
    (root / "files").mkdir()
    (root / "files" / "price-list.pdf").write_bytes(b"%PDF-1.4\n%fixture\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(root)))
    base = f"http://127.0.0.1:{server.server_address[1]}"
    for name in ("sitemap.xml", "robots.txt"):
        path = root / name
        path.write_text(path.read_text().replace("{BASE}", base))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield base
    server.shutdown()
