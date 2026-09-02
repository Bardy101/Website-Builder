# CLAUDE.md

Read this file first. It tells you what this project is, where the
authoritative documents live, and what the current task is.

---

## What this project is

A prospecting and outreach pipeline for a solo web design side-business
operating in Hertfordshire, UK. The pipeline finds local businesses with poor
or absent websites, generates a mockup and a personalised letter for each, and
tracks responses. **Outreach is by post, not email** — this is a deliberate
compliance decision (postal marketing falls outside PECR) and is not to be
"optimised" into cold email.

Operator is a full-time systems administrator building this in evenings. The
governing constraint on every design decision is **minimal ongoing support
burden**. Prefer boring, inspectable, low-maintenance solutions over clever
ones.

## Where the documents live

| Path | What it is | Authority |
|---|---|---|
| `docs/postal-pipeline-build-spec.md` | The full pipeline design, 18 sections | Primary reference — read for context on any stage |
| `docs/briefs/` | Implementation briefs, one per change | **Supersedes the spec** where they overlap |
| `docs/package-definition.md` | Client-facing pricing, scope and terms | Reference only, not code |
| `docs/client-intake-form.md` | Client intake questionnaire | Reference only, not code |

**Precedence rule:** if a brief in `docs/briefs/` contradicts the spec, the
brief wins. Briefs are written after the spec, in response to things learned
from live runs. The spec carries amendment notes pointing at superseding
briefs — respect them.

## Current task

**None — awaiting next brief.**

`docs/briefs/` is empty. The most recent completed work is
`docs/briefs/done/brief-visual-quality-signal.md` (visual quality signal),
which replaced the PageSpeed-based lead score with staleness detection and
added the screenshot contact sheet.


## Brief lifecycle (you maintain this, not the operator)

When you finish a brief and its acceptance criteria pass, do all of the
following as part of the same piece of work — do not wait to be asked:

1. Move the brief file from `docs/briefs/` to `docs/briefs/done/`.
2. Prepend a completion line to the top of the moved file:
   `**Completed:** <date> — <one line on what changed and anything that
   deviated from the brief.>`
3. Update the **Current task** section above: point it at the next brief in
   `docs/briefs/`, or write `None — awaiting next brief` if the folder is
   empty. Never leave it pointing at finished work.
4. Update the **Build state** section to reflect what now exists.
5. If implementation revealed the spec is wrong or out of date, add or amend
   the amendment note in `docs/postal-pipeline-build-spec.md` so the two
   documents don't drift. Do not silently rewrite the spec's design — note the
   discrepancy and flag it to the operator.

Treat this as part of finishing, the same as passing the tests. A stale
Current task section is the main way this repo becomes confusing.

## Build state

- **Phase 0 — prospect finder:** built, one live run completed, and amended by
  the visual-quality brief. Discovery scores on staleness (viewport tag, media
  queries, copyright age, table layout, Flash, HTTPS, platform hint); mobile
  PageSpeed score is retained as a column and a tiebreaker only. Screenshots
  are captured per candidate and cached by `place_id`; `contactsheet.py`
  renders a self-contained cull sheet whose exported `decisions.json` imports
  via `cull.py --import`.
  - Also present beyond the spec's phase 0: `combine.py` (merge batches by
    niche or town, re-scoring under current weights), website contact lookup
    reconciled against Companies House directors (spec section 5 step 2), and
    `run.py` — a menu wrapper over the CLIs for evening use. The CLI scripts
    remain argparse-first and scriptable as the conventions require; the menu
    only drives them.
- **Phase 1 — letter generation and batch one:** not started.
- **Phase 2 — template-fill previews (tier 2):** not started. Delivery
  substrate settled 2026-09-02: Webflow on the **Freelancer** Workspace
  tier (the floor for code export), so tier 1 and tier 2 fill a Webflow
  HTML export rather than a bought template — the mockup must match the
  live product. Master designs are built **without CMS Collections**, or
  the export stops matching the live site. One master per layout shape,
  not one Webflow site per prospect. See the amendment in section 7 of the
  spec; the one open question there is how clients edit after handover.

## Conventions

- Python 3.11. Standard library plus: `requests`, `beautifulsoup4`,
  `playwright`, `jinja2`, `weasyprint`, `qrcode`, `anthropic`.
- **Fail open on every external signal.** A failed fetch, timeout or missing
  element must never add points to a lead score or silently inflate a result.
  Absent evidence is not evidence.
- **Cache everything external.** Places API results, PageSpeed results and
  screenshots all cache for 30 days keyed by `place_id`. Re-running a batch
  must not re-spend API quota. Every cache gets a `--refresh-*` flag.
- API keys from environment variables only. Never commit a key, never write one
  into a config file in the repo.
- Batch data lives under `batches/<date>_<niche>_<town>/` and is gitignored —
  it contains third-party business data.
- Scripts are CLI-first with `argparse`, no interactive prompts. The operator
  runs these in the evening and wants them scriptable.
- Prefer plain files (CSV, JSON, HTML) over databases. The operator must be
  able to open any artefact and read it.

## Data handling

This pipeline processes information about real businesses and, in some cases,
named individuals (company directors). Treat it accordingly:

- Never commit `batches/` content.
- Do not add any electronic outreach — no email sending, no SMS, no form
  submissions to prospect websites. Contact is by post, or by reply to an
  opt-in the recipient initiated.
- Preview pages carry a banner making clear they are unsolicited concepts and
  nothing is live.
- Preview pages expire after 60 days.

## Working style

- Read the active brief in full before writing code.
- Acceptance criteria in a brief are the definition of done — work through them
  explicitly and say which pass.
- If a brief is ambiguous or looks wrong given what's in the repo, say so
  rather than guessing. Design decisions are made in conversation with the
  operator, not inferred at implementation time.
- Keep commits small and scoped to one brief section.
