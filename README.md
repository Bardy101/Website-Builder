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

  1  Find prospects
  2  Review a shortlist (cull to your 10-15)
  3  Tune the scoring from your decisions
  4  Open a shortlist in your spreadsheet app
  5  Check setup (keys, dependencies)
  6  Set up API keys (writes your .env file)
  q  Quit
```

It asks for the niche and town in plain English, runs the search, and offers
to open the result straight in Excel. Option **5** is the one to try first —
it tells you which keys are set and what's missing.

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

The easiest way to create it is **option 6 on the menu**, which prompts for
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
- **PageSpeed Insights** — same console, enable the PageSpeed Insights API.
  Free; the same key usually works for both.
- **Companies House** — [developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk/):
  sign up, create an application, copy the key. Free.

Each API costs nothing until you use it, and results are cached for 30 days,
so re-running a niche doesn't re-spend.

Only Places is required. Without the others you still get a ranked list —
`site_verdict` falls back to what the URL alone can tell you, and `owner_name`
stays blank (the letter then goes "FAO the Owner / Practice Manager").

**Caching:** every Places, PageSpeed and Companies House result is cached to
disk for 30 days, keyed by place ID. Re-running a niche is near-instant and
near-free, and you can widen the radius without re-paying for what you have.
A failed call is never cached, so one bad API day doesn't poison a month.

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
`--no-owner-lookup`, `--weights other.json`, `--fixture` (offline).

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
| No HTTPS | +15 | Trust problem, easy to show |
| ≥20 reviews and ≥4.3 rating | +15 | Established, cares about reputation |
| < 5 reviews | −20 | Too new or too small |
| Not OPERATIONAL | exclude | |
| Chain / franchise name match | exclude | Head office decides, not the manager |

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
| `unknown` | Site checks were skipped; not judged either way |

Unknown is never silently rated `fine`. If a field can't be established it stays
`null` — never guessed. A wrong owner name on a letter is worse than no name.

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
python3 -m unittest discover -s tests -t .      # 88 tests, no network needed
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
