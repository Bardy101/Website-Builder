# Local Website Audit + Mockup — Build Plan

An inbound-led web design service. Instead of cold outreach, local business
owners come to a self-serve "website health check," get a genuinely useful
report (in exchange for an email — which also gives you lawful consent to
follow up), and are then offered a free mockup that converts them into clients.
Public "redesign" content and a fast, well-ranked site of your own drive the
traffic in.

---

## 1. The funnel at a glance

```
   TRAFFIC SOURCES                  THE TOOL                    CONVERSION
 ┌─────────────────┐        ┌──────────────────────┐      ┌────────────────┐
 │ Public redesigns│        │  1. Enter URL        │      │ Free mockup    │
 │ Your own site   │  ───►  │  2. Instant scan     │ ───► │ (Lovable)      │
 │ Google Business │        │  3. Email gate       │      │ 15-min Zoom    │
 │ Referral partners│       │  4. Full report +    │      │ Quote on call  │
 │ Local FB groups │        │     AI suggestions   │      │ Close 30-50%   │
 └─────────────────┘        └──────────────────────┘      └────────────────┘
                                      │
                                      ▼
                            Lead stored + consent
                            captured → warm follow-up
```

The key idea: every channel points at the tool, the tool captures the lead and
the permission, and the mockup does the selling. No cold contact anywhere.

---

## 2. Phase 1 — The Instant Audit Tier (the MVP)

This is the only thing you build first. Everything else hangs off it.

### Data flow

```
 User enters URL
        │
        ▼
 [Validate + normalise URL] ── invalid ──► friendly error
        │ valid
        ▼
 ┌──────────────────────────────────────────────┐
 │  AUDIT PIPELINE (run in parallel)             │
 │                                               │
 │  A. PageSpeed Insights API  → perf, SEO,      │
 │     (Lighthouse)              accessibility,  │
 │                               best-practices, │
 │                               Core Web Vitals │
 │                                               │
 │  B. Fetch + parse HTML      → title, meta,    │
 │     (requests + BS4)          H1, viewport,   │
 │                               favicon, alt    │
 │                               text, HTTPS,    │
 │                               contact/social  │
 │                                               │
 │  C. (optional) screenshot   → mobile render   │
 │     SSL check, WHOIS age                      │
 └──────────────────────────────────────────────┘
        │
        ▼
 [Scoring engine] → A–F grades across 5 buckets:
        │           Speed · Mobile · Findability · Trust · Content
        ▼
 [Show top-line score]  ◄── the hook: visible immediately, free
        │
        ▼
 [EMAIL GATE] enter name + email to unlock full report  ◄── lead + consent
        │
        ▼
 [Claude API] turn raw findings into plain-English,
        │     prioritised, revenue-framed suggestions
        ▼
 [Assemble report] → shareable HTML page + optional PDF
        │
        ├──► Store lead (URL, email, scores, consent, timestamp)
        ├──► Email report (Amazon SES)
        └──► Report ends with CTA: "Want to see what it could look like?"
```

### The five score buckets (owner-friendly, no jargon)

| Bucket        | What it really measures                  | Source            |
|---------------|------------------------------------------|-------------------|
| Speed         | Load time, Core Web Vitals               | PageSpeed/Lighthouse |
| Mobile        | Responsive, viewport, tap targets        | PageSpeed + parse |
| Findability   | Title/meta/H1/structured data, indexable | Parse + Lighthouse SEO |
| Trust         | HTTPS, SSL validity, contact info, reviews | SSL check + parse |
| Content       | Clear services, CTAs, freshness, images  | Parse + Claude    |

Keep the grades benefit-framed: not "LCP is 4.2s" but "Your site takes twice as
long to load as customers expect — most leave before it finishes."

### Components & recommended stack (tailored to your skill set)

| Layer            | Choice                          | Why                                  |
|------------------|---------------------------------|--------------------------------------|
| Web app          | **Flask** (Python) + Jinja      | Same shape as your RAG app           |
| Audit data       | Google **PageSpeed Insights API** | One call gives 80% of the scorecard, free |
| Page parsing     | `requests` + **BeautifulSoup**  | Pull on-page + brand assets          |
| No-site path     | Google **Places API** (Place Details) | Name, category, reviews, photos |
| Suggestions      | **Claude API**                  | Plain-English, prioritised advice    |
| Hosting          | **AWS Lambda + API Gateway** (or small EC2/Lightsail) | Serverless scales to zero; EC2 if you prefer always-on simplicity |
| Lead store       | **DynamoDB** (or SQLite/RDS)    | Serverless, you already know AWS     |
| Email delivery   | **Amazon SES**                  | Cheap, you're already in AWS         |
| Secrets          | AWS Secrets Manager / env vars  | Keep API keys out of code            |

### Claude prompt design (the suggestions step)

Feed it the structured audit JSON + parsed content, and instruct explicitly:

- No jargon. Tie every issue to lost customers, trust, or money.
- Prioritise the top 3 fixes; list the rest briefly.
- End with a one-paragraph "what a rebuilt site would change" that sets up the
  mockup offer — without mentioning AI or any tool.

This mirrors the anti-buzzword discipline from the original method, just pointed
at a report instead of a cold message.

### Cost & abuse controls (build these in from day one)

- Rate-limit by IP; cap audits per minute.
- Cache results per domain for ~24h so repeat scans don't re-bill APIs.
- Gate the expensive bits (Claude call, full report) behind the email step.
- Watch free-tier quotas on PageSpeed and Places APIs; SES and Claude cost
  pennies per report if prompts are tight.

---

## 3. Phase 2 — The Mockup Hook

The report's closing CTA → "I'll build you a free mockup." Don't automate this
on day one.

- **Start manual.** When a lead clicks through, you extract their brand and
  content (logo, colours, services — already parsed in Phase 1) and build the
  mockup in Lovable in a few minutes. Optional 10-second Higgsfield walkthrough.
- **No-website businesses** are your best mockup targets — nothing-to-something
  is the most dramatic before/after. Pull their details from the Places API.
- **Automate later, with templates.** Once it's working, replace live build with
  3–4 of your own industry templates auto-populated with their scraped brand and
  content. Reliable, fast, cheap — avoid live LLM code-generation for a free tier.

---

## 4. Phase 3 — Distribution (what drives traffic to the tool)

- **Your own site:** lightning-fast, ranks for "web design [your town]" and a
  chosen niche, with the audit tool as the centrepiece. The site itself is your
  proof of work.
- **Google Business Profile + reviews:** get into the local map pack early.
- **Public redesign content (gift-first):** redesign a local business's site for
  free, send it privately, and — if they're pleased — post the before/after with
  their blessing. Each post ends "run your own free check at [tool]." One a week.
- **Referral partners:** accountants, bookkeepers, print/signage shops, branding
  freelancers. The warmest inbound there is. Coffee with 3–4 of them.

---

## 5. The legal win (why inbound solves the earlier problem)

Because owners enter their own details to get the report, **they initiate contact
and opt in.** That converts the PECR/UK GDPR cold-outreach problem into compliant,
warm follow-up. Store the consent (timestamp + what they agreed to), include your
identity and an opt-out in every email, and keep a short privacy notice on the
tool page. That's the whole compliance burden — and it's light.

---

## 6. Suggested roadmap

| When        | Focus                                                                 |
|-------------|-----------------------------------------------------------------------|
| Week 1–2    | Build the audit MVP: PageSpeed + parse + score + Claude suggestions + emailed report. Run it from your laptop first if needed. |
| Week 3–4    | Polish the front end + email gate, host on AWS, stand up your own one-page site with the tool embedded, set up Google Business Profile. |
| Month 2     | Start the weekly public-redesign content cadence; do your first manual mockups for warm leads; line up 2–3 referral partners. |
| Month 3+    | Add the no-website / Places path; templatise the mockup; iterate on what converts. |

### Metrics to watch
Tool visits → email captures → mockup requests → calls booked → closes.
Plus cost-per-lead and which traffic source actually converts.

---

## 7. Open decisions to make next

1. **Hosting model:** serverless (Lambda) for cost/scale vs. a small always-on
   instance for mental simplicity — which fits how you like to work?
2. **Starting niche:** which single vertical to anchor the tool and content
   around first (dentists / salons / law firms / trades / venues)?
3. **Build order inside the MVP:** the scoring engine vs. the Claude layer —
   which to prototype first?
