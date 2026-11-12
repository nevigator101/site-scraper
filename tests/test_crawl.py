import io
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from site_scraper.crawl import Page, _fill, _retryable


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (97, 206, 112)).save(out, "PNG")
    return out.getvalue()


def _result(**kw):
    base = dict(
        status_code=200,
        success=True,
        html="<html></html>",
        error_message="",
        redirected_url=None,
        response_headers={},
        markdown=None,
        screenshot=_png(),
    )
    return SimpleNamespace(**{**base, **kw})


def test_screenshot_is_spooled_to_disk_as_webp_not_kept_in_memory(tmp_path):
    page = Page("https://example.co.za/a/", 0, None, "start")
    _fill(page, _result(), tmp_path)
    assert page.ok
    assert page.screenshot_file.startswith(str(tmp_path))
    assert page.screenshot_file.endswith(".webp")
    with Image.open(page.screenshot_file) as im:
        assert (im.format, im.size) == ("WEBP", (40, 30))
    assert not hasattr(page, "screenshot")


def test_hook_screenshot_is_spooled_as_given(tmp_path):
    page = Page("https://example.co.za/b/", 0, None, "start")
    _fill(page, _result(screenshot=None), tmp_path, shot=b"RIFF....WEBP")
    assert Path(page.screenshot_file).read_bytes() == b"RIFF....WEBP"


def test_unreadable_screenshot_is_skipped_not_fatal(tmp_path):
    page = Page("https://example.co.za/c/", 0, None, "start")
    _fill(page, _result(screenshot=b"\x89PNG"), tmp_path)
    assert page.ok
    assert page.screenshot_file == ""


def test_browser_failures_and_5xx_are_retried_but_4xx_are_not():
    crashed = Page("https://example.co.za/x/", 1, None, "link")
    _fill(crashed, RuntimeError("pipe closed"))
    assert _retryable(crashed)

    server_error = Page("https://example.co.za/y/", 1, None, "link")
    _fill(server_error, _result(status_code=503, success=False))
    assert _retryable(server_error)

    missing = Page("https://example.co.za/z/", 1, None, "link")
    _fill(missing, _result(status_code=404, success=False))
    assert not _retryable(missing)

    fine = Page("https://example.co.za/", 0, None, "start")
    _fill(fine, _result())
    assert not _retryable(fine)


def test_browser_blocks_trackers_and_media_files_only():
    from site_scraper.crawl import MEDIA_FILES, TRACKERS

    for url in ("https://pagead2.googlesyndication.com/pagead/js/x.js",
                "https://www.googletagmanager.com/gtag/js?id=G-1",
                "https://ep1.adtrafficquality.google/x"):  # fmt: skip
        assert TRACKERS.search(url), url
    for url in ("https://example.co.za/", "https://fonts.gstatic.com/s/x.woff2",
                "https://example.co.za/googletagmanager.com.html"):  # fmt: skip
        assert not TRACKERS.search(url), url
    assert MEDIA_FILES.search("https://example.co.za/wp-content/uploads/2026/04/422.mp3")
    assert MEDIA_FILES.search("https://example.co.za/clip.MP4?v=2")
    assert not MEDIA_FILES.search("https://example.co.za/img/poster.png")
    assert not MEDIA_FILES.search("https://example.co.za/mp3-players/")
