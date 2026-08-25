# Postal Outreach Pipeline

Turn a niche + area into a folder of print-ready, personalised letters, with a
human approval gate before anything is printed.

**This repo currently implements Phase 0: the prospect finder.** It stands
alone, needs nothing else to exist, and permanently removes the job of
trawling the internet for candidates. Phases 1–2 (letter copy, mockups, PDFs)
are specified in [`BUILD-SPEC.md`](BUILD-SPEC.md) and not yet built.

```
discover  →  enrich  →  generate  →  preview  →  approve  →  print  →  post  →  track
└──────── built ────────┘  └────────────── not yet built ──────────────┘
```

---

## What Phase 0 does

One command → a ranked spreadsheet of candidate businesses with everything you
need to decide, worst web presence first.

```bash
./find.py --niche physiotherapist --area "Hitchin" --radius 8000 --top 25
```

It writes `shortlist.csv` and one `business.json` per row. Your job is then to
open the CSV, click the two links on each row, and cull 25 down to 10–15.
That sift is the point: you're clicking through a prepared list, not searching.

**What it deliberately does not do:** no letters, no mockups, no emails, no
publishing. It finds and it scores.

---

## Get the project

These scripts are **not standalone downloads**. `find.py` imports the
`pipeline/` package and reads `weights.json`, so it needs the whole folder.
Downloading one file on its own gives you `ModuleNotFoundError: No module
named 'pipeline'`.

```bash
git clone -b claude/tool-build-markdown-spec-vnr7ss \
    https://github.com/Bardy101/Website-Builder.git
cd Website-Builder
pip install -r requirements.txt
```

<details>
<summary>Windows / PowerShell</summary>

```powershell
cd $HOME\Downloads
git clone -b claude/tool-build-markdown-spec-vnr7ss https://github.com/Bardy101/Website-Builder.git
cd Website-Builder
py -m pip install -r requirements.txt
```

Then **double-click `run.bat`** in the project folder. That's the whole
workflow — no command line after setup.

Use `py` (the Python launcher) rather than the full
`C:\Program Files\Python314\python.exe` path — shorter, and it picks the
right interpreter. No git installed? Download the ZIP from the branch page
(**Code → Download ZIP**), extract it, and `cd` into the extracted folder.

Paths in the examples below use forward slashes; PowerShell accepts either.
</details>

---

## The easy way: the menu

**Double-click `run.bat`** (Windows) or **`run.command`** (macOS). Or type
`python run.py`. You get a menu and never touch a flag:

```
============================================================
  Postal Outreach Pipeline — prospect finder
============================================================

  1  Find prospects (one or several niches/towns)
  2  Review a shortlist (cull to your 10-15)
  3  Combine batches into one sheet (by niche or town)
  4  Open a shortlist in your spreadsheet app
  5  Tune the scoring from your decisions
  6  Setup & configuration (keys, checks, tests)
  q  Quit
```

The main menu is the daily workflow, in the order the work flows. Setup lives
behind option **6**:

```
  Setup & configuration

  1  Check setup (keys, dependencies)
  2  Set up API keys (writes your .env file)
  3  Test API keys (one small call each)
  b  Back to the main menu
```

Everything the command-line flags do is asked as a question instead:
**Find prospects** offers to add more niche/town pairs to the same run
(merged and de-duplicated into one sheet — no config file to write), and asks
whether to ignore the 30-day cache and re-fetch fresh data. Start with
**6 → 1 Check setup** — it tells you which keys are set and what's missing.

Everything below is the same functionality driven from the command line, if
you'd rather script it.

---

## Try it right now, with no API keys

A fixture of invented businesses runs the whole stage offline:

```bash
./find.py --niche physiotherapist --area Hitchin --fixture examples/sample_fixture.json
```

> **The demo data covers physiotherapists in Hitchin only.** It's six invented
> businesses, not a copy of the internet — asking it for a plumber correctly
> finds nothing, and it will say so. To search any niche or town you need a
> Places API key and no `--fixture` flag.

```
    55  Bancroft Physio Rooms                  none         Hitchin
    45  Walsworth Road Sports Injury Clinic    social_only  Hitchin
    20  New Leaf Physio                        none         Hitchin
    15  Hitchin Osteopathy & Physiotherapy     unknown      Hitchin
```

No website beats a Facebook page beats a real site; the two-review newcomer is
penalised; the chain and the closed practice were excluded before they cost an
API call.

---

## Setup for real runs

**Do you need a `.env` file?** Only to search real businesses — the demo data
works without one. It's a plain text file holding your API keys, kept out of
the code so they never reach GitHub (it's gitignored).

The easiest way to create it is **Setup & configuration → Set up API keys**
on the menu, which prompts for
each key and writes the file for you. Windows Explorer refuses to create files
whose name starts with a dot, so this saves a genuine fight. By hand:
`cp .env.example .env` and fill it in.

| Key | Needed for | Cost |
|---|---|---|
| `GOOGLE_PLACES_API_KEY` | discover — search and details | Free allowance, then paid; cached 30 days |
| `PAGESPEED_API_KEY` | mobile scores | Free, rate-limited |
| `COMPANIES_HOUSE_API_KEY` | owner names | Free |

**Where to get them:**

- **Google Places** — [console.cloud.google.com](https://console.cloud.google.com/):
  create a project, enable **Places API (New)**, then Credentials → Create API
  key. Billing has to be enabled, but there's a free monthly allowance that
  comfortably covers this usage. Check
  [current pricing](https://mapsplatform.google.com/pricing/) before you start,
  and set a budget cap while you're in there.

### Key restrictions — one option here will break it

When Google offers to restrict the new key:

| Setting | Choose | Why |
|---|---|---|
| **Application restrictions** | **None** | "Websites" checks the browser referrer header. Python sends none, so every call is blocked. "IP addresses" works only with a static IP — home broadband rotates, so it breaks silently later. |
| **API restrictions** | **Restrict key**, tick **Places API (New)** (and PageSpeed Insights if sharing the key) | Free protection: a leaked key can't be spent on anything else. Tick the *New* Places API — the legacy "Places API" is a different one and won't work. |

Your key lives only in the gitignored `.env` on your machine, so "None" for
application restrictions is a reasonable trade here. Set a **budget cap** in
the console as the real backstop.

**Setup & configuration → Test API keys checks this for you** — one small call per key, and it names
the specific misconfiguration rather than returning a raw permission error:

```
FAIL  Google Places: the key is restricted to websites (HTTP referrers)
        Application restrictions must be 'None' (or an IP address).
        A 'Websites' restriction checks the browser referrer header,
        which a Python script does not send, so every call is blocked.
```

Restriction changes can take a few minutes to take effect, so if a test fails
right after an edit, wait and run it again.
- **PageSpeed Insights** — **no separate key needed.** Enabling an API in
  Google Cloud doesn't issue a key; keys live under **Credentials** and are
  shared across the APIs you tick on them. So: enable the PageSpeed Insights
  API, then go to Credentials → click your existing Places key → under **API
  restrictions** tick **PageSpeed Insights API** as well → Save. Paste the same
  key into both prompts (the key setup on the menu offers to do this for you). Free.

  The API does answer without any key, but on a low anonymous quota that a
  batch of 25 sites can exhaust — scores then come back empty and those sites
  are scored `unknown`.
- **Companies House** — nothing to do with Google; it's a UK government
  service with its own registration.
  [developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk/)
  → sign up → **Manage applications** → create an application → open it →
  **Create new key**. Free.

  Two choices to get right: pick the **live** environment, not test/sandbox
  (the sandbox holds invented companies, so owner lookups would return
  nonsense), and pick client type **REST**, not Streaming or OAuth2 — those
  are different products that won't authenticate here.

Each API costs nothing until you use it, and results are cached for 30 days,
so re-running a niche doesn't re-spend.

Only Places is required. Without the others you still get a ranked list —
`site_verdict` falls back to what the URL alone can tell you, and `owner_name`
stays blank (the letter then goes "FAO the Owner / Practice Manager").

**Caching:** every Places, PageSpeed and Companies House result is cached to
disk for 30 days, keyed by place ID. Re-running a niche is near-instant and
near-free, and you can widen the radius without re-paying for what you have.
A failed call is never cached, so one bad API day doesn't poison a month.

### What a run actually costs

Google retired the pooled $200 monthly credit in March 2025. Each SKU now has
its own free monthly allowance and they don't pool:

| Tier | Free calls/month per SKU |
|---|---|
| Essentials | 10,000 |
| Pro | 5,000 |
| **Enterprise** | **1,000** |

**Which tier you land in depends on the fields you ask for.** This tool's
Place Details call requests reviews, rating and photos — Enterprise fields —
so **Place Details bills at Enterprise, the 1,000/month tier**. That's the
number that matters here. Text Search asks for far less and bills at Pro.

The expensive fields aren't optional padding: the spec needs verbatim review
quotes for the letter copy, and `recent_review_date` is the single best
"still trading properly" signal in the shortlist.

Two things keep the cost down:

- **Chains and closed businesses are screened from the search response**, so
  they never cost a Place Details call. The search asks for `businessStatus`
  precisely so this is possible.
- **Everything is cached for 30 days.** Re-running the same niche costs
  nothing at all.

Every run prints what it spent:

```
Google API usage this run:
     1  Text Search    (Pro tier, 5,000 free/month)
    39  Place Details  (Enterprise tier, 1,000 free/month)
    12  served from the 30-day cache, costing nothing
  At this rate, roughly 25 more runs this month within the free
  Enterprise allowance.
```

### What a re-run costs

The cache is keyed per item, not per run, so you only ever pay for what's
genuinely new:

| You do | Google calls |
|---|---|
| Run Hitchin, radius 8000 | Search + one Details per candidate |
| **Re-run identical** | **Zero** — everything served from cache |
| Change `--top` (same or smaller reach) | Zero — a cached search serves any smaller request, and one that ran dry serves any larger one |
| Widen to radius 10000 | One search. Details only for businesses not seen before |
| Re-run at 10000 again | Zero |
| `--refresh` | Everything again, deliberately |

**Business data does not silently refresh.** A cached business keeps the
phone, rating and reviews captured at first fetch for 30 days. That's the
point — but it means a re-run won't pick up a business that has since changed
its website or gone quiet. Use `--refresh` when you want current data and are
willing to pay for it, or just wait for the 30-day expiry.

**New features apply to cached businesses for free.** Each lookup is cached
under its own key, so when a lookup is added that never ran before — the
website contact lookup, say — a re-run fills it in for every business already
cached without a single Google call. That's why scenario 2 above is zero: the
Places data came from cache, and reading the business's own website costs
nothing.

At the spec's cadence — 10–15 letters a fortnight — the free allowance is
comfortable. Widening `--top`, or running many niche/town pairs through
`--batch-config`, is what eats it. Check the Cloud console's billing report
for the authoritative figure; the tiers above were current when this was
written and Google has changed them before.

---

## The commands

### `find.py` — search, score, rank

```bash
# one niche, one town
./find.py --niche physiotherapist --area "Hitchin" --radius 8000 --top 25

# the whole patch at once, merged and de-duplicated
./find.py --batch-config examples/herts.yaml
```

Useful flags: `--no-site-checks` (skip PageSpeed, much faster),
`--no-owner-lookup`, `--no-site-contacts`, `--weights other.json`,
`--fixture` (offline), `--refresh` (ignore the cache and re-fetch),
`--workers N` (concurrent lookups, default 8).

The slow lookups — PageSpeed, page fetches, Companies House, contact pages —
run concurrently across businesses (they're independent), so a first run over
25 candidates takes a couple of minutes rather than fifteen. A page Lighthouse
completes on but cannot score is cached like a score; a transport or quota
error is retried next run instead of being remembered for 30 days.

**On `--radius`:** it's applied as a *location bias*, not a hard boundary.
Google weights results towards the circle rather than excluding everything
outside it, so widening from 8000 to 10000 pulls in more outlying businesses
but won't return a strictly bounded set. The town named in `--area` still does
most of the scoping work.

The batch-config version is the one that really kills the manual trawl: leave
it running across Stevenage, Letchworth, Hitchin, Baldock and Royston, come
back to one merged ranked sheet. A business found by two niches keeps its
higher score and appears once.

### `cull.py` — record your judgement

```bash
# walk the list one row at a time
./cull.py --batch batches/2026-09-01_physios_hitchin

# or mark up a 'reason' column in the CSV and import it
./cull.py --batch batches/2026-09-01_physios_hitchin --from-csv shortlist.csv
```

Writes `approved.csv` and appends every rejection to `rejections.jsonl` with
the full business record attached. **Always with a reason code** — free text
kills the analysis:

| Code | Meaning |
|---|---|
| `chain` | Franchise or multi-site; head office decides |
| `winding_down` | Looks dormant, sparse recent reviews, closure signals |
| `site_fine` | Existing site is genuinely good enough |
| `too_small` | One-person operation unlikely to spend |
| `wrong_niche` | Miscategorised by Places |
| `duplicate` | Same business, second listing |
| `no_owner_signal` | No named contact and no route to one |
| `gut` | You just don't fancy it — kept deliberately, and tracked |

`gut` is the interesting one. If it stays above ~20% of rejections after three
batches there's a real signal you haven't articulated yet — `cull.py` tells you
when it crosses that line.

### `tune.py` — turn rejections into weight proposals

```bash
./tune.py --batch batches/2026-09-01_physios_hitchin
./tune.py --batch batches/*/        # pool several batches
```

No machine learning: a frequency table. Per reason code, the average of each
machine-visible signal among rejected businesses versus approved ones. Where a
cluster is clear it proposes a threshold change against `weights.json` — you
apply it by hand. It stays quiet until it has at least three cases of a reason
code, so early noise doesn't move your weights.

Only worth running once you have rejections to learn from, i.e. after batch two.

---

## Scoring

Weights live in [`weights.json`](weights.json) so tuning edits data, not code.
Keep it versioned and note the batch number that produced each change; if a
later batch performs worse you can see what moved.

| Signal | Score | Why |
|---|---|---|
| No website at all | +40 | Best lead: nothing to fix, only to build |
| Facebook page / directory URL | +30 | Effectively no site |
| Mobile score < 50 | +25 | Real, visible problem |
| Mobile score 50–69 | +12 | Visibly middling — a dated site should outrank a flawless one |
| No mobile viewport | +10 | Doesn't adapt to a phone at all |
| No HTTPS | +15 | Trust problem, easy to show |
| ≥20 reviews and ≥4.3 rating | +15 | Established, cares about reputation |
| < 5 reviews | −20 | Too new or too small |
| Not OPERATIONAL | exclude | |
| Chain / franchise name match | exclude | Head office decides, not the manager |

The 50–69 band and the viewport signal are the v2 graded band: without them,
mobile 51 scored identically to a flawless 95, and 49 → 51 swung a full 25
points. A site can also be ugly-but-fast — PageSpeed can't see design — so
your eye still overrules the score at the cull, and `tune.py` learns from it.
Each business's `score_breakdown` in its `business.json` shows exactly which
signals fired.

Exclusions are checked *before* any site check, so a chain never costs a
PageSpeed call.

**`site_verdict`** is the column you actually scan:

| Verdict | Means |
|---|---|
| `none` | No website — the best lead |
| `social_only` | A Facebook page or directory listing |
| `poor` | Real site, but slow on mobile or no HTTPS |
| `dated` | Middling score, or no mobile viewport |
| `fine` | Genuinely good enough — a weak lead |
| `unknown` | Couldn't be measured (checks skipped, or PageSpeed unavailable) — not judged either way |

Unknown is never silently rated `fine`. If a field can't be established it stays
`null` — never guessed. A wrong owner name on a letter is worse than no name.

---

## Combining batches

Run small, focused batches — one niche, one town — and slice them however you
like afterwards. Combining costs **zero API calls**: it re-reads the batch
folders already on disk, de-duplicates by Place ID (a business found by two
searches appears once), and **re-scores everything with the current
`weights.json`** — so old batches pick up scoring improvements for free.

```bash
# all physios, across every town you've searched
./combine.py --all --niche physio

# every niche you've searched, Hitchin only
./combine.py --all --town Hitchin

# or name the folders explicitly
./combine.py batches/2026-08-25_physiotherapist_hitchin batches/2026-08-26_physiotherapist_stevenage
```

Or **menu option 3** — pick the batches by number, answer two filter
questions, done.

- The niche filter matches by containment both ways, so `physio` matches a
  batch searched as `physiotherapist`.
- The town filter matches the CSV's `town` column exactly (case-insensitive).
- The output is an ordinary batch folder: cull it, tune from it, open its CSV.
  Its records are copies, so it stands alone.
- A combined sheet is never folded into a later `--all` — that would silently
  double the pool.

One thing combining can't do: conjure businesses the original searches never
kept. Each source batch holds its own top rows only, so if you plan to slice
by town later, search each town properly rather than relying on spillover
from a neighbour's radius.

---

## Who gets the letter?

Two sources disagree more often than you'd expect:

- **Companies House** tells you who *legally owns* the business.
- **The website** tells you who *actually runs it day to day*.

A registered director may be a spouse, a dormant co-founder, or an
accountant's nominee. The practice manager named on the site with their own
email address may be the person who'd actually action a website conversation.
Address the wrong one and the letter reads as careless.

So the tool gathers both (spec section 5's lookup order: Companies House,
then the business's own About page) and reconciles them into a
`contact_check` verdict:

| Verdict | Means | Needs you? |
|---|---|---|
| `agree` | Both sources name the same person | No |
| `likely_same` | Same surname, different forename — often a middle name or nickname | A glance |
| `differ` | Genuinely two different people | **Yes** |
| `owner_only` | Director found, nobody named on the site | No |
| `site_only` | Named on the site, no company match — often a sole trader | A glance |
| `none` | Nothing found; falls back to "FAO the Owner / Practice Manager" | No |

`likely_same` exists because of a real case: Companies House filed a director
as `MADSEN, John Jay` while the website named "Jay". One person, two renderings.
Middle names are kept from the register specifically so this can be spotted —
matching on the display name alone would have called it a conflict.

**On a `differ`, the tool defaults to the website's person** — they run the
place and a website is their problem to solve — but flags the row so you
decide. A run tells you which rows need you:

```
2 row(s) need you to pick who to address:
  MVMNT Physio & Health Hitchin
    Website names Jay Patel (practice manager); register names John Madsen
    as director. Different people — decide who actually owns the website
    decision.
```

The cull shows both sources side by side, so the choice takes seconds:

```
    address to: Jay Madsen  [likely_same]  <-- check this
      register: John Madsen  (ltd)   |   website: Jay Madsen (practice manager)
```

**Everything from the website is confidence `low`** — it's pattern matching
over marketing copy, not a register. It's offered to you, never silently
trusted, and if nothing is found the salutation stays "FAO the Owner /
Practice Manager" rather than guessing.

Reading the About page respects `robots.txt`, fetches at most four pages per
business, identifies itself honestly in the user agent, and caches for 30 days
like everything else. Disable it with `--no-site-contacts`.

> **The `contact_email` column is a signal about who to address, not a mailing
> list.** A personal mailbox (`jay@`) tells you who runs things in a way
> `info@` doesn't. Emailing it uninvited is not permitted under the spec's
> compliance rules, and for sole traders it's unlawful under PECR.

---

## Folder layout

One batch = one folder. One business = one subfolder keyed by Place ID. No
database until batch five and it's clearly needed.

```
batches/2026-09-01_physios_hitchin/
  batch.json          niche, area, dates, status counts, rejection reasons
  shortlist.csv       ranked output of discover, before you cull it
  approved.csv        the 10-15 you actually chose
  rejections.jsonl    every rejection, with the full record attached
  ChIJxxxx.../business.json
```

`batches/` and `.cache/` are gitignored — they're working output, not source.

---

## Development

```bash
python3 -m unittest discover -s tests -t .      # 193 tests, no network needed
```

Every network client takes an injectable transport, so the whole pipeline is
testable offline. Pure logic (scoring, verdicts, name matching, tuning) is
separated from I/O and tested directly.

---

## Compliance notes

Carried from the spec, and they matter before batch one goes out:

- ICO data protection fee paid before the first batch is posted.
- Privacy notice URL in the letter footer and on the preview page.
- `company_type` is recorded because it decides email permissibility later:
  ltd companies are corporate subscribers under PECR, sole traders are not —
  never email a sole trader uninvited.
- Email follow-up **only** to people who opted in, phoned, or replied.

Places data is used through the official API — no scraping.

---

## What's next

Per the spec's build order, don't start Phase 1 until Phase 0 has been run a
couple of times for real. The next steps when you get there:

1. **Copy generation + voice validator** — one Claude call per business, JSON
   out, regenerate if a banned word appears or a review quote isn't verbatim.
2. **Letter PDF + QR + short URL** — WeasyPrint, A4, two pages.
3. **Approval sheet + approve/reject scripts.**
4. **Batch one:** 10–15 letters, tier-1 hand-built mockups.

Hand-building the first few mockups is deliberate, not a shortcut: it's what
tells you which sections and fields the template actually needs.
