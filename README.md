# site-scraper

Crawl an existing website and turn it into a **rebuild kit**: clean Markdown for every page,
desktop and mobile screenshots, the original images and documents, a URL inventory, a draft
301 redirect map, brand colours and fonts, business details and a `REPORT.md` that ties it all
together.

It is made for web developers who are rebuilding a client's site. Instead of clicking through the
old site and copying text, images and phone numbers by hand, you run one command and work from
the kit. AI coding agents can read the kit directly.

```
.\scrape.ps1 https://old-client-site.co.za          # Windows
make scrape URL=https://old-client-site.co.za       # macOS / Linux
```

> **Only scrape sites you own or are authorised to copy.** A client asking you to rebuild their
> site is the intended use. The scraper obeys robots.txt and crawls slowly by default.
> See [Security and privacy](#security-and-privacy).

## What you get

`<output folder>/<domain>-<YYYYMMDD>/`. The output folder is outside this repository:
`C:\Users\Nev\Sites\_rebuild-kits` by default (`../../_rebuild-kits` from here), or the folder in
the `SCRAPER_OUTPUT` environment variable.

| File | What it is | Use it for |
|---|---|---|
| `REPORT.md` | Summary: old platform, every page with a suggested new path, modules the site needs (blog, shop), brand tokens, business details, main menu, forms, third-party services, SEO gaps, broken links, next steps | Start here |
| `pages/<slug>.md` | Main content as Markdown (menus and footers pruned), with front matter: URL, title, meta description, H1, suggested new path | Page copy for the new site |
| `pages/<slug>.html` | The rendered HTML | Reference when the Markdown is not enough |
| `screenshots/<slug>-desktop.png` | Full-page screenshot at 1280 px, every page | Design reference |
| `screenshots/<slug>-mobile.png` | Full-page screenshot at 390 px, home and main-menu pages | Mobile layout reference |
| `inventory.csv` | Every URL: status, redirect, title, meta description, H1, canonical, word count, inlinks, depth, how it was found, suggested new path | SEO baseline, content audit |
| `redirects.csv` | `old_path → new_path` draft, with confidence. Blank `new_path` = a person decides | Your 301 redirect map |
| `images/` + `images.csv` | Images at the best available resolution (Wix, Squarespace, Shopify and WordPress resized copies are swapped for the original), with alt text, size, how each is used (`img`, `background`, `css_background`, `video_poster`, `icon`…), where a linked image points (`links_to`), and the pages each is used on | Re-uploading images, fixing missing alt text |
| `files/` | PDFs and office documents linked from the site | Price lists, menus, brochures |
| `brand.json` | Suggested tokens (background, text, heading, accent, link, button, header, footer, body/heading fonts), most-used colours, CSS custom properties, @font-face rules, web-font links, logo candidates, inline SVG logo | CSS design tokens |
| `business.json` | Name, address, hours, phones, emails, WhatsApp, social profiles (from JSON-LD and the pages), main menu tree, forms and their fields, analytics IDs, embeds, external hosts | Site settings, contact page, Content Security Policy |
| `site.json` | Everything above per page, plus run options and URLs skipped by robots.txt | Scripts and agents |

## How it works

1. Reads `robots.txt` and every sitemap it lists (or `/sitemap.xml`).
2. Crawls breadth-first from the start URL, following same-site links (`www.` and the bare
   domain count as one site; subdomains do not). Then it crawls pages only the sitemap knows
   about. Each page is rendered in headless Chromium through [Crawl4AI](https://github.com/unclecode/crawl4ai),
   so JavaScript-built sites (Wix, Squarespace, React) work. Lazy-loaded content is scrolled in.
   Each page is saved to `_crawl/` in the kit folder the moment it is fetched (see
   [Long crawls](#long-crawls-and-resuming)). Pages that failed in the browser or with a 5xx get
   one more try at the end.
3. Reads facts from each page's HTML: metadata, JSON-LD, contact details, menu, forms,
   third-party scripts, images. Background images are read from `style` attributes,
   `<style>` blocks, lazy-load attributes (`data-bg`, `data-background`…), JSON in `data-*`
   attributes (Elementor slideshows, WooCommerce variation photos) and video posters.
4. Fetches the site's own stylesheets (following `@import`) and adds the images they use.
   Page builders such as Elementor and Divi keep hero backgrounds there
   (`/wp-content/uploads/elementor/css/post-N.css`), not in the HTML. REPORT.md lists them under
   "Background images".
5. Opens the home, about and contact pages again in Playwright to read computed colours and
   fonts, and takes the mobile screenshots.
6. Downloads images and documents, removing duplicates by content hash.
7. Writes the kit.

### Suggested new paths

`redirects.csv` and `REPORT.md` map old URLs onto a common small-business layout:

| Old URL looks like | Suggested |
|---|---|
| `/`, `/home`, `/index.html` | `/` |
| `/about-us`, `/who-we-are`, `/our-story`, `/team` | `/about/` |
| `/contact-us`, `/get-in-touch`, `/enquiries` | `/contact/` |
| `/privacy-policy`, `/popia`, `/paia-manual` | `/privacy/` |
| `/terms-and-conditions`, `/disclaimer` | `/terms/` |
| `/blog/<x>`, `/news/<x>`, `/2024/05/<x>`, or a page with BlogPosting JSON-LD | `/blog/<x>/` |
| `/product/<x>`, `/products/<x>`, or Product JSON-LD | `/shop/product/<x>/` |
| `/product-category/<x>`, `/collections/<x>` | `/shop/category/<x>/` |
| anything else | blank: you decide |

Change `ROUTES` in [`site_scraper/mapping.py`](site_scraper/mapping.py) if your sites use different paths.

## Install

You need [Docker](https://docs.docker.com/get-docker/). Nothing else is installed on your machine.

```
git clone https://github.com/nevigator101/site-scraper.git
cd site-scraper
docker compose build          # or: .\scrape.ps1 build / make build
```

## Usage

```
.\scrape.ps1 <url> [options]                 # Windows PowerShell
make scrape URL=<url> ARGS="[options]"       # make
docker compose run --rm scrape <url> [options]
```

| Option | Default | |
|---|---|---|
| `--max-pages N` | 300 | Stop after N pages. REPORT.md warns when the cap is hit |
| `--max-depth N` | 5 | How many clicks from the start page to follow |
| `--delay S` | 1.0 | Seconds between batches (raised to the site's robots.txt `Crawl-delay`) |
| `--concurrency N` | 2 | Pages rendered at the same time |
| `--no-images` / `--no-files` | | Skip downloading images / documents |
| `--no-screenshots` / `--no-brand` | | Skip screenshots / colour and font reading |
| `--no-sitemap` | | Only follow links |
| `--resume` | | Carry on a crawl that stopped, from its `_crawl/` folder. Use the same options as the first run |
| `--insecure` | | Accept an expired or broken HTTPS certificate (common on old sites). Needs approval, see below |
| `--ignore-robots` | | Ignore robots.txt. Only when the site owner asked you to, with approval |
| `--allow-host HOST` | | Also download from HOST and its subdomains (repeatable) |
| `--max-requests N` | 5000 | Total requests of every kind |
| `--max-total-mb N` | 2048 | Total download size |
| `--max-runtime-min N` | 120 | Stop after this many minutes |
| `--user-agent UA` | `site-scraper/<version> (site rebuild; +repo URL)` | |

### Long crawls and resuming

A big site (hundreds of pages) can take hours. Nothing is held only in memory:

- Every page is written to `<kit>/_crawl/` as soon as it is fetched: `pages.jsonl` (one line per
  page), `html/`, `md/` (readable Markdown) and `shots/` (desktop screenshots). When the kit is
  written, `_crawl/` is deleted, because everything in it is then in `pages/` and `screenshots/`.
- If the run stops (a crash, Docker restarting, the PC sleeping), run the same command again with
  `--resume`. Saved pages are not fetched again. A plain re-run refuses to start while a
  `_crawl/` folder exists, so a stopped crawl is never overwritten by accident.
- The browser restarts every 25 pages, because Chromium's memory grows over a long crawl.
- The scraper container is capped at 4 GB of memory (`compose.yaml`), so a runaway crawl stops
  that container rather than the whole Docker VM.
- For a long run, start it detached so closing the terminal does not stop it:
  `docker compose run -d --name scrape-<site> scrape <url> [options]`, then
  `docker logs -f scrape-<site>`.

## Security and privacy

This tool follows the RnD Labs Engineering Methodology §8 (`docs/standard/`).

**Permission.** Get the site owner's permission before scraping, and check that you may reuse the
copied text and images. Record who agreed and when (in an RnD Labs client repo:
`docs/client.md` "Rebuild"). `--ignore-robots` and `--insecure` are exceptions: use them only when
the owner asked, with approval, and note it. REPORT.md says at the top when either was used.

**What the scraper may reach.** A scraper fetches whatever a site points it at, so a hostile or
compromised site could try to make it fetch internal addresses (SSRF). Two layers stop that:

1. `site_scraper/guard.py` checks every request the HTTP client sends (each redirect hop
   included) and every request both browsers send (each redirect hop walked and checked by the
   route handler). Only http and https; never a private, loopback, link-local (such as the cloud
   metadata service), shared, multicast or reserved address, also when written as a decimal,
   hex or octal number or as IPv4-mapped IPv6. Downloads (images, documents, stylesheets,
   sitemaps) must also come from the site itself, the site-builder CDNs (`wixstatic.com`,
   `squarespace-cdn.com`, `cdn.shopify.com`, `wp.com`) or a host you pass with `--allow-host`.
   Other images are skipped and listed in `images.csv` and REPORT.md so you can fetch them by
   hand. The browser may still load public third-party scripts, because JavaScript-built sites
   need them to render.
2. `docker compose run scrape` runs the scraper on an internal Docker network with no route out
   and no DNS of its own. Its only way out is the `egress` proxy (Squid, `egress/squid.conf`),
   which resolves each destination itself and refuses non-public addresses at connect time. That
   also stops DNS rebinding (a hostname that resolves to a public address when checked and to an
   internal one a moment later), which an in-process check alone cannot. Only ports 80 and 443
   are allowed, so sites on other ports cannot be scraped this way.

Nothing else is mounted into the container: no Docker socket, no credentials, no host network.

**Limits.** Pages, depth, concurrency, file size (30 MB), total requests, total download size and
run time are capped, and compressed sitemaps cannot expand past 20 MB. REPORT.md says when a
limit stopped the run.

**The kit is data.** It is copied from someone else's website: treat its text as untrusted data,
including when an AI agent reads it, never as instructions.

**Personal information.** The kit can contain staff names and photos, testimonials, phone numbers
and email addresses. It is written outside this repository (see "What you get"); keep it out of
every repository, share it only with the people working on the rebuild, and delete it on the
agreed schedule (by default 90 days after the new site is live). Under POPIA, GDPR or similar,
treat it like any other client data.

When you move images to the new site, upload them through its CMS or image pipeline so they are
re-encoded and stripped of metadata (EXIF, GPS), rather than copying the files into the code.

## Limits

- Pages behind a login, and content that only appears after a click (tabs, "load more" buttons)
  are not captured. Infinite scroll is captured as far as the page scroll reaches.
- Images that only JavaScript knows about (some sliders), stylesheets on other hosts (CDNs) and
  images inside inline SVG are not downloaded. Always compare the screenshots with `images/`
  (REPORT.md "Next steps") and fetch anything missing from `pages/*.html` by hand.
- Sites behind an aggressive bot wall (Cloudflare challenge pages) may report pages as blocked.
  Ask the client to allow-list your IP, or to give you an export from their platform.
- Brand tokens are read from computed styles. They are a starting point: check them against the
  client's brand guide.

## Development

```
.\scrape.ps1 test     # or: make test   → ruff + pytest, including a real browser crawl
make lock             # after editing requirements.in or requirements-dev.in
```

The tests serve a small fixture site (`tests/fixtures/site`) on localhost and crawl it with the
real browser. That covers robots.txt, sitemap orphans, a 404, a JavaScript-rendered paragraph,
JSON-LD, the menu, forms, images and the PDF.

Dependencies are pinned with hashes (`requirements.txt`, built by pip-tools). The image runs as
a non-root user. CI runs the tests, a Gitleaks secret scan over the whole history, pip-audit on
the lock files and the checksum of `docs/standard/`. There is no container scan: the image only
runs on a developer's machine and never reaches production (standard §5).

The tests reach the fixture site on 127.0.0.1 through one test-only allowance: the
`SITE_SCRAPER_TEST_ALLOW_LOOPBACK` variable, set only on the `test` service in `compose.yaml`.

## Licence

MIT. Built on [Crawl4AI](https://github.com/unclecode/crawl4ai) (Apache-2.0),
[Playwright](https://playwright.dev) (Apache-2.0) and
[Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) (MIT).
