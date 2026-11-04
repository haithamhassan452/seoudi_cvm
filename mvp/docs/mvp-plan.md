# MVP plan — scope, schedule, and the decisions behind them

**Goal of the MVP:** on Monday morning, a marketer can see who moved lifecycle
last week, which customers got a promotion recommendation and why, and which
journey each customer is in — and every one of those numbers can be traced to a
Magento order.

Not in scope: sending at scale, measurement, personalisation on the storefront.

---

## 1. Scope, by capability

| # | Capability | MVP state | Definition of done |
|---|---|---|---|
| 1 | Identity & PII | `magento_customer_id` business key, UUID internal, PII encrypted in a separate table | PII never appears in clear text in the DB or any list API |
| 2 | Purchase facts | rebuilt weekly from orders + behaviour events | re-running the job changes nothing |
| 3 | Lifecycle & value | new / active / at_risk / lost + VIP flag + RFM + CLV + churn heuristic | thresholds live in `Policy`, are unit-tested at the boundaries |
| 4 | Promotion recommender | rule-based offer ladder with hard margin guardrails | 15% off can never be recommended at 34% margin; VIPs never get a discount |
| 5 | Five journeys | YAML-defined, state-machine executed, consent enforced at send time | cooldown prevents re-entry; exit fires before the next step |
| 6 | Reporting | `sync_run.stats` + `/v1/segments` + `/v1/sync-runs` | the Monday report is one JSON object |
| 7 | Channel sending | **not built** — every send is a `queued` intent in `message_log` | by design: prove the audience first |

---

## 2. Two-week build plan

Assumes one backend developer, part-time review from marketing. Everything below
is already scaffolded in this repo; the plan is what remains to go live.

### Week 1 — data you trust

| Day | Work | Output |
|---|---|---|
| 1 | Magento integration with read-only ACLs, token stored in Actions secrets. Run `weekly_sync --mock` to familiarise. | working integration |
| 2 | Point the sync at production Magento, `--dry-run`, compare 20 customers by hand against the admin | ingestion is correct, or it isn't |
| 3 | Confirm lifecycle boundaries with marketing. **This is a business conversation, not a code one** — "is 61 days right for our category?" | `Policy` tuned, tests updated |
| 4 | Offer ladder session: agree real margin costs and discount caps per category with finance | `OFFER_LADDER` in `weekly_sync.py` reflects reality |
| 5 | Deploy API to Fly/Render, connect Neon, run the workflow manually | `/v1/segments` returns real numbers |

### Week 2 — journeys you trust

| Day | Work | Output |
|---|---|---|
| 6 | Review journey timelines with marketing. Timing is the whole product: a day-3 email and a day-7 email are different companies. | five YAMLs signed off |
| 7 | Wire one channel adapter (email via Resend) behind the `message_log` intent | a real email to an internal address |
| 8 | Send the first journey to a **50-customer test cohort**, read every message yourself | copy and cadence approved |
| 9 | Turn on the cron, add the Monday report ritual, set alerting on job failure | running unattended |
| 10 | Baseline: record current retention, repeat rate, unsub rate. Any future claim is measured against these. | the baseline you'll be judged on |

**The MVP is done when** one journey has run unattended for two weeks on a real
cohort, and the Monday report is trusted enough that nobody asks for a spreadsheet.

---

## 3. Journey catalogue — decisions and rationale

### VIP (priority 10)
- **Why highest priority:** a VIP who is also at risk matches both this and the
  rescue journey. They get VIP treatment. Losing a top-5% customer costs more
  than every at-risk customer in the base combined.
- **No discounts, by construction.** The `offer_fit` function returns 0.02 for any
  discount mechanic when `is_vip`. This is the single most defended rule in the code.
- **Timing:** day 0 recognition, day 3 early access, day 10 personal note with the
  one offer they may receive, day 24 thanks.

### At-risk rescue (priority 20)
- **Entry:** 2+ orders *and* silence past 61 days. Both conditions matter — one
  order and silence is a different problem (see below).
- **The offer depth is deliberately shallow.** At-risk customers respond to timing
  and relevance far more than to discount depth. Deep discounts here train them
  to lapse again next quarter, and you pay twice.
- **Exit fires on `days_since_last_order <= 7`** — the moment they buy, they leave.

### Active nurture (priority 30)
- **The quietest journey.** These customers are already buying; the risk is
  annoying them. One in-app nudge, then a replenishment reminder timed to their
  own cadence.
- **Cooldown 60 days** — shorter than the others because this is a nudge, not a
  campaign.

### New onboarding (priority 40)
- **The highest-leverage automation in the hub.** The first 14 days decide whether
  the customer ever repeats.
- **Free-shipping threshold, not a discount.** It lifts AOV instead of cutting it,
  and it teaches a threshold rather than a price.

### Lost win-back (priority 50)
- **Lowest priority, cheapest to run, easiest to waste money on.**
- **Single-purchase customers land here, not in at-risk.** A customer who bought
  once and went quiet never formed a habit — you cannot be at risk of losing one.
  They get a win-back, not a rescue.
- **Run it with a holdout and retire it the quarter it stops paying.**

### What every journey has in common
| Rule | Where it's enforced |
|---|---|
| One journey at a time per customer | `pick_journey` + `one_journey_at_a_time` |
| Re-entry blocked by cooldown | `run_journeys` (checks every historical run) |
| Exit checked before every step | `advance()` |
| Consent checked at send time, not enrolment | `_dispatch()` |
| Max 3 contacts / 7 days | `_dispatch()` + `Policy.max_messages_per_7d` |
| Capped new enrolments per run | `Policy.max_journey_entries_per_run` |
| No journey may lack an exit rule | CI validation |

---

## 4. Weekly sync — schedule and failure handling

**Schedule:** `0 0 * * 1` (Monday 00:00 UTC ≈ 02:00 Africa/Cairo).
**Manual:** Actions → Weekly sync → Run workflow (`workflow_dispatch`).
**Concurrency:** `cancel-in-progress: false` — two runs must never overlap.

| Failure | Detection | Handling |
|---|---|---|
| Magento API down / 429 | step fails | 3 retries with exponential backoff; job fails loudly, cron retries next week |
| Partial ingest | `orders_new` far below trend | acceptable — the 3-day lookback heals gaps next run |
| LLM/DS layer unavailable | n/a in MVP (rule-based) | future: fall back to the last batch + rules, never block the job |
| Job silently stops running | GitHub disables crons on inactive repos after 60 days | keep the repo active; the artifact + step summary give you a heartbeat |
| Database suspended (Neon) | first query slow | harmless; `pool_pre_ping` handles reconnects |
| Bad journey YAML merged | CI validation + runtime `validate()` | the job refuses to run rather than half-send |

**The report is the product.** Every run appends a `sync_runs` row with the full
stats blob, so "what did the system do on 4 November" is answerable a year later.

---

## 5. Free-tier limits and what breaks first

| Limit | Number | What breaks | Mitigation |
|---|---|---|---|
| GitHub Actions minutes | 2,000/mo private | nothing at weekly cadence (~3 min/run) | — |
| Neon storage | 0.5 GB | ~2–3M behaviour events | purge raw events > 90 days; keep aggregates |
| Neon compute hours | 191/mo | continuous polling would exhaust it | weekly batch + on-demand API, no polling |
| Render free | sleeps after 15 min | 30 s cold start on the API | internal tool only, or move to Fly |
| Resend free | 100 emails/day | campaigns, not journeys | fine for a 50-customer pilot |
| Magento API rate limit | ~varies by plan | bulk exports during peak | 3-day incremental lookback, never full re-pull |

**Scale ceiling of this design:** ~50k customers with a weekly batch and no
streaming. Past that, move the facts aggregation into SQL and see the blueprint
(`../cvm-hub/docs/01-architecture-blueprint.md` §7).

---

## 6. Backlog after the MVP, in order

1. **Measurement.** Exposure log → holdout allocation → difference-in-differences
   lift. Nothing else matters until you can tell whether any of this worked.
2. **Email adapter for real** (Resend/Brevo) with bounce + unsubscribe handling.
3. **Preference centre.** Consent must be editable by the customer before volume grows.
4. **Coupon creation in Magento** via `POST /rest/V1/coupons`, using the
   deterministic `promo_code` already generated per recommendation.
5. **DS handover** of `churn_prob`, `clv_12m` and `price_sensitivity` — the
   heuristic versions are placeholders with the same interface.
6. **Push (FCM)** — the cheapest channel per message, and the best for the
    new-customer journey.
7. **Journey branching** — only when a real journey needs it.

---

## 7. Risks specific to this MVP

| Risk | Why it bites | Mitigation |
|---|---|---|
| Going live before the audience is trusted | a bad segment at 400 customers is embarrassing; at 40,000 it's a brand incident | the `queued`-intent design means nothing sends until you wire a channel |
| Marketing tunes `Policy` without tests | thresholds are commercial decisions with code consequences | thresholds are in one dataclass with boundary tests; changes go through PR |
| Losing `PII_KEY` | every name and email becomes unreadable | back it up offline before the first real ingest; rotate with `PII_KEY_VERSION` |
| Treating `magento_customer_id` as a secret-safe id | it's a small sequential integer, trivially enumerable | never expose it in a URL, JWT or email link; use the internal UUID externally |
| Guest orders silently dropped | guests are a large share of first-time buyers | staged by the parser; linked on registration by blind-index email match |
| The Monday report being ignored | the system becomes shelfware | one named owner, reviewed in a standing 15-minute meeting |
