"""SSRF guard, run budget and decompression limits (RnD Labs standard §8)."""

from __future__ import annotations

import gzip
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from site_scraper.assets import OFFSITE, Asset, _fetch, download
from site_scraper.discover import MAX_SITEMAP_BYTES, gunzip_bounded, parse_sitemap
from site_scraper.guard import Blocked, Budget, BudgetExceeded, HostPolicy, is_public


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "127.8.9.10", "10.0.0.5", "172.16.3.4", "192.168.1.50",  # private, loopback
        "169.254.169.254", "169.254.1.1",  # link-local, cloud metadata
        "100.64.0.1",  # shared (CGNAT)
        "0.0.0.0", "224.0.0.1", "239.255.255.250", "240.0.0.1", "255.255.255.255",
        "::1", "::", "fe80::1", "fc00::1", "fd00:ec2::254", "ff02::1",
        "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",  # IPv4-mapped
    ],
)  # fmt: skip
def test_non_public_addresses_are_refused(address):
    assert not is_public(address)


@pytest.mark.parametrize("address", ["8.8.8.8", "41.0.0.1", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address):
    assert is_public(address)


def _policy(**kwargs) -> HostPolicy:
    return HostPolicy(root="https://example.co.za/", **kwargs)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/", "http://localhost/", "http://[::1]/", "http://169.254.169.254/latest/",
        "http://2130706433/", "http://0x7f000001/", "http://017700000001/", "http://0/",  # 127.0.0.1
        "http://[::ffff:7f00:1]/",
    ],
)  # fmt: skip
def test_urls_to_internal_addresses_are_blocked(url):
    with pytest.raises(Blocked):
        _policy().check(url, download=False)


@pytest.mark.parametrize(
    "url",
    ["http://10.0.0.1/", "http://2130706433/", "http://0x7f000001/", "http://127.1/",
     "http://[::ffff:169.254.169.254]/", "http://169.254.169.254/"],
)  # fmt: skip
def test_ip_literals_are_checked_even_behind_the_proxy(url):
    """Behind the egress proxy there is no DNS here, but literal addresses are still refused."""
    with pytest.raises(Blocked):
        _policy(proxy="http://egress:3128").check(url, download=False)


def test_hostnames_are_left_to_the_proxy_when_there_is_one():
    _policy(proxy="http://egress:3128").check("https://example.co.za/", download=True)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.co.za/x", "gopher://x/"])
def test_only_http_and_https(url):
    with pytest.raises(Blocked):
        _policy().check(url, download=False)


def test_allowlist_for_downloads():
    policy = _policy(allow_hosts=("cdn.example.net",))
    assert policy.on_allowlist("https://example.co.za/img/a.png")
    assert policy.on_allowlist("https://www.example.co.za/img/a.png")
    assert policy.on_allowlist("https://static.wixstatic.com/media/a.jpg")
    assert policy.on_allowlist("https://images.squarespace-cdn.com/a.jpg")
    assert policy.on_allowlist("https://img.cdn.example.net/a.jpg")
    assert not policy.on_allowlist("https://tracker.example.org/pixel.gif")
    assert not policy.on_allowlist("https://example.co.za.evil.test/a.png")
    assert not policy.on_allowlist("https://notexample.co.za/a.png")


def test_off_site_download_is_blocked_before_dns():
    with pytest.raises(Blocked, match="off-site"):
        _policy().check("https://tracker.example.org/pixel.gif", download=True)


def test_loopback_allowance_is_test_only_and_exact(monkeypatch):
    monkeypatch.setenv("SITE_SCRAPER_TEST_ALLOW_LOOPBACK", "1")
    policy = HostPolicy.for_run("http://127.0.0.1:8000/")
    policy.check("http://127.0.0.1:8000/", download=True)
    with pytest.raises(Blocked):
        policy.check("http://127.0.0.2:8000/", download=False)
    with pytest.raises(Blocked):
        policy.check("http://169.254.169.254/", download=False)
    monkeypatch.delenv("SITE_SCRAPER_TEST_ALLOW_LOOPBACK")
    with pytest.raises(Blocked):
        HostPolicy.for_run("http://127.0.0.1:8000/").check("http://127.0.0.1:8000/", download=True)


# ---- the HTTP client checks every redirect hop ----------------------------------------------


class _Redirector(BaseHTTPRequestHandler):
    """/go → 302 to the metadata address; /hop → /ok; /ok → 200; /big → 2 MB."""

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        location = {"/go": "http://169.254.169.254/latest/meta-data/", "/hop": "/ok"}.get(self.path)
        if location:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"x" * (2 * 1024 * 1024) if self.path == "/big" else b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def redirector():
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Redirector))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def loopback_policy(monkeypatch, redirector):
    monkeypatch.setenv("SITE_SCRAPER_TEST_ALLOW_LOOPBACK", "1")
    return HostPolicy.for_run(redirector + "/")


async def test_redirect_to_metadata_address_is_blocked(loopback_policy, redirector):
    async with loopback_policy.http_client(follow_redirects=True) as client:
        assert (await client.get(redirector + "/ok")).text == "ok"
        with pytest.raises(Blocked, match="169.254.169.254"):
            await client.get(redirector + "/go")
        assert await _fetch(client, redirector + "/go") is None  # callers see a failed fetch


async def test_offsite_assets_are_skipped_and_listed(loopback_policy, redirector, tmp_path):
    assets = [
        Asset(url=redirector + "/ok", kind="document"),
        Asset(url="https://tracker.example.org/pixel.gif", kind="image"),
    ]
    async with loopback_policy.http_client(follow_redirects=True) as client:
        await download(assets, tmp_path, client, allowed=loopback_policy.on_allowlist)
    assert assets[0].file
    assert assets[1].error == OFFSITE
    assert not assets[1].file


# ---- the browser checks every request and every redirect hop --------------------------------


@pytest.mark.e2e
async def test_browser_cannot_reach_internal_addresses(loopback_policy, redirector):
    from playwright.async_api import async_playwright

    failed: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            await loopback_policy.install_browser_guard(context)
            page = await context.new_page()
            page.on("requestfailed", lambda r: failed.append(r.url))

            # A page on the allowed site embedding an image from another internal address.
            await page.route(
                redirector + "/page",
                lambda route: route.fulfill(
                    content_type="text/html",
                    body='<img src="http://10.0.0.1/x.png"><img src="http://127.0.0.2/y.png">',
                ),
            )
            await page.goto(redirector + "/page")
            await page.wait_for_load_state("load")
            assert "http://10.0.0.1/x.png" in failed
            assert "http://127.0.0.2/y.png" in failed

            # The allowed site redirects the browser to the metadata service: the hop is checked
            # by the handler (Playwright would otherwise follow it without asking again).
            with pytest.raises(Exception, match="ERR_BLOCKED_BY_CLIENT"):
                await page.goto(redirector + "/go")

            # An allowed redirect chain still lands on the right page and address. (A fresh tab:
            # the blocked navigation above leaves Chromium's error page committing in the old one.)
            fresh = await context.new_page()
            response = await fresh.goto(redirector + "/hop")
            assert response.status == 200
            assert fresh.url == redirector + "/ok"
        finally:
            await browser.close()


# ---- budgets ---------------------------------------------------------------------------------


async def test_byte_budget_stops_downloads(monkeypatch, redirector):
    monkeypatch.setenv("SITE_SCRAPER_TEST_ALLOW_LOOPBACK", "1")
    budget = Budget(max_bytes=1024 * 1024)
    policy = HostPolicy.for_run(redirector + "/", budget=budget)
    async with policy.http_client() as client:
        assert await _fetch(client, redirector + "/big") is None
        assert budget.hit.startswith("total download size")
        with pytest.raises(BudgetExceeded):
            await client.get(redirector + "/ok")


def test_request_budget():
    budget = Budget(max_requests=2)
    budget.count_request()
    budget.count_request()
    with pytest.raises(BudgetExceeded):
        budget.count_request()
    assert budget.exhausted
    assert "total requests" in budget.hit


def test_runtime_budget():
    budget = Budget(max_seconds=0)
    assert budget.exhausted
    assert "run time" in budget.hit


# ---- decompression bombs ---------------------------------------------------------------------


def test_gzip_bomb_is_cut_off():
    bomb = gzip.compress(b"\0" * (200 * 1024 * 1024))  # 200 MB of zeros, ~200 KB compressed
    assert len(bomb) < 1024 * 1024
    assert len(gunzip_bounded(bomb)) == MAX_SITEMAP_BYTES
    assert parse_sitemap(bomb) == ([], [])


def test_broken_gzip_is_ignored():
    assert parse_sitemap(b"\x1f\x8b" + b"not really gzip") == ([], [])


def test_blocked_is_an_httpx_error():
    """Existing `except httpx.HTTPError` handlers treat a blocked request as a failed fetch."""
    assert issubclass(Blocked, httpx.HTTPError)
