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

## What you get

`output/<domain>-<YYYYMMDD>/`

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
3. Reads facts from each page's HTML: metadata, JSON-LD, contact details, menu, forms,
   third-party scripts, images. Background images are read from `style` attributes,
   `<style>` blocks, lazy-load attributes (`data-bg`, `data-background`…), page-builder settings
   (Elementor slideshows) and video posters.
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
| `--insecure` | | Accept an expired or broken HTTPS certificate (common on old sites) |
| `--ignore-robots` | | Ignore robots.txt. Only when the site owner asked you to |
| `--user-agent UA` | `site-scraper/<version> (site rebuild; +repo URL)` | |

## Privacy

The kit can contain personal information: staff names and photos, testimonials, phone numbers
and email addresses. `output/` is git-ignored. Keep it out of version control, share it only with
the people working on the rebuild, and delete it when the new site is live. If you work under
POPIA, GDPR or similar, treat it like any other client data.

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
a non-root user.

## Licence

MIT. Built on [Crawl4AI](https://github.com/unclecode/crawl4ai) (Apache-2.0),
[Playwright](https://playwright.dev) (Apache-2.0) and
[Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) (MIT).
