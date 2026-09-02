# Postal Outreach Pipeline — Build Spec

**Purpose:** turn a niche + area into a folder of print-ready, personalised letters (each with a live mockup preview and QR code), with a human approval gate before anything is printed. You choose the businesses and post the letters; the script does everything in between.

**Design principles:** export → review → apply. Every stage writes files you can inspect. Nothing is printed, published or sent without a manual approve step. Batches of 10–15, not hundreds. Python CLI, no web app, no database beyond a folder of JSON.

**Build in phases, and each phase is useful on its own.** Phase 0 (section 2) is the prospect finder: it stands alone, needs nothing else to exist, and permanently removes the job of trawling the internet for candidates. Build it first, run it this week, and only then build the letter and mockup machinery on top. The target end state is that the tool finds, scores, writes, builds and renders; you approve, post and turn up to meetings.

---

## 1. Pipeline overview

```
discover  →  enrich  →  generate  →  preview  →  approve  →  print  →  post  →  track
 (Places)   (Places    (Claude:      (template   (you)      (PDF)    (you)   (QR scans,
              details,   letter copy,   fill →                                 replies)
              owner      mockup        static
              lookup)    content)      page)
```

Each stage is a separate script that reads and writes the same per-business folder. Re-running a stage on one business is cheap; re-running the whole batch is a one-liner.

---

## 2. Phase 0: the prospect finder (build this first, standalone)

**Deliverable:** one command → a ranked spreadsheet of candidate businesses with everything you need to decide. Nothing else in this spec needs to exist for this to be useful.

```
find.py --niche physiotherapist --area "Hitchin" --radius 8000 --top 25
```

Writes `shortlist.csv` and one `business.json` per row. Roughly one evening to build, and it is the last time you ever manually search for a prospect.

**Columns in `shortlist.csv`, ordered for fast human scanning:**

| Column | Why it's there |
|---|---|
| `lead_score` | Sort key — worst web presence first |
| `name`, `town` | Recognition |
| `website` | Blank = best lead |
| `site_verdict` | `none` / `social_only` / `poor` / `dated` / `fine` |
| `mobile_score` | PageSpeed mobile, blank if no site |
| `https` | Yes/no |
| `rating`, `review_count` | Established and reputation-conscious? |
| `recent_review_date` | Dormancy check — the single best "still trading properly" signal |
| `owner_name` | From Companies House where matched |
| `company_type` | `ltd` / `sole_trader` / `unknown` — decides email permissibility later |
| `phone`, `address`, `postcode` | Everything the letter needs |
| `maps_url`, `website_url` | Clickable, so your sift is two clicks per row |
| `photo_count` | Proxy for how much material a mockup would have |

The last two matter more than they look: the whole point is that your sift is *clicking through a prepared list*, not searching. Open the maps link, glance at the site, decide, move on. Twenty-five rows in under an hour.

**Run it across several niches and towns at once.** A `--batch-config` YAML listing niche/town pairs lets you leave it running and come back to one merged, de-duplicated, ranked sheet covering Stevenage, Letchworth, Hitchin, Baldock and Royston. That is the version that really kills the manual trawl.

**Caching:** every Places and PageSpeed result cached to disk for 30 days, keyed by place ID. Re-running a niche is then near-instant and near-free, and you can widen the radius without re-paying for what you already have.

**What phase 0 deliberately does not do:** no letters, no mockups, no emails, no publishing. It finds and it scores. That's all — and it's the part that buys back the most of your evenings.

---

## 3. Folder and data model

One batch = one folder. One business = one subfolder keyed by Google Place ID.

```
batches/
  2026-09-01_physios_stevenage/
    batch.json                     # niche, area, dates, status counts
    shortlist.csv                  # ranked output of discover, before you cull it
    approved.csv                   # the 10–15 you actually chose
    ChIJxxxxxxxxxxxx/
      business.json                # everything known about them (see below)
      copy.json                    # Claude-generated letter + mockup copy
      preview/                     # static site files for the mockup
        index.html
        assets/
      letter.pdf                   # print-ready, A4, 2 pages
      insert.png                   # mockup screenshot used on page 2
      qr.png
      status.json                  # stage timestamps + approval flags
    ChIJyyyyyyyyyyyy/
      ...
    _approved/                     # symlinks/copies of approved letter.pdf, ready to print
    _rejected/                     # ones you binned, with a reason field kept for tuning
```

**business.json** (the single source of truth for one business):

```json
{
  "place_id": "ChIJ...",
  "name": "Hitchin Physio Clinic",
  "niche": "physiotherapist",
  "address": { "line1": "12 Bancroft", "town": "Hitchin", "postcode": "SG5 1JQ" },
  "phone": "01462 000000",
  "website": null,
  "rating": 4.8,
  "review_count": 63,
  "business_status": "OPERATIONAL",
  "opening_hours": ["Mon 08:00–18:00", "..."],
  "photos": ["places_photo_ref_1", "places_photo_ref_2"],
  "reviews": [ { "text": "...", "rating": 5 } ],
  "owner": { "name": null, "source": null, "confidence": "none|low|high" },
  "company": { "number": null, "type": "unknown|sole_trader|ltd|llp" },
  "site_score": null,
  "lead_score": 0,
  "preview_slug": "hitchin-physio-clinic",
  "preview_url": "https://preview.yourdomain.co.uk/p/hitchin-physio-clinic",
  "short_url": "https://yd.uk/h7k2",
  "created": "2026-09-01T20:14:00Z"
}
```

Keep this flat and boring. If a field is unknown it is `null`, never guessed.

---

## 4. Stage: discover

**Input:** niche keyword(s), centre point (town), radius, max results.
**Output:** `shortlist.csv`, ranked worst-web-presence-first.

Use Google Places API (Text Search / Nearby Search, then Place Details for the fields you need). This is legitimate API use — no scraping.

**Lead score** (simple, tunable weights):

| Signal | Score | Why |
|---|---|---|
| No website at all | +40 | Best lead: nothing to fix, only to build |
| Website is a Facebook page / directory URL | +30 | Effectively no site |
| Website exists but Lighthouse mobile score < 50 | +25 | Real, visible problem |
| Website exists but no HTTPS | +15 | Trust problem, easy to show |
| review_count ≥ 20 and rating ≥ 4.3 | +15 | Established, cares about reputation |
| review_count < 5 | −20 | Too new or too small |
| business_status ≠ OPERATIONAL | exclude | |
| Chain / franchise name match | exclude | Head office decides, not the manager |

For businesses that do have a site, run the same lightweight checks the audit tool will use (PageSpeed Insights API for mobile score, HTTPS check, viewport meta present). Cache results for 30 days — the API is free but rate-limited.

**Your job at this stage:** open `shortlist.csv`, glance at the top 20–25, cull to 10–15, save as `approved.csv`. This is the 60–90 minute manual pass, and it is where the taste lives. Record a reason code for every rejection — see section 17, which turns the cull into the feedback loop that tunes these weights.

---

## 5. Stage: enrich

**Input:** `approved.csv`
**Output:** populated `business.json` per business.

- Pull full Place Details: address components, phone, hours, photos (download the top 3–4 at 800px), the five most recent reviews.
- **Owner name lookup, in order:** (1) Companies House API — free, search by name + postcode; if a match, take the active director(s) and set `company.type = ltd`, confidence high. (2) The business's own website "About" page, if one exists — first name + surname near "founder", "owner", "principal"; confidence low. (3) Nothing — leave `null`, letter goes "FAO the Owner / Practice Manager".
- Record `company.type` because it matters later: ltd companies are corporate subscribers under PECR (email is permissible if they reply), sole traders are not (never email uninvited).
- Generate `preview_slug` (kebab-case name, de-duplicated) and reserve the short URL.

---

## 6. Stage: generate (copy)

**Input:** `business.json`
**Output:** `copy.json`

One Claude API call per business, with a fixed system prompt and the business record as JSON. Ask for **JSON only**, and validate before saving:

```json
{
  "letter": {
    "salutation": "Dear Sarah,",
    "opening": "…",           // 2 sentences, references something real: a review, the town, their specialty
    "observation": "…",       // 2–3 sentences: the specific gap you saw (no site / slow / not mobile)
    "offer": "…",             // 2 sentences: 'I've built a first draft — it's yours to look at, no strings'
    "close": "…",             // 1–2 sentences, phone + preview URL, no pressure
    "ps": "…"                 // one line, human, for handwriting
  },
  "mockup": {
    "headline": "…",
    "subheadline": "…",
    "services": ["…","…","…"],
    "about_short": "…",
    "cta_primary": "Book an appointment",
    "review_pull_quote": "…"  // must be a real review from business.json, verbatim
  }
}
```

**Voice rules baked into the system prompt:** plain English, no marketing adjectives, no "leverage/elevate/unlock", British spelling, under 220 words for the letter body, never claim they are losing customers (you don't know that), never mention competitors by name. Reject and regenerate if any banned word appears or if the review quote isn't verbatim from the input.

---

## 7. Stage: preview (the mockup)

**Input:** `business.json` + `copy.json`
**Output:** `preview/index.html` + assets, published to a static host; `insert.png` screenshot.

### The substrate decision (read this before building anything)

> **Amendment — delivery substrate. DECIDED 2026-09-02.** Everything below
> chose a substrate for the *preview* and left the delivered site's platform
> unstated. That put this section in conflict with `package-definition.md`,
> whose clause 6 and Essentials handover both assume a managed platform the
> client edits themselves. The operator's constraint is that **the mockup must
> match the live product**, so preview and delivery cannot sit on different
> substrates.
>
> **Chosen: Webflow, exported to HTML.** Design once in Webflow; export the
> static markup and variable-ise that for tier 1 and tier 2; deliver the
> client's live site on Webflow with editor access. One design serves both
> ends, so the mockup is the live product's own markup rather than a
> lookalike. Framer stays rejected for exactly the reason given in the table
> below — no code export — and Webflow is the only managed platform that
> avoids it. Bought HTML/Tailwind templates remain useful as design
> references and for learning the field list, but the pipeline's source of
> truth becomes the Webflow export.
>
> **Plan tier — resolved 2026-09-02.** Webflow's Workspace tiers are
> Starter (free) / **Core ($28/mo billed monthly, cheaper annually)** /
> Growth ($60) / Enterprise. Code export is absent on Starter and appears at
> **Core**, so Core is the floor. Core also carries the two other things this
> model needs: ten staging sites and one Shared Library for the design
> system. Growth buys unlimited staging, site-level roles and publishing
> permissions — none of which matter solo.
>
> At roughly £265/year against £495 per site, the first client each year
> covers the subscription. It is an operating cost on the operator, not
> something clause 6 passes to the client.
>
> Note the two-cost structure: the Workspace plan is yours (designing), the
> Site plan is the client's (hosting on their domain). The Workspace tiers
> do not include a custom domain launch at any level. Client *count* is never
> capped by the Workspace tier — "unlimited paid hosting sites" is on every
> tier including the free one — so the only per-site cost is the client's own
> Site plan.
>
> Guest access is listed on every tier, free included, which confirms the
> delivery model from the other side: a client on the free tier can still
> invite you into their Workspace as a guest. They buy their Site plan, you
> build in their account, they own it — which is how clause 5's "the client
> owns their platform account" stays true without you carrying their hosting.
>
> **Core's 10 staging sites are not a cap on batch size.** There is no
> Webflow site per prospect: you build one master design per layout shape —
> clinic, professional, trades, generic — export each once, and the pipeline
> generates per-prospect previews as static files on your own host. Four of
> ten used. This is the property Framer could not offer.
>
> **Design rule that follows: build the templates with no CMS Collections.**
> Webflow's export emits HTML/CSS/JS but not Collection-driven content, so a
> Collection-backed design would export as something that no longer matches
> the live site — breaking the constraint this whole decision exists to
> satisfy. Keeping the templates static also means the client needs only the
> cheapest Site plan, which is what keeps clause 6's "~£10–20/month" honest.
>
> **Still unverified:** how the client edits their live site after handover.
> The Workspace table does not cover it, and Webflow retired its standalone
> Editor. The implied model is that the client holds their own free Starter
> Workspace plus a paid Site plan and edits there, with you joining as a free
> guest. Confirm before promising the Essentials handover video, because the
> package's support boundary rests on it. If client editing turns out to need
> a paid seat in *your* Workspace, the per-client cost changes and the
> fallback below applies.
>
> **Fallback if the economics break:** static HTML delivered and hosted by
> you, with client self-editing dropped and an edit allowance folded into
> Site Care instead. That is a different product — it would require clauses
> 5, 6 and 8 and the Essentials handover in `package-definition.md` to be
> rewritten, not just narrowed.

**Premium feel does not come from the builder. It comes from the template.** No AI generator supplies taste you don't have — they reliably produce competent, generic layouts, which is the one thing that kills the premise of the letter. A bought, professionally designed template supplies the typography, spacing and colour decisions a designer already made; your job reduces to filling it in well, which is a job you can do.

**Buy two or three premium HTML/Tailwind templates.** Roughly £30–80 each from the usual marketplaces, or a Tailwind component library. Pick them by target shape, not by industry label: a clinic/practice layout and a professional-services layout between them cover physios, accountants and conveyancers. You own the files.

Critically, the same files serve tier 1 and tier 2 — hand-edited for batch one, variable-ised for the script afterwards. **Batch one is therefore practice for the automation, not a detour from it.**

**Rejected substrates and why:**

| Tool | Verdict |
|---|---|
| **Lovable** | Wrong category. It builds full-stack React apps with auth and a database; you need a brochure page. Output is functional but not design-optimised, and it produces generic layouts needing manual polish. |
| **Framer** | Genuinely premium output and fast — but **no code export and locked hosting**. Fine for hand-building ten mockups, fatal at tier 2, because template-fill depends on a script injecting fields and publishing. Build a Framer workflow now and you throw it away in six weeks. |
| **Bought HTML/Tailwind templates** | **Superseded — see the amendment above.** Premium by default, fully scriptable, you own the files, publishes anywhere for pennies — but delivering raw HTML breaks the client self-editing the package is sold on. Kept as a design reference and as the fallback if Webflow's export tier proves uneconomic. |
| **Webflow** | **Chosen.** The only managed platform that exports clean static HTML *and* lets the client edit the live site, so one design serves both the scripted preview and the delivered product. Costs a plan tier; verify code export is on it. |

Two things that do more for perceived quality than any tool choice: **real photography** (Places photos are frequently poor — budget a few decent stock shots per niche) and **restraint** (generous whitespace, two fonts maximum, one accent colour lifted from their existing branding). Amateur sites look amateur because they're crowded, not because the components are bad.

### Tier 1 — hand-filled templates (batch one)

Copy the bought template into `preview/`, then by hand: swap the business name and logo, set the accent colour from their branding, drop in photos, paste the copy from `copy.json`. Screenshot the hero plus one section as `insert.png`, publish, paste the URL into `business.json`.

Fifteen minutes each once you've done two. Ten businesses ≈ 2–3 hours. **While doing this, keep a running note of every field you had to change** — that list *is* the tier 2 variable schema, and collecting it is half the reason tier 1 exists.

### Tier 2 — template-fill (the target state)

- The tier 1 templates, with every field you noted swapped for a Jinja2 variable. Cover **trades** (plumber/roofer/electrician), **clinic** (physio/dentist/osteo), **professional** (accountant/solicitor), and a **generic local service** fallback. Each a single-page site: hero with phone + CTA, services grid, about, reviews, map embed, contact/hours, footer.
- Script picks the template by niche, fills it from `business.json` + `copy.json`, pulls a dominant colour from their logo/photos (or a safe niche palette if none), writes `preview/index.html`, and publishes to `preview.yourdomain.co.uk/p/<slug>` (S3 + CloudFront, or Cloudflare Pages — either is pennies).
- Screenshot with Playwright at 1440×900 and 390×844 → `insert.png` (desktop) plus a phone-frame variant for the letter.
- Add a thin banner across the top of every preview: "A first-draft concept for [Business], prepared by [You] — nothing here is live or published on your behalf." This matters both for honesty and so an owner who scans it isn't confused.
- **Preview page includes the opt-in form:** "Want the full walkthrough video? Leave your email." Unticked consent checkbox, privacy link, timestamped submission stored (SES email to you + a JSON append). This is the compliant follow-up trigger.
- Previews expire after 60 days (or on request) — a nightly job unpublishes anything past its date unless flagged `keep`.

**Template licensing:** check the licence before buying. You need one that permits use across multiple end-client projects, or a per-client licence cheap enough to buy on each sale. A preview is arguably not a delivered product, but once a client says yes it certainly is — don't discover this at invoice time.

### Tier 3 — bespoke generation (optional, later)

Only if template-fill feels samey. Claude writes the full page against the bought template's design language as a brief, Playwright screenshots it, a second Claude call reviews the screenshot for obvious breakage, and you still eyeball before it publishes. Not worth building until tier 2 has run for a few batches.

---

## 8. Stage: letter PDF

**Input:** `copy.json`, `insert.png`, `qr.png`
**Output:** `letter.pdf` — A4, two pages, print-ready.

- Render with WeasyPrint (HTML/CSS → PDF) from one letter template. Your letterhead top-left, their name/address block, date.
- **Page 1:** the letter. Body from `copy.json`, ~200 words, generous whitespace. Signature image. Blank space for the handwritten P.S. (the P.S. text is printed on the approval sheet for you to copy by hand — do not print it).
- **Page 2:** "Here's what a first draft could look like" — the desktop screenshot large, phone screenshot small beside it, the QR code, the short URL in large type, and your phone number. One line under the QR: "Scan to see it live — it's yours to look at, no strings."
- QR: generated with `qrcode`, encoding `short_url` (never the long URL). Short URL redirects to the preview and **logs the hit** — this is your open/engagement signal.
- Also emit `approval-sheet.pdf` per batch: one row per business showing name, owner, first two lines of the letter, thumbnail of the mockup, P.S. text to handwrite, and a checkbox. This is what you review.

---

## 9. Stage: approve

Manual, by design.

- Open `approval-sheet.pdf` and each `letter.pdf`. Approve, edit, or reject.
- Approve: `approve.py <place_id>` → copies `letter.pdf` into `_approved/`, sets `status.approved = timestamp`.
- Edit: fix `copy.json` by hand and rerun stages 7 (and 6 if the mockup copy changed) for that one business.
- Reject: `reject.py <place_id> --reason "chain"` → moves to `_rejected/`, unpublishes the preview. Reasons accumulate in `batch.json` for tuning the discover weights.

Nothing in `_approved/` was written without you having looked at it.

---

## 10. Stage: print and post

Two options; both start from `_approved/`.

**Make it look expensive.** The letter is a costly signal before it is a message: it says you spent real time and money on them before they gave you anything. Lean into that — the extra pound per unit is trivial against what it communicates. Batch one physical spec: 120gsm letter stock, the mockup printed on a separate sheet of heavier card (200–250gsm) rather than as page 2 of the letter, a proper C5 envelope (not DL), handwritten envelope and P.S., first-class stamp (not franked). It should feel like something a person made, not something a system produced — even though a system produced most of it.

- **Self-print:** merge `_approved/*.pdf` into one file, print duplex on decent stock, fold, handwrite the P.S. and address the envelope, first-class stamp. Ten letters ≈ 30 minutes.
- **Print-and-post service:** several UK printers accept a PDF upload plus an address CSV and post it for you (Docmail, Stannp, PostGrid and similar). Loses the handwritten P.S. — worth it only once volume justifies it. Script emits `addresses.csv` in their format regardless, so switching is trivial.

Log `status.posted = date` when they go in the postbox.

---

## 11. Stage: track and follow up

- **QR / short-URL hits** land in a small log (Cloudflare Worker KV, or a Lambda writing to DynamoDB — the same table the audit tool will use). Nightly script rolls them into `batch.json`: scanned / not scanned per business, first-scan date.
- **Preview opt-ins** email you immediately. That is your green light: reply personally within a day (this is warm and consented — Loom walkthrough of the mockup, offer of a 20-minute face-to-face).
- **Phone calls** in: log manually.
- **No scan, no reply after 14 days:** second letter, different angle (e.g. "three things I noticed about how your practice appears on Google"), same QR. Never an email — that would be unsolicited electronic marketing, and for sole traders it's not permitted.
- **No response after two letters:** mark `closed`, unpublish preview at 60 days, keep the record so you don't re-approach them for 12 months.

**Report per batch:** letters posted, scans, opt-ins, calls, meetings, wins, per-niche. After two batches you'll know which niche and which letter angle to keep.

---

## 12. Compliance checklist (baked in, not bolted on)

- ICO data protection fee paid before batch one goes out.
- Privacy notice URL in the letter footer and on the preview page ("we've used publicly available business details to prepare this; ask and we'll delete them").
- Preview opt-in form: unticked checkbox, explicit wording, timestamp stored.
- Email follow-up **only** to people who opted in on the preview page, phoned, or replied. Not to non-responders.
- MPS: run the approved list through the Mailing Preference Service once volume warrants; for 10–15 B2B letters a fortnight this is a courtesy, not a legal bar.
- Preview banner makes clear it's a concept, not something published on their behalf. Never use their logo on the preview if you have any doubt it's theirs to use — a plain wordmark in their colours is safer.

---

## 13. Stack

- **Python 3.11**, one repo, `pipeline/` package with a script per stage plus a `run_batch.py` that chains them.
- APIs: Google Places (discover/enrich), PageSpeed Insights (site checks), Companies House (owner lookup), Anthropic (copy), all keys in a `.env`.
- Templates: Jinja2 for both letter and site templates. WeasyPrint for PDF. Playwright for screenshots. `qrcode` for QR.
- Preview hosting: S3 + CloudFront (fits your AWS estate) or Cloudflare Pages (simpler). Short URLs + hit logging: one Cloudflare Worker or one Lambda + DynamoDB table.
- Local state: the batch folder. No database until you're on batch five and it's clearly needed.

---

## 14. Build order and effort

Grouped into phases. Each phase ends with something usable, so you can stop, run it, and decide whether to carry on.

### Phase 0 — the prospect finder (build this week)

| Step | Effort | Gate |
|---|---|---|
| Places search + details + caching | 1 evening | Twenty-five real businesses in a CSV |
| Site checks (PageSpeed, HTTPS, viewport) + `site_verdict` | 2 hours | Verdicts you agree with when you spot-check five |
| Lead score + ranked CSV with clickable links | 1 hour | Top of the list looks like who you'd have picked |
| Companies House owner lookup | 1 hour | Named owner on most ltd companies |
| Multi-niche/multi-town batch config | 1 hour | One merged sheet for the whole Herts patch |
| Cull loop: reason codes + `cull.py` | 1 hour | Rejections captured with reasons |

**Phase 0 output: you never manually search for a prospect again.** Everything below is optional until you've used this a couple of times.

### Phase 1 — first batch out the door

| Step | Effort | Gate |
|---|---|---|
| Copy generation + voice validator | 1 evening | Ten letters you'd be happy to sign |
| Letter PDF + QR + short URL + hit log | 1 evening | A printed letter that looks like a person wrote it |
| Approval sheet + approve/reject scripts | 1 hour | — |
| **Batch one: 10–15 letters across three arms, tier-1 hand-built mockups, expensive-looking physical spec** | 1 weekend | Posted. This is the go/no-go experiment |

Hand-filling the bought templates here is deliberate, not a shortcut: the list of fields you change by hand becomes the tier 2 variable schema. Build the template blind and you'll build it twice. Buy the templates *before* batch one, not after — tier 1 and tier 2 share the same files.

### Phase 2 — remove yourself from the mockup job

| Step | Effort | Gate |
|---|---|---|
| Buy 2–3 premium HTML/Tailwind templates | 1 hour + ~£100 | Templates you'd be proud to put your name on |
| Template-fill (tier 2), first template | 2 evenings | Preview you'd show a client |
| Remaining templates | 1 evening each | — |
| `tune.py` weight proposals | 1 hour, after batch two | Only once you have rejections to learn from |
| Print-and-post integration | 1 hour | Only if volume justifies |

**Phase 2 output: the tool finds, scores, writes, builds and renders.** You approve, post, and turn up to meetings.

Roughly one evening to phase 0, three more to first batch, another three or four to full tier-2 automation. Don't build phase 2 until batch one has taught you something.

---

> **Amendment — visual quality signal. IMPLEMENTED.** The lead score
> originally used PageSpeed mobile score as a website-quality proxy. It isn't
> one, and the signal proved inverted in the first live run: dated sparse sites
> score well, good rich sites score badly. Sections 2 and 4 are superseded on
> this point by `briefs/done/brief-visual-quality-signal.md`, which demotes
> mobile score to a tiebreaker, adds staleness detection (viewport tag, media
> queries, copyright age, table layout, Flash, platform hint) and introduces
> screenshot capture plus a contact-sheet cull. **Read the brief, not the
> weights table in section 4** — that table is now historical.
>
> Two further points where the shipped code knowingly differs from what these
> documents say, flagged for the operator rather than silently adopted:
>
> - **`site_verdict` has a sixth bucket, `unknown`,** for a site that could not
>   be fetched. Neither the spec's five buckets nor the brief's four cover that
>   case, and calling an unreachable site `fine` would bury a possible prospect
>   on no evidence — the opposite of the fail-open rule. If the operator would
>   rather such rows were dropped or bucketed differently, that is a design
>   decision to make in conversation.
> - **Section 2's shortlist columns are out of date.** The live CSV also
>   carries the staleness columns from the brief, plus contact-reconciliation
>   columns (`address_to`, `contact_check`, `site_contact`, `contact_email`)
>   from the section 5 step-2 owner lookup. Section 2's table should be
>   refreshed when someone next revises the spec.

## 15. Open decisions

- Preview host: S3/CloudFront vs Cloudflare Pages.
- ~~Webflow plan tier for code export~~ — resolved: Core, $28/mo. Remaining
  unknown is how the client edits their live site after handover (see the
  amendment in section 7).
- Which template marketplace for design reference, and whether any bought
  template's licence covers multi-client use.
- Short-URL domain: buy a short one or use a path on your main domain.
- First niche for batch one (recommendation: physios or accountants — static content, mostly ltd companies, review-conscious).
- Batch one is split 5/5/5 between preview-first, tease → reveal, and gift — see section 16. Cut the tease arm first if fifteen mockups is too many for a first weekend.

---

## 16. Sequence variant: tease → reveal

Batch one runs **three arms** — five preview-first, five tease → reveal, and five gift (see below). If that stretches the budget or the mockup-building time, cut the tease arm first; the gift arm is the more interesting test.

**Arm A (preview-first)** gets the full letter with mockup and QR as described in section 8. **Arm B (tease → reveal)** gets a light first letter with no mockup, then the reveal letter at day 14 to non-responders. This tests whether familiarity plus a mockup outperforms a cold mockup, and whether the mockup is doing the work at all. **Arm C (gift)** is the counterintuitive version — a mockup with no pitch at all — described in its own subsection below.

### Letter one (tease)

One page, no insert. Copy structure, ~150 words:

- Salutation (named owner where known).
- Opening: one real observation — their reviews, their specialty, something from their Google profile.
- The gap: "your practice doesn't come across online the way it does in person / in your reviews."
- The offer: "I'd like to build you a first draft of what it could look like — no charge, no strings. If that's of interest, scan the code or ring me and I'll get on with it."
- Close, phone number, short URL, handwritten P.S. space.

`copy.json` gains a `tease` block with the same fields as `letter`. The voice rules apply unchanged.

### Tease QR page

The short URL for arm B initially points to a lightweight **holding page** at the same slug: business name, one line ("A first-draft concept for [Business] is on its way"), and the same opt-in form (unticked checkbox, privacy link) worded "leave your email and I'll send you the draft when it's ready." Same hit logging. When the reveal is published, the slug flips from holding page to preview — no new URL, no new QR.

### Reveal (letter two, day 14)

Identical to the arm A letter but with an opening that acknowledges the first: "I wrote a fortnight ago — I went ahead and built it anyway. Here's what it could look like." Page 2 as before: screenshots, QR, short URL, phone.

Because the mockup for arm B is only built at day 14, **build them in priority order: businesses that scanned the tease first**, then the rest. If a tease scan converts to an opt-in before day 14, skip the letter — send the preview by email (they've consented) and move to the warm follow-up.

### Arm C — the gift (no pitch)

The opposite of a good strategy can also be a good strategy. This arm doesn't sell. The letter says, roughly: *"Your reviews were so good and your website was so much worse than the practice deserves that I built this for you. It's yours — no strings, no follow-up. Do what you like with it. If you'd like it made live, my number's below, but you don't need to ring me."* Your name and a small line in the footer, nothing else.

- Same mockup, same card insert, same QR to the live preview.
- **No second letter to this arm.** The whole point is that it's a gift; a follow-up turns it into a pitch and breaks the test.
- The preview page for this arm carries the same opt-in form, worded even more softly — "if you'd like the files, leave an email" — because a scan tells you it landed even when nobody rings.

What you learn: whether reciprocity out-performs the direct ask. If the gift arm produces more calls and warmer meetings than arm A, that's the default going forward and the copy in section 6 changes shape entirely. Note also that the people who ring you off a gift are self-selected and warm — fewer, but a much better fit.

### After the reveal

Both arms converge. Everyone who has received the mockup letter gets the same 14-day wait, then `closed`. **No third letter** — two is a sequence, three is a campaign, and in a small area the same name landing repeatedly works against you.

### What to record

`status.json` gains `arm: "A"|"B"|"C"`, `tease_posted`, `tease_scanned`, `reveal_posted`. The batch report splits every metric by arm: scans after letter one, scans after reveal, opt-ins, calls, meetings, wins. Two batches of this is enough to pick a default sequence.

Also log the trivial variables, because they move numbers more than they should: envelope handwritten vs printed, first vs second class, day of week posted, whether the insert shows the phone or desktop view first. Cheap to vary, free to record via the QR log, and worth a look after batch three.

### Decision rule after batch two

- Arm B wins clearly → default to tease → reveal; only build mockups for non-opt-outs, prioritise scanners. Cheaper per lead.
- Arm A wins clearly → always lead with the artefact; drop the tease.
- Arm C (gift) wins → the pitch was hurting you. Default to gift-first; drop the sales close from the letter entirely and let the preview page and reciprocity do the work.
- No clear difference → keep arm A (simpler, one letter fewer to write) and stop testing.

---

## 17. The cull loop: turning your judgement into weights

The discover stage ranks; **you cull**. The cull is not overhead to be automated away — it is how the scoring model gets trained, and it is the only part of the pipeline that sees what the API cannot. Formalising it costs about twenty lines of script and makes every subsequent batch cheaper.

### Why not just trust the score from day one

The lead score in section 4 ranks on machine-visible signals: no website, poor mobile score, no HTTPS, review counts. It cannot see that a practice looks like it's winding down, that the owner replies to every review (cares, and is reachable), that a site is dated but clearly converting, or that two "independent" listings are the same person. Those judgements are what batch one teaches you, and they're what makes tuned weights worth trusting later.

### The loop

1. `discover.py --niche physio --area Hitchin --top 25` → `shortlist.csv`, ranked.
2. You open it and spend an hour. Keep 10–15, reject the rest — **always with a reason code**.
3. `cull.py` writes `approved.csv` and appends every rejection to `rejections.jsonl` with the full business record attached.
4. `tune.py` reports, per reason code, which machine-visible signals correlate with your rejections — and proposes weight adjustments. You accept or ignore them.

### Reason codes

Fixed, short list — free text kills the analysis. Start with:

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

`gut` is the interesting one. If it stays above roughly 20 percent of rejections after three batches, there's a real signal you haven't articulated yet, and it's worth sitting down to name it.

### What tune.py actually does

No machine learning. A frequency table: for each reason code, the average value of each machine-visible signal among rejected businesses versus approved ones. If `too_small` rejections cluster at review counts under twelve, it proposes raising the low-review penalty threshold. If `site_fine` rejections cluster at mobile scores above sixty-five, it proposes tightening that band. It prints proposals as a diff against `weights.json`; you apply them by hand.

Keep `weights.json` versioned, with the batch number that produced each change in a comment field. If a later batch performs worse, you can see what moved.

### Convergence target

- Batch one: cull 25 → 12. Expect to reject roughly half.
- Batch three: cull 20 → 13. Rejections falling, `gut` shrinking.
- Batch five: cull 15 → 12. The score is doing the real work; your hour becomes twenty minutes.

If rejection rate is *not* falling by batch four, the problem is the signals, not the weights — likely the niche is too broad, or Places categorisation is too noisy for it. Narrow the niche before adding cleverness.

### Where the public audit tool fits

Still not yet. The discover-and-cull loop is internal plumbing and earns its keep from batch one. The public-facing audit page with its own URL and email gate only pays off once there's inbound traffic to catch, and there isn't any until the letters start landing. Revisit it after batch two — by then the same scoring code powers both, and it's mostly a front end.

---

## 18. The real asset: a warm client list

Worth recording now, though it changes nothing about phases 0–2.

The websites are the product; **the client list is the asset.** A delivered, paid-for, happy engagement buys two things a stranger will never give you: trust, and permission to make contact. A past client is an order of magnitude easier to sell to than any name on a shortlist, and the postal pipeline is, viewed from a distance, a fairly cheap machine for manufacturing past clients.

### Keep the door open (this is the bit that's easy to get wrong)

The current model deliberately ends the relationship at handover — correct for support, wrong for anything later. Two cheap mechanisms preserve the relationship without reopening the support tail:

- **The £19/month monitoring plan** — a standing, low-effort reason to exist in their world.
- **A genuine annual check-in** — one email a year, on the anniversary, flagging anything that's aged badly. Costs an hour a client per year.

Both are permitted contact: they're existing customers who gave consent at intake, and the soft opt-in applies. This is the payoff for having done the consent properly at the start.

### The filter for anything sold later

One test, and it's non-negotiable given the time constraint: **can it fail quietly, and be fixed asynchronously?**

| Passes | Fails |
|---|---|
| Booking / calendar integration | Customer-facing chatbot |
| Review collection and display | Anything answering the public in real time |
| Conveyancing quote calculator | Live chat staffed by you |
| Annual refresh / redesign | Anything with an SLA |
| Additional pages, landing pages | Hosting or email you're responsible for |
| Analytics summary once a quarter | Social media management |

Chatbots are the instructive example: appealing, obviously sellable, and a support tail dressed as a product. They say wrong things, they need retraining, and the client rings *you* when one embarrasses them in front of a customer. If one is ever built, it must be a managed third-party product resold with the vendor carrying support — never your own.

### Sequencing

Don't build any second product until there are roughly ten past clients to sell it to. Before that the audience is too small to justify the build, and the right move is more batches. When the time comes, the cheapest research is an email to those ten asking what they'd pay to fix — the answer is rarely what you'd guess.
