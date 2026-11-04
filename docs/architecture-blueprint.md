# Customer Value Management & Engagement Hub
## Architecture Blueprint — Adobe Commerce Cloud Edition

**Version** 0.9 · **Date** 2026-11-04 · **Status** Draft for review
**Scope** Internal CVM + engagement platform ("local MoEngage") for an Adobe Commerce 2.x Cloud storefront, integrated with the existing analytics dashboard and a data-science team running local LLMs.

---

## 0. Executive summary

You already have two assets most companies lack when they start this build:

| Asset | What it gives you | How the hub uses it |
|---|---|---|
| **Analytics dashboard connected to Magento** | A working, validated data pipeline + a single source of truth for commercial KPIs | The hub **reads** from the same curated mart instead of re-ingesting Magento. No duplicate ETL, no number drift. |
| **DS team with local LLMs** | Heavy modelling (RFM, CLV, churn, elasticity, affinity) and generative idea/copy creation, on-prem, no PII leaving your infra | The hub is a **consumer** of their outputs — it does not model. It turns model output into audiences, offers, messages, journeys and measurement. |

So the product boundary is clean:

> **Data science produces scores, signals and ideas. The CVM Hub owns decisions, execution, governance and measurement.**

The build is therefore ~70% product engineering (audiences, campaigns, journeys, offers, channels, measurement) and ~30% integration. That is the opposite of most "build your own MoEngage" projects, which drown in data plumbing first.

**Recommended shape:** a dedicated application (Laravel + MySQL/Postgres + Redis + a queue worker) that:
- reads the **gold mart** your dashboard already uses (read-only),
- pushes **4 things** back to Magento (customer tags/attributes, coupon batches, cart price rules, personalised blocks),
- sends through **4 channel adapters** (email, web push, in-app/on-site, SMS-vendor),
- stores every exposure and outcome so that **incremental lift** can be measured, and
- logs every LLM-proposed idea with its hypothesis, expected uplift and realised result — a closed learning loop that makes the idea engine better each quarter.

---

## 1. What "CVM + engagement hub" means here

Five capabilities, in dependency order. Each is independently shippable.

| # | Capability | User-facing question it answers |
|---|---|---|
| 1 | **Customer value intelligence** | Who are our customers worth investing in, and what are they worth? (RFM, CLV, churn risk, lifecycle) |
| 2 | **Audience building** | Who exactly do I target, how many are reachable, and what does it cost me? |
| 3 | **Engagement idea engine** | What should I do with them? (LLM-proposed, historically-grounded, human-approved) |
| 4 | **Execution** | Campaigns, journeys, offers/promotions, recommendations — in the right channel, with caps and consent |
| 5 | **Measurement & learning** | Did it work, was it incremental, what did it cost, and what do we do next time? |

Non-goals for v1 (deliberate): full visual journey canvas with drag-drop branching, real-time 1:1 personalisation at every page slot, ad-network activation, and a general-purpose CDP for non-Magento sources. Add later.

---

## 2. Build vs. buy — decision record

| Factor | Buy (MoEngage / Insider / CleverTap / Braze) | Build (this blueprint) | Local vendors (Egypt/MENA) |
|---|---|---|---|
| Time to first campaign | 3–6 weeks | 10–14 weeks for capability #1–4 | 4–8 weeks |
| Annual cost @ ~100k contacts | $30k–$120k+ | ~1–2 FTE + infra (~$40–80k) | $8k–$25k |
| Magento depth | Generic REST integration | Native: attributes, segments, cart rules, coupons, GraphQL | Varies |
| Data residency | Data leaves to vendor cloud | Stays in your VPC (Egypt PDPL-friendly) | Usually local |
| LLM idea generation | Black box, no custom | Full control, your prompts, your eval, your priors | Rare |
| Cost per SMS/email | Vendor markup | At cost | At cost |
| Vendor risk | High switching cost | Internal capability | Medium |

**Verdict:** build, but *buy or reuse* three components rather than writing them: the **email/SMS delivery** (your existing vendor + an SMTP/API provider), **push delivery** (Firebase Cloud Messaging — free), and **BI visualisation** if you want ad-hoc exploration (keep the dashboard you have).

The real justification for building is not cost — it is that **incremental-lift measurement and the LLM idea loop are the product**, and no off-the-shelf tool in your budget will let you own either.

---

## 3. System architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          ADOBE COMMERCE CLOUD 2.x                            │
│  Storefront (Luma/PWA)   Admin   Customers   Orders   Carts   Cart Price Rules │
│  Customer Segments (Commerce)  Dynamic Blocks   Coupon Codes                  │
└───────┬───────────────────────────────┬──────────────────────┬───────────────┘
        │ REST / GraphQL (read)         │ REST (write-back)    │ Adobe I/O Events
        │ + bulk async export           │ tokens, coupons      │ / Commerce Webhooks
        ▼                               ▲                      ▼
┌───────────────────────┐       ┌───────┴────────┐      ┌──────────────────────┐
│  EXISTING ANALYTICS   │       │  WRITE-BACK    │      │  EVENT INGESTION     │
│  PIPELINE + DASHBOARD │       │  SERVICE       │      │  API (storefront     │
│  (yours, unchanged)   │       │  (adapter)     │      │  tracker + webhooks) │
│  → GOLD MART          │       └───────▲────────┘      └──────────┬───────────┘
└───────┬───────────────┘               │                          │
        │ READ-ONLY (curated views)     │                          │
        ▼                               │                          ▼
┌───────────────────────────────────────┴──────────────────────────────────────┐
│                            CVM HUB  (Laravel app)                            │
│                                                                              │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌───────────┐ ┌─────────────┐  │
│  │ Profile &  │ │ Audience   │ │ Idea       │ │ Offers &  │ │ Orchestration│ │
│  │ Score      │ │ Builder    │ │ Engine     │ │ Promo     │ │ (campaigns,  │ │
│  │ Store      │ │ (segments) │ │ (LLM)      │ │ Engine    │ │  journeys)   │ │
│  └────────────┘ └────────────┘ └─────┬──────┘ └───────────┘ └──────┬──────┘  │
│  ┌────────────┐ ┌────────────┐       │        ┌───────────┐        │         │
│  │ Eligibility│ │ Recommend. │       │        │ Experiment│        │         │
│  │ Fatigue    │ │ / NBA API  │       │        │ & Holdout │        │         │
│  │ Consent    │ │            │       │        │ Attribution│       │         │
│  └────────────┘ └─────┬──────┘       │        └───────────┘        │         │
└───────────────────────┼──────────────┼───────────────┬─────────────┼─────────┘
                        │              │               │             │
                        ▼              ▼               ▼             ▼
                 ┌───────────┐  ┌────────────┐  ┌──────────────────────────┐
                 │ Storefront│  │ DS / LLM   │  │ CHANNEL ADAPTERS          │
                 │ (GraphQL  │  │ SERVICES   │  │ email · web-push · in-app │
                 │  + block) │  │ (local,    │  │ SMS-vendor · on-site slot │
                 └───────────┘  │  GPU box)  │  └──────────────────────────┘
                                └────────────┘
```

**Three rules that keep this clean**

1. **One direction of truth.** Magento → analytics mart → hub. The hub never becomes a second source of truth for orders or revenue.
2. **Write-back is minimal and idempotent.** Four write paths only: customer attributes/tags, coupon batches, cart price rules, personalised blocks. All retried with idempotency keys.
3. **The hub is the only thing that talks to customers.** Magento transactional emails stay transactional; all marketing sends originate in the hub so exposure is logged in one place.

---

## 4. Layer-by-layer design

### 4.1 Data acquisition

| Source | Mechanism | Frequency | Notes |
|---|---|---|---|
| Customers, orders, invoices, credit memos, carts, products | **Reuse the existing pipeline** into the gold mart | Hourly/nightly | Do not build a second extractor. If the mart lacks storefront behaviour, add it there, not in the hub. |
| Storefront behaviour (view_item, search, add_to_cart, remove_from_cart, wishlist, checkout steps) | Lightweight JS tracker → `POST /v1/events` on your ingestion API | Real time, batched 5s | Anonymous `anonymous_id` + `customer_id` when logged in. Store raw events 90 days, aggregates 720 days. |
| Near-real-time business events (order placed, refund, customer registered, cart abandoned) | **Adobe Commerce Webhooks / Adobe I/O Events** (available on Cloud) | Event-driven | Use for triggers (e.g. "order delivered + 7 days"), never for reporting. |
| Product catalogue + prices + stock | Gold mart, refreshed | Hourly | Needed for recommendation eligibility filtering. |
| Consent & preferences | **Hub owns this** (source of truth), mirrored to Magento customer attributes | Real time | Consent must be revocable in one place. |
| SMS/email engagement (delivered, opened, clicked, bounced, unsub) | Provider webhooks → hub | Real time | Adapters normalise into one `message_event` table. |

**Anti-patterns to reject now:** direct DB replication of Magento (Cloud gives you no reliable logical replication and will bite you on version upgrades); scraping the admin; storing card data anywhere near marketing systems.

### 4.2 Golden profile store (mini-CDP)

One row per human, not per account.

```
customer (hub-local)
  id                    hub UUID
  magento_customer_id   nullable (guest checkouts exist)
  email_hash / email_enc, phone_hash / phone_enc   ← tokenised; plaintext in a KMS-backed vault
  identity_links[]      anonymous_id, device_id, store_view, social ids
  first_seen, last_seen, first_order_at, last_order_at
  orders_count, lifetime_revenue_net, refunds, aov, discount_share
  preferred_locale, city, preferred_store_view
  lifecycle_stage       new | active | at_risk | dormant | lost | winback
  consent_email, consent_push, consent_inapp, consent_sms  (+ lawful_basis, source, captured_at)
  suppressed            opt-out / complaint / hard bounce / deletion request
```

**Identity resolution** rules (in this order): exact email hash → exact phone hash → logged-in session link → device id + same-day cart signature. Merge with survivorship rules; keep an `identity_merge_log` for auditability. Do not attempt probabilistic matching in v1.

### 4.3 Score store (where your DS team plugs in)

The hub stores scores as **versioned, timestamped, source-attributed rows**, never as columns on the profile:

```
customer_score (customer_id, score_name, score_value, score_json, model_version,
                scored_at, valid_until)
```

Minimum score set for v1 — define these with the DS team, do not let the list grow unchecked:

| Score | Type | Use in hub |
|---|---|---|
| `rfm` | `{r,f,m}` 1–5 quantiles | Segment definitions, RFM matrix |
| `segment_rfm` | string (11 canonical segments) | Default audience source |
| `clv_12m` | currency, predicted | Value tier, budget allocation |
| `churn_prob_90d` | 0–1 | Retention audiences, risk-weighted priority |
| `next_purchase_date` | date (BTYD/pareto-nbd) | Journey timing, "due to buy" triggers |
| `price_sensitivity` | 0–1 | Offer depth selection |
| `discount_dependency` | 0–1 | Margin guardrail |
| `category_affinity` | `[{cat, weight}]` | Personalisation, offer selection |
| `basket_affinity` | `[{sku, weight}]` | Recommendation candidates |
| `propensity_*` | 0–1 per campaign goal | Prioritisation within audience |

Rules: scores are **immutable once written**; recompute = new row. Every campaign records the model version it used, so you can explain a result six months later.

### 4.4 Engagement idea engine (the differentiator)

This is the piece that makes the hub more than a send tool, and it is where your local LLM earns its place. Design it as a **retrieval-grounded, schema-constrained generator with a human gate and a feedback loop** — not as "ask GPT for campaign ideas".

```
 signal pack (per segment, computed by DS)      ┌────────────────────────────┐
 ─────────────────────────────────────────       │  LOCAL LLM (vLLM/Ollama)   │
 • segment size, reachable %, value band        │  prompt = role + guardrails │
 • 30/90/180-day trend deltas (orders, AOV)     │  + signal pack + retrieved  │
 • top/bottom categories, price bands           │    past-campaign priors     │
 • churn drivers (SHAP top features)            │  + output JSON schema       │
 • basket affinity pairs                        └──────────────┬─────────────┘
 • contact history (frequency, last touch,                    │
   fatigue score, response rates per channel)                 ▼
 • inventory pressure / margin targets          Candidate ideas (5–8), each:
 • past campaigns on this segment               {hypothesis, mechanic, channel,
                                                 offer, urgency, expected_uplift,
                                                 cost_estimate, risk_flags,
                                                 guardrails, success_metric,
                                                 min_sample_size, rationale}
                                                              │
                                    ┌─────────────────────────▼─────────────────┐
                                    │  SCORER (deterministic, not LLM)          │
                                    │  score = 0.35·prior_lift_similarity       │
                                    │        + 0.20·reachable_audience_size     │
                                    │        + 0.20·expected_margin_impact      │
                                    │        + 0.15·strategic_fit               │
                                    │        - 0.10·risk_penalty                │
                                    └─────────────────────────┬─────────────────┘
                                                              ▼
                                    HUMAN REVIEW (marketer) → approve / edit / reject
                                                              ▼
                                    CAMPAIGN DRAFT → journey / offer / audience auto-filled
                                                              ▼
                                    RESULT (measured lift, not vanity) → idea_library
                                                              ▼
                                    next prompt retrieves these as priors  ⟲
```

**Why the scorer must not be the LLM.** LLMs are excellent at generating plausible mechanics and copy, and terrible at arithmetic and self-assessment. Keep the numbers in code. The LLM proposes the *what* and *why*; your code and history supply the *how much*.

**Prompt hygiene (non-negotiable):**
- No raw PII in the prompt. Send segment aggregates, hashed ids, category names — never names, emails or phones.
- Constrain output with **JSON Schema / grammar-constrained decoding** (vLLM `guided_json`, Outlines, or Ollama `format`). Reject non-conforming output and retry once, then fail loudly.
- Temperature 0.2–0.4. Seed the run and store the seed.
- Log the full prompt, retrieved priors, model name/version, latency and token count with each idea batch. Ideas are auditable artefacts.
- **Draft copy never auto-sends.** Any generated customer-facing text passes a human, plus an automated check for banned claims, competitor mentions, price mismatches with Magento, and Arabic/English grammar.

**Idea library as an asset.** After ~6 months you own a table of `(segment archetype × mechanic × channel × offer depth) → measured lift` specific to *your* customers. That table is worth more than the LLM, and it is what a vendor cannot sell you.

### 4.5 Audience builder

Two layers, both needed:

- **Dynamic audiences** (rule-based, evaluated by the hub): attribute + score + event predicates, e.g. *"churn_prob_90d > 0.6 AND orders_count ≥ 2 AND last_order_at < 120 days AND consent_email = 1 AND NOT suppressed"*. Materialised nightly into `audience_member` for fast campaign assembly; estimated daily.
- **Synced audiences** (pushed to Adobe Commerce **Customer Segments**): the top ~20 commercially important audiences only, used for on-site dynamic blocks and cart price rules. Commerce's segment engine is rule-based on customer/order attributes — keep the rules simple and one-way from hub to Magento.

Every audience view shows: total, reachable per channel, overlap with last 30 days' contact, and expected revenue at risk. Marketers must *see* fatigue before they send.

### 4.6 Offers & promotion engine

Separate "offer" (the value proposition) from "mechanic" (how it is implemented).

```
offer_ladder (library, ordered by margin cost)
  free_shipping_threshold > gift_with_purchase > bundle > tiered_spend
  > fixed_amount_off > percent_off > loyalty_points_multiplier
```

**Guardrails enforced in code, not in guidelines:**
- `max_discount_depth` per segment and per category (from `price_sensitivity` + `discount_dependency`)
- margin floor: reject offers that push category contribution margin below X% at expected uplift
- cannibalisation check: exclude customers who would have bought at full price (use holdout, not guesswork)
- one-offer-per-customer-per-28-days cap; exclusion of customers in an active experiment
- **every coupon is generated, not shared**: `POST /rest/V1/coupons` per customer or bulk generate, tied to `campaign_id` in the coupon's `usage_limit`/metadata → clean attribution
- all offers must be expressible as Magento **cart price rules** so storefront, app and CS all see the same price

**Discount simulator (ship in v1, it saves money immediately):** given audience size, baseline conversion, AOV, proposed depth and margin, show incremental orders, incremental gross profit, discount cost, and break-even uplift. If break-even uplift > historical elasticity for that segment, block with an explanation.

### 4.7 Recommendation / next-best-action service

Serve two things via one API, `GET /v1/nba/{customer_id}?context=email|onsite|push`:

1. **Next best product(s)** — hybrid: candidate generation (basket affinity + category affinity + popularity in segment + inventory-eligible) → ranking (small model or your DS team's ranker) → **reason string** (short, LLM-generated, limited to the actual signal, e.g. "Because you bought the 60ml serum").
2. **Next best action** — channel + offer + timing, chosen by expected value: `p(convert) × E[margin] − channel_cost − discount_cost`, then passed through eligibility/caps. If nothing clears the bar, return `null` and the hub sends nothing. **"Don't send" must be a first-class outcome**, or you train customers to ignore you.

On-site delivery: a small Magento module (or PWA drop-in) calls the hub with the session token, gets JSON, renders a block. Cache 5 minutes per customer+context. Never make a storefront page wait on the hub — render the slot client-side or use Magento's Dynamic Blocks as fallback content.

Evaluation: offline `recall@5`, `NDCG@10` against held-out purchases; online A/B with revenue per session as the primary metric. Do not ship a recommender without the offline eval harness — it is the only way to know if a "better" model actually is.

### 4.8 Orchestration

Journey model (keep it boring and debuggable):

```
trigger  →  audience gate  →  [wait]  →  branch(es)  →  action(channel, content, offer)
         →  goal event  →  exit rules  →  caps
```

Ship v1 with: entry triggers (event, segment, scheduled, manual), waits (delay / until-date / until-event), branches (split %, if/else on profile or score), actions (email, push, in-app, SMS, hub-internal like "tag customer" or "apply coupon"), goal (order, add-to-cart, page visit), exit (purchased, unsubscribed, suppressed).

Concurrency rules matter more than the builder UI: global max contacts/day per customer, per-channel caps, quiet hours per locale, exclusion of customers in another journey touching the same goal, and a hard kill switch on every journey.

Runner: a queue worker (Redis + Laravel Horizon) processing a `journey_step` table with `scheduled_at`; idempotent per `(journey_run_id, node_id)`. At your scale (likely <500k contacts) this is a cron + queue, not Kafka. Do not build streaming infrastructure you don't need.

### 4.9 Channel adapters

One interface, four implementations:

```
interface ChannelAdapter {
  send(Message $m): SendReceipt      // provider message id
  handleWebhook(Request $r): array   // normalised events
  capabilities(): ['rich_html','deep_link','media','template','carousel']
}
```

| Channel | Implementation | Key detail |
|---|---|---|
| **Email** | Your existing provider (SMTP API); MJML templates; hub-owned unsubscribe + preference centre | Authenticate SPF/DKIM/DMARC on your sending domain. Bounce/complaint webhooks must auto-suppress. |
| **Web push** | Firebase Cloud Messaging (free) + service worker; deep links into the Magento storefront | Permission prompts must be consent-gated and rate-limited; store `push_token` per device, expire stale tokens. |
| **In-app / on-site** | Hub feeds REST + Magento module or PWA component; also Adobe Commerce Dynamic Blocks for Commerce segments | Poll or long-poll, 5-minute cache, always have a fallback block. |
| **SMS** | **Adapter to your external vendor.** Adobe Commerce has *no native SMS marketing gateway* — the built-in Twilio integration is for 2FA phone calls only | Arabic/English templates, per-country sender ID rules (Egypt requires registered sender IDs), cost per message in the offer P&L, hard cost cap per campaign. |

Add a `ChannelCostRate` table so every campaign shows channel cost, not just discount cost. Otherwise reported "revenue" will look great while profit goes backwards.

### 4.10 Measurement & experimentation

The hub's job is to prove incrementality, not to claim credit.

- **Exposure log.** `message_event(customer_id, campaign_id, variant, event_type, at, provider_id)` — one normalised table for all channels. Without this, nothing else works.
- **Holdout groups.** Every campaign ≥ a size threshold auto-allocates 5–10% held out (or a matched control). Incremental revenue = treated revenue − (holdout revenue × scaling).
- **Attribution** for reporting (7-day click / 1-day view default for email/push), but **decisions are made on holdout lift**. Show both; never mix them in one chart.
- **Campaign P&L**: incremental revenue − COGS − discount cost − channel cost − returns/refunds = incremental contribution. Rank campaigns by this, not by revenue.
- **Revenue at risk / retained revenue**: for retention activity, the metric is *revenue retained vs. predicted churn baseline*, not revenue attributed.
- **Experiment registry**: every A/B (subject line, offer depth, channel mix, send time) recorded with hypothesis, sample size, decision, and outcome — so you stop re-running the same test.
- **Cohort retention curves** (month-0 to month-12 by acquisition cohort) belong on the Overview page. That is the number CVM actually moves.

### 4.11 Governance, privacy, LLM safety

Egypt **PDPL (Law 151/2020)** treats marketing contact as processing requiring a lawful basis, and gives data-subject rights (access, correction, erasure, objection). Practical implementation:

- Consent captured at source with `{purpose, channel, lawful_basis, source, text_version, captured_at, ip}`; **preference centre in the hub** as the single place to change it.
- Every send checks `consent` **at send time**, not at audience-build time.
- Erasure request → suppression in hub + anonymisation log + `DELETE`/anonymise in mart; keep only aggregate counters.
- PII encrypted at rest (KMS), tokenised for joins; marketing users see masked data, unmask is an audited action with a reason.
- RBAC: `viewer / analyst / marketer / approver / admin`, plus **maker-checker on any send above a threshold** and on any discount above depth limit.
- Immutable audit log for: consent changes, audience exports, sends, offer creation, price-rule changes, LLM prompt/response pairs.
- Cross-border transfer note: your push/email providers host outside Egypt. Either pick regional endpoints or document the transfer basis. This is also a strong argument for keeping modelling on-prem.
- **LLM safety rails:** no PII in prompts, JSON-schema output, human approval before any customer-facing text, blocked-claims list, price/offer values injected from Magento at render time (never from LLM text), and a per-run cost/latency budget.

---

## 5. Data model (hub-owned tables)

| Table | Purpose | Notes |
|---|---|---|
| `customer` | golden profile | tokenised PII |
| `customer_identity` | identity links | merge log |
| `customer_consent` | purpose × channel × basis | versioned |
| `customer_score` | versioned score rows | immutable |
| `event` | raw behavioural events | partitioned monthly, 90-day retention |
| `audience` / `audience_member` | saved segments | materialised nightly |
| `segment_definition` | rule JSON | DSL, validated |
| `product` / `product_affinity` | catalogue mirror + pairs | refreshed hourly |
| `offer`, `offer_ladder`, `coupon_batch` | promotions | links to Magento rule id |
| `idea`, `idea_review`, `idea_prior` | LLM output + human decision + measured outcome | the learning loop |
| `campaign`, `campaign_variant`, `campaign_schedule` | execution | holds model versions used |
| `journey`, `journey_node`, `journey_run` | orchestration | per-step idempotency |
| `message_template` | channel-specific content | MJML / push / SMS / in-app |
| `message_event` | sends, deliveries, opens, clicks, conversions, bounces | the measurement spine |
| `suppression` | unsub, complaint, hard bounce, erasure | checked at send time |
| `experiment`, `experiment_assignment` | A/B + holdout registry | |
| `channel_cost_rate` | cost per message per channel/country | drives P&L |
| `audit_log` | who did what, when | append-only |
| `integration_sync` | Magento/webhook sync state | idempotency keys, retries |

Retention policy: `event` 90 days raw + daily aggregates forever; `message_event` 24 months; scores 24 months; `idea` forever (it is your IP).

---

## 6. Magento integration specifics (Adobe Commerce Cloud)

**Auth & access**
- Create a dedicated Adobe Commerce **integration** with least-privilege ACLs (Customers read, Orders read, Cart Price Rules read/write, Coupons read/write, Catalog read, Customer Attributes read/write). Token via `/rest/V1/integration/admin/token`, cached, rotated on schedule.
- Cloud environments have API rate limits and a shared-infrastructure SLA — **never** run bulk extraction against production during peak. Use the async bulk endpoints (`/rest/async/V1/...`) for catalogue and customer bulk operations.
- No direct database access on Cloud environments. Design accordingly: no triggers, no stored procedures, no views in Magento.

**Read paths** — prefer the mart. If the hub needs something the mart lacks, add it to the mart's pipeline, not to the hub. Magento reads are for *validation* (does this customer still exist, is this SKU in stock) not for reporting.

**Write-back paths (the complete list)**

| Write | Endpoint | Idempotency |
|---|---|---|
| Tag customer with hub segment/tier | `PUT /rest/V1/customers/{id}` on custom attributes (`cvm_tier`, `cvm_rfm_segment`, `cvm_churn_band`, `cvm_nba_offer`) | customer id + run id |
| Generate coupon batch | `POST /rest/V1/coupons` per code, or bulk generate where available in your version (2.4.7+) | `campaign_id` prefix in the code + `integration_sync` key |
| Create/update promotion | `POST/PUT /rest/V1/salesRules` | external reference attribute |
| Sync audience to Commerce Customer Segments | Admin or API; keep segment count ≤ ~20 | hash of rule definition |
| Personalised content slot | Magento module → hub `GET /v1/nba` | 5-min cache |

Custom attributes should be created once via a setup data patch in a small Magento module you own (versioned in git), not clicked in admin — so staging and production stay identical.

**Events** — subscribe to order/customer/cart/refund events via Adobe Commerce Webhooks (or Adobe I/O Events → your ingestion endpoint). Handle at-least-once delivery: dedupe on `event_id`. Triggers only; never report from the event stream.

**Storefront tracker** — a tiny script (or PWA plugin) posting to your ingestion API, with: `anonymous_id` (first-party cookie + localStorage), `customer_id` when authenticated, `event_name`, `properties`, `consent_state`, `store_view`, `locale`. Under 3KB, async, no blocking, respects consent before setting the cookie.

---

## 7. Non-functional requirements

| Concern | Target |
|---|---|
| Campaign send throughput | 50k messages/hour per channel (queue workers scale horizontally) |
| Audience preview latency | < 3s for a full audience estimate; materialised members < 1s p95 for 500k |
| Storefront NBA API | p95 < 120ms cached, < 400ms cold; must degrade to `null`, never error the page |
| Hub availability | 99.5% (marketing system, not checkout) — Magento must never depend on it for browsing or purchase |
| Score freshness | daily batch; behavioural aggregates hourly; no real-time scoring in v1 |
| Environments | local / staging / production, with a **sandbox Magento store view** for end-to-end send tests |
| Kill switches | per channel, per journey, per campaign, global |
| Data deletion SLA | 30 days end-to-end including mart and vendor suppression lists |

---

## 8. Phased roadmap

Assumes a team of **1 senior backend (Laravel), 1 frontend, 1 data scientist (part-time shared with dashboard), 1 DevOps (0.3), 1 marketing owner (product owner)**. Effort in person-weeks.

### Phase 0 — Foundations (3–4 weeks)
Data contracts, environments, skeleton.
- Confirm the gold-mart schema with the DS team; write the **analysis contract** (`docs/02-analysis-and-llm-contract.md`) and freeze v1 fields.
- Hub app skeleton: auth, RBAC, audit log, `customer` + `customer_consent` + `customer_score` tables, score ingestion endpoint.
- Magento integration account + custom attributes module + webhook subscriptions in staging.
- Event ingestion API + storefront tracker.
- **Done when:** a nightly job loads the full customer base with RFM + CLV + churn into the hub, and the Overview page shows real numbers.

### Phase 1 — Value intelligence + first campaigns (6–8 weeks)
- Profile & score store UI, RFM matrix, segment explorer, Customer-360 drawer.
- Audience builder (rules DSL, estimates, reachability) + 20 synced Commerce segments.
- Email channel adapter end-to-end (template, send, webhooks, suppression, unsubscribe/preference centre).
- In-app/on-site notification feed (simplest channel after email).
- Exposure logging + basic campaign reporting + holdout allocation.
- **Done when:** marketing runs a real 3-cell email campaign to a real RFM audience with a 10% holdout and can read incremental revenue the following week.

### Phase 2 — Engagement idea engine + offers (6–8 weeks)
- Signal-pack endpoint from DS; LLM idea generation with schema-constrained output; scorer; review UI.
- Idea library + measured-lift feedback loop.
- Offer ladder, coupon generation to Magento, discount simulator with margin guardrails, promo P&L.
- Web push via FCM + SMS adapter to vendor.
- Campaign P&L dashboard, channel cost table.
- **Done when:** ≥10 approved ideas executed, each with a measured lift, and the library influences the next batch's ranking.

### Phase 3 — Journeys + recommendations (6–8 weeks)
- Journey runner (triggers, waits, branches, goals, exits, caps, kill switch).
- NBA service: next best product + next best action, reason strings, "send nothing" path.
- Storefront integration (module or PWA drop-in), recommendation A/B with revenue-per-session.
- Lifecycle automations: welcome, cart abandonment, post-purchase, winback, replenishment.
- **Done when:** 4 core lifecycle journeys running automatically ≥70% of sends, with caps enforced and retired journeys archived.

### Phase 4 — Scale & optimisation (ongoing)
- Incrementality programme: quarterly test calendar, elasticity re-fits, offer-depth ladder refinement.
- Arabic-first content ops + LLM copy assistance with human editing.
- Ads activation (Meta/Google customer-list export) with consent gating.
- Optional: CLV-based budget allocation model, next-best-channel, send-time optimisation per customer.

**Total to end of Phase 3: roughly 30–40 person-weeks of engineering** (plus DS modelling parallel). Realistic elapsed time with a small team: **6–8 months**, with value delivered from month 3.

---

## 9. KPI tree

```
Incremental contribution profit
├── Revenue retained / reactivated         (retention activities)
├── Incremental orders
│   ├── Audience reachability %            ← consent coverage, data quality
│   ├── Conversion rate per channel        ← offer fit, recommendation quality
│   └── AOV                                ← bundles, free-shipping thresholds
├── Cost of engagement
│   ├── Discount cost / incremental rev     (target < 0.35)
│   ├── Channel cost per order
│   └── Contact fatigue: unsub rate, complaint rate, spam rate (< 0.1%)
└── Customer health
    ├── Repeat purchase rate (90d)
    ├── Cohort retention M6 / M12
    ├── % customers in "at risk" bucket
    └── CLV:CAC by acquisition cohort
```

Report these, not opens and clicks. Opens are meaningless post-Apple MPP anyway.

---

## 10. Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Duplicating data plumbing instead of reusing the mart | +8 weeks, permanent number drift | Phase 0 contract freeze; one-way dependency on the mart |
| LLM generates brand-damaging or non-compliant copy | Reputational | Human gate, blocked-claims list, prices injected from Magento at render |
| Sending too much → unsubscribes and domain damage | Compounding loss | Caps + quiet hours + "send nothing" path + deliverability monitoring from day 1 |
| Magento API rate limits during a big send | Failed write-backs | Async bulk, backoff, idempotency, coupon pre-generation overnight |
| Scores arrive late or wrong → audiences built on stale data | Wrong targeting | `valid_until` on every score, freshness banner on every audience, block campaign launch if scores older than X days |
| Only one person understands the hub | Bus factor | Everything in code/git, data contracts written, no admin-clicked configuration |
| Marketing team doesn't adopt it | Total waste | Phase 1 delivers a real campaign, not a demo; marketing owner is the product owner from week 1 |
| Privacy non-compliance (PDPL) | Legal | Consent at send time, preference centre, DSR workflow, audit log, regional endpoints |
| Discount leakage and cannibalisation | Margin erosion | Holdouts on every campaign, simulator, margin floor in code, maker-checker |

---

## 11. Decisions I need from you

1. **Where does the gold mart live** (BigQuery / Snowflake / Postgres replica / the dashboard's own store)? Determines how the hub reads it.
2. **Which email provider** is behind your current setup — can the hub send directly on its API, or is there a relay constraint?
3. **SMS vendor API** docs + per-country sender ID status (Egypt registration).
4. **LLM serving stack** the DS team has (vLLM? Ollama? which model?) and whether it can be called by the hub synchronously (idea generation) vs batch.
5. **Identity**: do you have guest-checkout volume that needs merging, and do you have a mobile app (affects in-app channel design)?
6. **Scale**: monthly active customers, orders/month, catalogue size, store views/locales.
7. Is **Adobe Commerce Customer Segments** licensed on your plan (Commerce, not Open Source) — this is what makes on-site personalisation cheap.

Once these are answered, Phase 0 can start with a frozen contract rather than guesses.

---

*Companion documents: `02-analysis-and-llm-contract.md` (the DS/LLM → hub interface), `03-prototype-notes.md` (walkthrough of the clickable prototype in `/prototype`).*
