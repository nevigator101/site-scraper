"""SSRF guard and run budget (RnD Labs standard §8; README "Security and privacy").

Every request the scraper makes passes `HostPolicy`, the HTTP client's on each redirect hop and
the browser's on every request it sends:

- only http and https;
- the host must resolve to public addresses only: private, loopback, link-local (including the
  cloud metadata service), shared (CGNAT), multicast, reserved and unspecified addresses are
  refused, also when written as decimal or octal numbers or as IPv4-mapped IPv6;
- downloads (images, documents, stylesheets, sitemaps) must also be on the allowlist: the site
  being scraped, the site-builder CDNs it serves originals from, and any `--allow-host`.

The browser may load public third-party scripts, because JavaScript-built sites need them to
render; it never reaches a non-public address. The check resolves DNS just before the request,
so a hostile DNS server could still answer differently a moment later (rebinding); the egress
proxy in compose.yaml closes that gap by checking the address it actually connects to.

`Budget` caps total requests, total bytes and run time across the whole run.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from site_scraper.urls import bare_host

log = logging.getLogger(__name__)

# Hosts that serve a site's original images when the site is on a hosted builder
# (see assets.original_candidates). Subdomains match too (static.wixstatic.com).
BUILDER_CDNS = ("wixstatic.com", "squarespace-cdn.com", "cdn.shopify.com", "wp.com")
# Test-only: the end-to-end test serves its fixture site on 127.0.0.1. Set by compose.yaml's
# `test` service; never a command-line option.
TEST_LOOPBACK_ENV = "SITE_SCRAPER_TEST_ALLOW_LOOPBACK"
PROXY_ENV = "SCRAPER_PROXY"  # set by compose.yaml's scrape service: http://egress:3128
_METADATA = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
REDIRECTS = {301, 302, 303, 307, 308}
MAX_HOPS = 10


class Blocked(httpx.RequestError):
    """A request the policy refuses. Subclasses httpx's error so callers treat it as a failure."""


class BudgetExceeded(Blocked):
    """The run reached one of its limits (requests, bytes or time)."""


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip in _METADATA or (ip.version == 4 and ip in _CGNAT):
        return False
    return ip.is_global and not (
        ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback
    )


def _ip_literal(host: str):
    """The address a host names directly, including the legacy IPv4 spellings browsers and
    resolvers accept (2130706433, 0x7f000001, 017700000001, 127.1); None for a hostname."""
    host = host.strip("[]")
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if host and all(c in "0123456789abcdefxABCDEFX." for c in host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    return None


@dataclass
class Budget:
    max_requests: int = 5000
    max_bytes: int = 2048 * 1024 * 1024
    max_seconds: float = 120 * 60
    requests: int = 0
    bytes: int = 0
    started: float = field(default_factory=time.monotonic)
    hit: str = ""  # which limit stopped the run, for REPORT.md

    @property
    def exhausted(self) -> bool:
        if not self.hit and time.monotonic() - self.started > self.max_seconds:
            self.hit = f"run time ({self.max_seconds / 60:.0f} minutes)"
        return bool(self.hit)

    def count_request(self) -> None:
        if self.exhausted:
            raise BudgetExceeded(f"budget reached: {self.hit}")
        self.requests += 1
        if self.requests > self.max_requests:
            self.hit = f"total requests ({self.max_requests})"
            raise BudgetExceeded(f"budget reached: {self.hit}")

    def count_bytes(self, n: int) -> None:
        self.bytes += n
        if self.bytes > self.max_bytes and not self.hit:
            self.hit = f"total download size ({self.max_bytes // (1024 * 1024)} MB)"
        if self.hit:
            raise BudgetExceeded(f"budget reached: {self.hit}")


@dataclass
class HostPolicy:
    root: str
    allow_hosts: tuple[str, ...] = ()
    allow_loopback: bool = False
    budget: Budget = field(default_factory=Budget)
    # The egress proxy (compose.yaml). When set, all traffic goes through it, the container has
    # no DNS of its own, and the proxy refuses non-public addresses at connect time; this policy
    # still checks schemes, IP literals, the allowlist, every redirect hop and the budget.
    proxy: str = ""

    @classmethod
    def for_run(cls, root: str, allow_hosts=(), budget: Budget | None = None) -> HostPolicy:
        return cls(
            root=root,
            allow_hosts=tuple(h.lower().strip(".") for h in allow_hosts if h),
            allow_loopback=os.environ.get(TEST_LOOPBACK_ENV) == "1",
            budget=budget or Budget(),
            proxy=os.environ.get(PROXY_ENV, ""),
        )

    # ---- host and address rules -------------------------------------------------------------

    def on_allowlist(self, url: str) -> bool:
        host = bare_host(urlsplit(url).hostname or "")
        names = (bare_host(urlsplit(self.root).hostname or ""), *BUILDER_CDNS, *self.allow_hosts)
        return any(host == n or host.endswith("." + n) for n in names if n)

    def _address_allowed(self, address: str) -> bool:
        if is_public(address):
            return True
        return self.allow_loopback and address == "127.0.0.1"

    def _check_scheme(self, url: str) -> tuple[str, int]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise Blocked(f"blocked (not an http(s) URL): {url}")
        return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)

    def _check_addresses(self, url: str, infos) -> None:
        addresses = {info[4][0] for info in infos}
        bad = sorted(a for a in addresses if not self._address_allowed(a))
        if not addresses or bad:
            raise Blocked(f"blocked (non-public address {', '.join(bad) or 'none'}): {url}")

    def _pre_dns(self, url: str, download: bool) -> tuple[str, int] | None:
        """Checks that need no DNS. Returns (host, port) when DNS must still be checked here."""
        host, port = self._check_scheme(url)
        if download and not self.on_allowlist(url):
            raise Blocked(f"blocked (off-site host, not on the allowlist): {url}")
        literal = _ip_literal(host)
        if literal is not None:
            self._check_addresses(url, [(None, None, None, None, (str(literal), port))])
            return None
        if self.proxy:
            return None  # the egress proxy resolves and refuses non-public addresses itself
        return host, port

    def check(self, url: str, *, download: bool) -> None:
        """Raise Blocked unless `url` may be requested (synchronous DNS)."""
        target = self._pre_dns(url, download)
        if target is None:
            return
        try:
            infos = socket.getaddrinfo(*target, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise Blocked(f"blocked (cannot resolve): {url}") from exc
        self._check_addresses(url, infos)

    async def acheck(self, url: str, *, download: bool) -> None:
        """Raise Blocked unless `url` may be requested (DNS without blocking the event loop)."""
        target = self._pre_dns(url, download)
        if target is None:
            return
        host, port = target
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise Blocked(f"blocked (cannot resolve): {url}") from exc
        self._check_addresses(url, infos)

    # ---- the HTTP client --------------------------------------------------------------------

    def http_client(self, **kwargs) -> httpx.AsyncClient:
        """An httpx client whose every request, including each redirect hop, is checked."""

        async def on_request(request: httpx.Request) -> None:
            try:
                self.budget.count_request()
                await self.acheck(str(request.url), download=True)
            except Blocked as exc:
                log.info("%s", exc)
                raise type(exc)(str(exc), request=request) from None

        client = httpx.AsyncClient(
            event_hooks={"request": [on_request]}, proxy=self.proxy or None, **kwargs
        )
        client.scraper_budget = self.budget  # read by assets._fetch to count bytes
        return client

    # ---- the browser ------------------------------------------------------------------------

    async def install_browser_guard(self, context) -> None:
        """Check every request a Playwright browser context sends, and every redirect hop.

        Playwright calls a route handler only for the first URL of a redirect chain, and the
        browser follows a redirect it is handed WITHOUT asking the handler again. So the handler
        walks the chain itself, one checked hop at a time (`max_redirects=0`):

        - a subresource gets the final response straight from the handler: the browser never
          touches the network for it;
        - a navigation that redirected gets one redirect to the final, already-checked URL, so
          the page has the right address and relative links. The browser fetches that URL
          itself; a server that answers it differently the second time is stopped by the
          egress proxy (compose.yaml), not by this handler.
        """

        async def handle(route) -> None:
            request = route.request
            current = request.url
            if not current.startswith(("http://", "https://")):
                await route.continue_()  # data:, blob: and about: stay inside the browser
                return
            hops = 0
            try:
                while True:
                    self.budget.count_request()
                    await self.acheck(current, download=False)
                    response = await route.fetch(url=current, max_redirects=0)
                    location = response.headers.get("location")
                    if response.status in REDIRECTS and location and hops < MAX_HOPS:
                        current = urljoin(current, location)
                        hops += 1
                        continue
                    break
            except Blocked as exc:
                log.info("browser %s", exc)
                await route.abort("blockedbyclient")
                return
            except Exception as exc:  # noqa: BLE001 (network errors: the browser sees a failure)
                log.debug("browser fetch failed %s: %s", current, exc)
                await route.abort("failed")
                return
            if hops and request.is_navigation_request():
                await route.fulfill(status=302, headers={"location": current}, body="")
                return
            body = await response.body()
            try:
                self.budget.count_bytes(len(body))
            except BudgetExceeded:
                await route.abort("blockedbyclient")
                return
            await route.fulfill(response=response, body=body)

        await context.route("**/*", handle)

    @property
    def browser_proxy(self) -> dict | None:
        """Playwright's `proxy` option: the browser and route.fetch go through the egress proxy."""
        return {"server": self.proxy} if self.proxy else None
