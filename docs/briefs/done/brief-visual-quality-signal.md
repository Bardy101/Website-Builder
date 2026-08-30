**Completed:** 2026-08-30 — Staleness detection replaces PageSpeed as the quality signal; mobile score is now a tiebreaker only. Screenshot capture and a self-contained contact sheet added, with keep/cull toggles exporting decisions.json into `cull.py --import`. All six acceptance criteria verified. Deviations: (a) the `unknown` site_verdict bucket is kept for sites that could not be fetched — the brief's list has no bucket for that case, and calling an unreachable site `fine` would bury a possible prospect on no evidence; (b) `staleness_points` counts `no_https` alongside the five signals marked new, so the subtotal and the verdict's signal count use one definition and cannot disagree; (c) `pipeline/screenshots.py` falls back to a pre-installed Chromium when Playwright's bundled build is absent, which this project's remote sandbox needs and is a no-op on a normal install.

---

# Implementation Brief — Visual Quality Signal (Phase 0 amendment)

**For:** Claude Code
**Target repo:** prospect finder (`find.py` and friends)
**Companion doc:** `postal-pipeline-build-spec.md` §2, §4
**Status:** ready to implement

---

## Problem statement

The current lead score uses PageSpeed mobile score as a proxy for website
quality. It is not one. PageSpeed measures page weight and technical hygiene;
neither correlates with visual quality, and in practice they anti-correlate,
because good design costs bytes.

Observed in the first live run: a dated, sparse, visually poor site scores well
on mobile (little to load) and therefore ranks *low* as a prospect; a
well-designed modern site with hero imagery, custom fonts, an embedded map and
a booking widget scores badly and therefore ranks *high*. The signal is
inverted. The top-ranked candidate on every run so far has been a site the
operator judged genuinely good — a `site_fine` rejection.

Visual quality is the thing being sold and is the one attribute neither the
Places API nor PageSpeed can observe.

## Fix, in two parts

1. **Reweight** — demote mobile score to a tiebreaker; promote signals that
   detect *staleness* rather than *slowness*.
2. **Screenshot + contact sheet** — capture every candidate site and let the
   operator cull visually. This is the primary cull mechanism; the score's job
   is only to produce a sane ordering for the eye to work through.

Both are required. Ranking and culling are different jobs: screenshots cannot
rank, metrics cannot judge appearance.

---

## Part 1 — Scoring changes

### 1.1 New module: `staleness.py`

Fetch each candidate's homepage HTML once (requests, 10s timeout, browser
User-Agent, follow redirects, cap body read at ~2MB) and parse with
BeautifulSoup. Reuse the existing fetch if one already happens in the enrich
stage — do not fetch twice.

Expose `analyse_staleness(url) -> dict` returning:

| Key | Type | Detection |
|---|---|---|
| `has_viewport` | bool | `<meta name="viewport">` present |
| `has_https` | bool | final URL scheme after redirects is `https` |
| `copyright_year` | int or None | max 4-digit year in a footer-ish node matching `(c)`, `&copy;`, `copyright` |
| `copyright_age` | int or None | current year minus `copyright_year` |
| `uses_table_layout` | bool | any `<table>` containing 3+ `<tr>` and no `<th>`, outside `<article>` |
| `has_media_queries` | bool | `@media` in any inline `<style>` or first-party stylesheet (fetch up to 3, 5s each, fail open to True) |
| `has_flash` | bool | `<embed>`/`<object>` referencing `.swf`, or `application/x-shockwave-flash` |
| `platform_hint` | str or None | one of `wix`, `squarespace`, `wordpress`, `godaddy`, `weebly`, `framer`, `webflow`, `shopify`, from generator meta tag or asset URL patterns |
| `fetch_ok` | bool | request succeeded and returned HTML |

All checks fail open (absent evidence never adds points) so a fetch failure
never inflates a score.

### 1.2 Revised score weights

Replace the current weights in the discover stage. Old values in the spec's §4
table are superseded.

| Signal | Points | Note |
|---|---|---|
| No website at all | +40 | unchanged — still the strongest buy signal |
| Social-only presence (Facebook page as website) | +30 | unchanged |
| No viewport meta tag | +25 | **new** — best single tell for a pre-2013 build |
| No media queries in CSS | +15 | **new** — non-responsive |
| Copyright year 3+ years stale | +15 | **new** — `copyright_age >= 3` |
| Table-based layout | +15 | **new** |
| No HTTPS | +15 | unchanged |
| Flash present | +10 | **new** — rare, decisive when found |
| Established review base (20+ reviews, 4.0+) | +15 | unchanged — signals a real business worth approaching |
| Fewer than 5 reviews | −20 | unchanged |
| Recent review activity (within 90 days) | +5 | unchanged |
| Mobile PageSpeed score | **tiebreaker only** | see below |

**Mobile score handling.** No longer contributes to `lead_score`. Retain
`mobile_score` as a CSV column for reference, and use it only to break ties:
sort by `lead_score` descending, then `mobile_score` ascending. Sites scoring
below 30 with *no* staleness flags set are almost certainly rich modern sites,
not neglected ones — do not let that case bubble up.

**Platform-hint adjustments.** A recognised modern managed platform
(`squarespace`, `framer`, `webflow`, `shopify`) implies someone paid for and
maintains the site. Apply −15. `wix`, `godaddy`, `weebly` imply a DIY build and
are neutral, 0. `wordpress` is neutral (spans both extremes).

### 1.3 New CSV columns

Append to the existing `find.py` output, after `site_verdict`:

`has_viewport`, `has_media_queries`, `copyright_year`, `uses_table_layout`,
`has_flash`, `platform_hint`, `staleness_points`, `screenshot_path`

`staleness_points` is the subtotal contributed by part 1.2's new signals —
makes the score auditable at a glance during the cull.

### 1.4 Revised `site_verdict` derivation

Current buckets (`none` / `social_only` / `poor` / `dated` / `fine`) stay, but
derive `dated` and `poor` from staleness rather than mobile score:

- `none` — no website field
- `social_only` — website is a Facebook/Instagram URL
- `dated` — 2+ staleness signals set
- `poor` — exactly 1 staleness signal set
- `fine` — 0 staleness signals set

`fine` rows are still emitted, not dropped — the contact sheet is the arbiter,
and a false negative is cheaper than a missed prospect.

---

## Part 2 — Screenshots and contact sheet

### 2.1 Capture, in the discover stage

Playwright (already a dependency for the tier 2 preview work). For each
candidate with a website:

- Desktop: 1440x900, `screenshot_desktop.png`
- Mobile: 390x844, `screenshot_mobile.png`
- Above-the-fold only (no full-page capture — the hero is what's being judged)
- `wait_until="networkidle"`, 15s timeout; on timeout fall back to
  `domcontentloaded` plus a 2s settle, then capture whatever rendered
- Dismiss cookie banners best-effort: click the first visible element matching
  a small list of common accept-button selectors, 1s timeout, ignore failure
- On total failure write no file and set `screenshot_path` empty; record the
  reason in the run log

Run captures concurrently, 4 at a time. A 25-site run should finish in about
two minutes.

**Caching is required.** Store screenshots under the existing 30-day place
cache keyed by `place_id`, so re-runs are instant and only new candidates are
captured. Add `--refresh-screenshots` to force recapture.

### 2.2 Contact sheet generator

New script: `contactsheet.py --batch <batch_dir> [--sort score|name] [--min-score N]`

Emits a single self-contained `contactsheet.html` in the batch directory:

- Responsive grid, roughly 4 across on a laptop
- Each cell: mobile screenshot thumbnail, business name, town, `lead_score`,
  `site_verdict`, `staleness_points`, and the live site URL as a link
- Sorted by `lead_score` descending by default
- Click a thumbnail to open the desktop screenshot in a lightbox
- Cells for `site_verdict == "none"` show a placeholder tile reading NO WEBSITE
- Base64-inline the thumbnails (resized to ~400px wide) so the file is a single
  portable artefact that can be opened or sent anywhere

**Cull controls in the sheet.** Each cell gets keep/cull toggle buttons and a
reason-code dropdown using the existing fixed codes (`chain`, `winding_down`,
`site_fine`, `too_small`, `wrong_niche`, `duplicate`, `no_owner_signal`,
`gut`). State persists to `localStorage` keyed by batch. An "Export decisions"
button downloads `decisions.json` — an array of `{place_id, decision, reason}`.

`cull.py` gains `--import decisions.json` to apply that file to the batch,
writing `_approved/` and `_rejected/` as it already does. No server, no
framework — one HTML file and a JSON download.

Target: 25 candidates culled in under five minutes.

---

## Acceptance criteria

1. Re-running `find.py` against the existing first-run batch no longer places
   the known-good site at rank 1.
2. Every row carries the new staleness columns and a screenshot path (or a
   logged reason for absence).
3. `contactsheet.html` opens standalone with no network access and shows every
   candidate.
4. Decisions exported from the sheet import cleanly into `cull.py`.
5. A second run against the same batch completes without re-capturing any
   screenshot.
6. Sites that fail to fetch or screenshot never gain points from the failure.

## Out of scope

- Claude-based visual scoring of screenshots. The operator's eye on a contact
  sheet is faster to build and more reliable. Revisit only if batch volume
  outgrows manual culling.
- `tune.py` weight-diff automation — still deferred until after batch two.
