# syntax=docker/dockerfile:1
# site-scraper image: Python 3.13 + locked dependencies + Playwright Chromium. Runs as non-root.
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    CRAWL4_AI_BASE_DIRECTORY=/home/scraper \
    CRAWL4AI_NO_CLOUD_NOTICE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 scraper \
    && mkdir -p /app/output && chown scraper:scraper /app/output

COPY site_scraper ./site_scraper

USER scraper
ENTRYPOINT ["python", "-m", "site_scraper"]
CMD ["--help"]

# Test image: the runtime plus pytest and ruff. Source is mounted by compose.yaml.
FROM runtime AS test
USER root
COPY requirements-dev.txt .
RUN pip install --require-hashes -r requirements-dev.txt
# The source is mounted read-mostly: no caches written into it.
ENV RUFF_NO_CACHE=true
USER scraper
ENTRYPOINT []
CMD ["sh", "-c", "ruff check . && ruff format --check . && pytest"]
