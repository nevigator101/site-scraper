"""Brand tokens (colours, fonts, logo) and mobile screenshots, read from the rendered pages."""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import Browser, async_playwright
from playwright.async_api import Error as PlaywrightError

log = logging.getLogger(__name__)

# Runs in the page. Collects computed colours (weighted by visible area), fonts, CSS custom
# properties, @font-face rules, Google Fonts links and logo candidates.
BRAND_JS = r"""
() => {
  const toHex = (c) => {
    const m = c && c.match(/rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)(?:[,\s/]+([\d.]+%?))?/);
    if (!m) return null;
    if (m[4] !== undefined) {
      const a = m[4].endsWith('%') ? parseFloat(m[4]) / 100 : parseFloat(m[4]);
      if (a < 0.5) return null;
    }
    return '#' + [m[1], m[2], m[3]].map(n => (+n).toString(16).padStart(2, '0')).join('');
  };
  const add = (map, key, weight) => { if (key) map[key] = (map[key] || 0) + weight; };
  const visible = (el, r) => {
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity) > 0.1;
  };
  const text = {}, backgrounds = {}, borders = {};
  const els = Array.from(document.body ? document.body.querySelectorAll('*') : []).slice(0, 6000);
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (!visible(el, r)) continue;
    const s = getComputedStyle(el);
    const area = Math.min(r.width * r.height, 1920 * 1080);
    add(backgrounds, toHex(s.backgroundColor), area);
    const ownText = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());
    if (ownText) add(text, toHex(s.color), el.textContent.trim().length);
    if (parseFloat(s.borderTopWidth) > 0) add(borders, toHex(s.borderTopColor), r.width);
  }
  const styleOf = (selector) => {
    const el = document.querySelector(selector);
    if (!el) return null;
    const s = getComputedStyle(el);
    return {
      selector, color: toHex(s.color), background: toHex(s.backgroundColor),
      font_family: s.fontFamily, font_size: s.fontSize, font_weight: s.fontWeight,
      line_height: s.lineHeight, letter_spacing: s.letterSpacing,
      text_transform: s.textTransform, border_radius: s.borderRadius,
    };
  };
  const roles = {
    body: styleOf('body'),
    h1: styleOf('h1'), h2: styleOf('h2'), h3: styleOf('h3'),
    link: styleOf('main p a[href]:not([class*="button" i]):not([class*="btn" i])')
      || styleOf('p a[href]:not([class*="button" i]):not([class*="btn" i])')
      || styleOf('main a[href]:not([class*="button" i]):not([class*="btn" i])')
      || styleOf('a[href]'),
    button: styleOf('a.button, a.btn, .btn, button:not([aria-hidden]), [class*="button"] a, a[class*="button"], input[type=submit]'),
    header: styleOf('header') || styleOf('[role=banner]'),
    footer: styleOf('footer') || styleOf('[role=contentinfo]'),
    nav_link: styleOf('header nav a, nav a'),
  };
  const fonts = {};
  for (const el of els.slice(0, 3000)) {
    if (!el.textContent || !el.textContent.trim()) continue;
    const family = getComputedStyle(el).fontFamily;
    add(fonts, family, 1);
  }
  const cssVars = {}, fontFaces = [];
  for (const sheet of Array.from(document.styleSheets)) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    for (const rule of Array.from(rules || [])) {
      if (rule.type === 5) {
        fontFaces.push({
          family: rule.style.getPropertyValue('font-family').replace(/["']/g, '').trim(),
          weight: rule.style.getPropertyValue('font-weight'),
          style: rule.style.getPropertyValue('font-style'),
          src: rule.style.getPropertyValue('src').slice(0, 500),
        });
      } else if (rule.selectorText === ':root' || rule.selectorText === 'html' || rule.selectorText === 'body') {
        for (const name of Array.from(rule.style)) {
          if (name.startsWith('--') && Object.keys(cssVars).length < 200) {
            cssVars[name] = rule.style.getPropertyValue(name).trim().slice(0, 200);
          }
        }
      }
    }
  }
  const fontLinks = Array.from(document.querySelectorAll('link[href*="fonts.googleapis"], link[href*="use.typekit"], link[href*="fonts.bunny"]')).map(l => l.href);
  const logos = [];
  const seen = new Set();
  const header = document.querySelector('header, [role=banner]') || document.body;
  const candidates = Array.from(document.querySelectorAll(
    'img[src*="logo" i], img[alt*="logo" i], img[class*="logo" i], [class*="logo" i] img, [id*="logo" i] img, a[href="/"] img, header img'
  ));
  for (const img of candidates) {
    const src = img.currentSrc || img.src;
    if (!src || seen.has(src)) continue;
    seen.add(src);
    const r = img.getBoundingClientRect();
    logos.push({ src, alt: img.alt || '', width: Math.round(r.width), height: Math.round(r.height),
                 in_header: header.contains(img) });
    if (logos.length >= 5) break;
  }
  const svgLogo = header.querySelector('a[href="/"] svg, [class*="logo" i] svg');
  const top = (map, n) => Object.entries(map).sort((a, b) => b[1] - a[1]).slice(0, n)
    .map(([value, weight]) => ({ value, weight: Math.round(weight) }));
  return {
    text_colors: top(text, 8), background_colors: top(backgrounds, 8), border_colors: top(borders, 5),
    fonts: top(fonts, 6), roles, css_variables: cssVars, font_faces: fontFaces.slice(0, 40),
    font_links: fontLinks, logos, inline_svg_logo: svgLogo ? svgLogo.outerHTML.slice(0, 20000) : '',
    theme_color: (document.querySelector('meta[name=theme-color]') || {}).content || '',
  };
}
"""


async def _brand_for(browser: Browser, url: str, user_agent: str, policy, insecure: bool) -> dict:
    context = await browser.new_context(
        user_agent=user_agent, viewport={"width": 1280, "height": 900}, ignore_https_errors=insecure
    )
    try:
        if policy is not None:
            await policy.install_browser_guard(context)
        page = await context.new_page()
        await page.goto(url, wait_until="load", timeout=60_000)
        await page.wait_for_timeout(1500)
        return await page.evaluate(BRAND_JS)
    finally:
        await context.close()


async def _mobile_shot(
    browser: Browser, url: str, target: Path, user_agent: str, policy, insecure: bool
) -> bool:
    context = await browser.new_context(
        user_agent=user_agent,
        viewport={"width": 390, "height": 844},
        device_scale_factor=2,
        is_mobile=True,
        has_touch=True,
        ignore_https_errors=insecure,
    )
    try:
        if policy is not None:
            await policy.install_browser_guard(context)
        page = await context.new_page()
        await page.goto(url, wait_until="load", timeout=60_000)
        await page.wait_for_timeout(1000)
        target.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(target), full_page=True)
        return True
    finally:
        await context.close()


async def collect(
    brand_urls: list[str],
    mobile_shots: dict[str, Path],
    user_agent: str,
    policy=None,
    insecure: bool = False,
) -> tuple[dict, list[str]]:
    """Brand data per URL, and the list of mobile screenshots that were written.

    `policy` (guard.HostPolicy) checks every request the browser sends."""
    brand: dict = {}
    written: list[str] = []
    async with async_playwright() as p:
        proxy = policy.browser_proxy if policy is not None else None
        browser = await p.chromium.launch(headless=True, proxy=proxy)
        try:
            for url in brand_urls:
                try:
                    brand[url] = await _brand_for(browser, url, user_agent, policy, insecure)
                except PlaywrightError as exc:
                    log.warning("brand read failed for %s: %s", url, exc)
            for url, target in mobile_shots.items():
                try:
                    if await _mobile_shot(browser, url, target, user_agent, policy, insecure):
                        written.append(url)
                except PlaywrightError as exc:
                    log.warning("mobile screenshot failed for %s: %s", url, exc)
        finally:
            await browser.close()
    return brand, written


def summarise(per_page: dict) -> dict:
    """Merge per-page brand data into one palette + type summary, home page first."""
    if not per_page:
        return {}
    pages = list(per_page.values())
    first = pages[0]

    def merged(key: str, n: int) -> list[dict]:
        totals: dict[str, int] = {}
        for page in pages:
            for item in page.get(key, []):
                totals[item["value"]] = totals.get(item["value"], 0) + item["weight"]
        return [{"value": v, "weight": w} for v, w in sorted(totals.items(), key=lambda x: -x[1])][
            :n
        ]

    roles = first.get("roles", {})
    accent_candidates = [
        (roles.get("button") or {}).get("background"),
        (roles.get("link") or {}).get("color"),
        (roles.get("h1") or {}).get("color"),
    ]
    neutral = {"#000000", "#ffffff", None}
    accent = next((c for c in accent_candidates if c not in neutral), None)
    return {
        "suggested_tokens": {
            "background": (roles.get("body") or {}).get("background") or "#ffffff",
            "text": (roles.get("body") or {}).get("color"),
            "heading": (roles.get("h1") or roles.get("h2") or {}).get("color"),
            "accent": accent,
            "link": (roles.get("link") or {}).get("color"),
            "button_background": (roles.get("button") or {}).get("background"),
            "button_text": (roles.get("button") or {}).get("color"),
            "header_background": (roles.get("header") or {}).get("background"),
            "footer_background": (roles.get("footer") or {}).get("background"),
            "font_body": (roles.get("body") or {}).get("font_family"),
            "font_heading": (roles.get("h1") or roles.get("h2") or {}).get("font_family"),
            "theme_color": first.get("theme_color", ""),
        },
        "text_colors": merged("text_colors", 8),
        "background_colors": merged("background_colors", 8),
        "border_colors": merged("border_colors", 5),
        "fonts": merged("fonts", 6),
        "roles": roles,
        "css_variables": first.get("css_variables", {}),
        "font_faces": first.get("font_faces", []),
        "font_links": sorted({link for page in pages for link in page.get("font_links", [])}),
        "logos": first.get("logos", []),
        "inline_svg_logo": first.get("inline_svg_logo", ""),
    }
