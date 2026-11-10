# RnD Labs Engineering Methodology (vendored)

`rnd-labs-methodology.md` is the RnD Labs shared engineering standard, **version 1.1**.
It is the governing standard for this repository (see `AGENTS.md` "Order of authority").

- **Do not edit it here.** Every repository carries an identical copy, checked by
  `SHA256SUMS` (`make standard-check`, and in CI). A local edit fails the check.
- **Upstream:** the template repository `Py_Django_WebstackV1` (`docs/standard/`). Client repos
  receive it by merging the template; `site-scraper` carries the same file.
- **Changing the standard:** edit it in the template only, bump the version line and the
  "Revision history" table, record why in an ADR (`docs/decisions/`), regenerate `SHA256SUMS`
  (`sha256sum rnd-labs-methodology.md > SHA256SUMS`), then propagate to every repository.
- **Deviations** are not made by editing this file. Record them in the exceptions register
  (`docs/security.md` for the template, `docs/client.md` for a client) with owner, reason,
  compensating control and expiry (standard §5).
