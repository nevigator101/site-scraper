from pathlib import Path
from types import SimpleNamespace

from site_scraper.crawl import Page, _fill, _retryable


def _result(**kw):
    base = dict(
        status_code=200,
        success=True,
        html="<html></html>",
        error_message="",
        redirected_url=None,
        response_headers={},
        markdown=None,
        screenshot=b"\x89PNG",
    )
    return SimpleNamespace(**{**base, **kw})


def test_screenshot_is_spooled_to_disk_not_kept_in_memory(tmp_path):
    page = Page("https://example.co.za/a/", 0, None, "start")
    _fill(page, _result(), tmp_path)
    assert page.ok
    assert page.screenshot_file.startswith(str(tmp_path))
    assert Path(page.screenshot_file).read_bytes() == b"\x89PNG"
    assert not hasattr(page, "screenshot")


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
