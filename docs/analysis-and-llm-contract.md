# Analysis & LLM Contract — v1
## The interface between your Data Science layer and the CVM Hub

**Purpose** Freeze what the DS/LLM team emits and what the hub consumes, so both sides can build in parallel.
**Rule** The hub never models. The DS layer never sends. This document is the membrane between them.

---

## 1. Transport overview

| Direction | Endpoint | Method | Frequency | Auth |
|---|---|---|---|---|
| DS → Hub | `/v1/scores:bulk` | POST (NDJSON, chunked) | Daily 02:00 + on-demand | mTLS + bearer |
| DS → Hub | `/v1/segments/{key}/signals` | PUT | Daily | mTLS + bearer |
| DS → Hub | `/v1/ideas` | PUT (replaces open candidates) | Daily or on request | mTLS + bearer |
| DS → Hub | `/v1/recommendations:bulk` | POST (NDJSON) | Daily | mTLS + bearer |
| LLM → Hub | `/v1/ideas:generate` | POST (hub calls LLM) | On marketer request | internal |
| Hub → DS | `/v1/outcomes:export` | GET (hub pushes) | Weekly | mTLS + bearer |
| Hub → Storefront | `/v1/nba/{customer_id}` | GET | Real time | edge token |
| Storefront → Hub | `/v1/events` | POST (batched) | 5s | public + HMAC |

All payloads: `application/json`, UTF-8, timestamps **ISO-8601 UTC** (`2026-11-04T02:00:00Z`), currency in **minor units as integers** (EGP piastres → `149900` = EGP 1,499.00). Never floats for money.

Common envelope:

```json
{
  "contract_version": "1.0",
  "source": "ds-scoring-pipeline",
  "model_version": "rfm-2026.10.1",
  "produced_at": "2026-11-04T02:00:00Z",
  "idempotency_key": "scores:2026-11-04:rfm-2026.10.1",
  "records": []
}
```

`idempotency_key` is mandatory. Re-posting the same key is a no-op and returns `200 {"accepted": 0, "skipped": "duplicate_key"}`.

---

## 2. Score ingestion — `POST /v1/scores:bulk`

One line per customer per score (NDJSON), so a partial failure never blocks the rest.

```json
{
  "customer_key": "magento:12345",
  "score_name": "churn_prob_90d",
  "value_num": 0.7341,
  "value_json": null,
  "scored_at": "2026-11-04T02:00:00Z",
  "valid_until": "2026-11-11T00:00:00Z",
  "model_version": "churn-xgb-2026.10.3",
  "confidence": 0.82
}
```

**Identity on the wire:** `customer_key` is always `"<namespace>:<id>"`. Supported namespaces: `magento` (customer entity id), `email_sha256`, `phone_e164_sha256`, `hub` (hub UUID). The hub resolves to its golden profile and records unresolved keys in `score_ingest_reject`.

**Score catalogue v1** — only these names are accepted in v1 (extend by amending this document, not by inventing names):

| `score_name` | Payload | Range | Required |
|---|---|---|---|
| `rfm` | `value_json: {r,f,m}` | ints 1–5 | ✅ |
| `segment_rfm` | `value_json: {segment}` | one of the 11 canonical keys | ✅ |
| `clv_12m` | `value_num` minor units | ≥ 0 | ✅ |
| `churn_prob_90d` | `value_num` | 0–1 | ✅ |
| `price_sensitivity` | `value_num` | 0–1 | ✅ |
| `discount_dependency` | `value_num` | 0–1 | recommended |
| `next_purchase_date` | `value_json: {date}` | ISO date | recommended |
| `category_affinity` | `value_json: [{category_id, weight}]` | weights sum ≈ 1 | recommended |
| `basket_affinity` | `value_json: [{sku, weight, reason}]` | ≤ 20 items | recommended |
| `propensity_{goal}` | `value_num` | 0–1 | per goal |

**Canonical RFM segment keys** (fixed vocabulary — used in URLs, filters and the idea library):

```
champions · loyal · potential_loyalist · new_customers · promising
need_attention · about_to_sleep · at_risk · cannot_lose_them
hibernating · lost
```

**Never send:** names, emails, phone numbers in clear text, addresses, order line items, or anything from the payment record. The hub already has those. Send keys and numbers.

**Validation rules the hub enforces** (reject the row, keep the batch alive):
- score name in catalogue; value in range; `valid_until` ≥ `scored_at`
- `model_version` present and not older than the currently active version for that score (unless `force: true` in the envelope)
- max 500k records per chunk; max 50 chunks per key per day

---

## 3. Segment signal pack — `PUT /v1/segments/{segment_key}/signals`

This is **the input to the engagement idea engine**. It must be aggregate, PII-free, and complete enough that the LLM never has to guess.

```json
{
  "contract_version": "1.0",
  "segment_key": "at_risk",
  "computed_at": "2026-11-04T02:00:00Z",
  "size": 41230,
  "reachable": { "email": 38400, "push": 19100, "inapp": 26800, "sms": 22100 },
  "value": {
    "avg_clv_12m": 284000,
    "revenue_ltm": 184000000,
    "revenue_share_of_total": 0.183,
    "avg_aov": 118000,
    "margin_rate": 0.34
  },
  "rfm_profile": { "r": 1.8, "f": 3.4, "m": 3.1 },
  "lifecycle": "at_risk",
  "trends": {
    "orders_30d_delta_pct": -0.41,
    "orders_90d_delta_pct": -0.22,
    "aov_90d_delta_pct": 0.03,
    "sessions_30d_delta_pct": -0.35,
    "discount_share_90d": 0.41
  },
  "top_categories": [
    { "category_id": "skincare", "share": 0.38, "aov_index": 1.21 },
    { "category_id": "haircare", "share": 0.24, "aov_index": 0.94 }
  ],
  "declining_categories": [{ "category_id": "fragrance", "delta_pct": -0.55 }],
  "basket_pairs": [{ "a": "serum-30ml", "b": "spf-50", "lift": 2.7, "support": 0.08 }],
  "churn_drivers": [
    { "feature": "last_order_days", "direction": "increase", "shap": 0.31 },
    { "feature": "support_tickets_90d", "direction": "increase", "shap": 0.12 },
    { "feature": "discount_share_90d", "direction": "decrease", "shap": 0.09 }
  ],
  "contact_history": {
    "messages_30d_per_customer": 2.1,
    "last_touch_days_ago": 9,
    "unsub_rate_90d": 0.011,
    "response_rate_by_channel": { "email": 0.021, "push": 0.034, "sms": 0.045, "inapp": 0.008 },
    "fatigue_score": 0.62
  },
  "inventory_context": [
    { "category_id": "skincare", "overstock_score": 0.71, "target_margin_rate": 0.38 }
  ],
  "past_campaigns": [
    { "campaign_id": "CMP-0231", "mechanic": "free_shipping", "channel": "email",
      "offer_depth": 0, "measured_lift": 0.11, "incremental_margin": 1842000,
      "sent_at": "2026-08-12", "audience_overlap": 0.71 }
  ],
  "constraints": {
    "max_discount_depth_pct": 0.15,
    "banned_mechanics": ["cashback"],
    "quiet_hours": { "start": "22:00", "end": "09:00", "tz": "Africa/Cairo" },
    "blackout_dates": ["2026-11-25"]
  }
}
```

If a field is unknown, send `null` — do **not** omit it. The idea engine distinguishes "no data" from "zero".

---

## 4. Idea candidates — `PUT /v1/ideas`

The LLM may generate ideas, but they land in the hub as **candidates with structure**. The hub scores them deterministically and a human approves.

```json
{
  "contract_version": "1.0",
  "generated_by": { "model": "qwen2.5-32b-instruct", "prompt_version": "idea-v3",
                    "temperature": 0.3, "seed": 4411, "latency_ms": 8420 },
  "batch_id": "IDEA-2026-11-04-at_risk",
  "candidates": [
    {
      "candidate_id": "c-001",
      "segment_key": "at_risk",
      "title": "Replenishment nudge with free shipping",
      "hypothesis": "At-risk skincare buyers who are 40% below their 90-day order rate respond to a replenishment reminder with a zero-margin-cost incentive (free shipping over threshold) better than to a discount, protecting margin while reactivating.",
      "mechanic": "free_shipping_threshold",
      "mechanic_params": { "threshold": 75000, "current_threshold": 100000 },
      "channel": ["email", "push"],
      "timing": { "send_window": "Sun–Tue 10:00–13:00", "delay_hours": 0 },
      "content_direction": "Reference their last purchase category; urgency from stock, not fake scarcity.",
      "expected": {
        "uplift_pct": [0.04, 0.09],
        "incremental_revenue": [3200000, 7100000],
        "incremental_margin": [900000, 2100000],
        "basis": "2 comparable campaigns on overlapping audience, n=58k"
      },
      "cost_estimate": { "channel_cost": 380000, "discount_cost": 0, "effort_days": 1.5 },
      "risk_flags": ["threshold_lower_than_baseline_profitable"],
      "guardrails": ["cap 1 message per 14 days", "exclude customers with open cart discount"],
      "success_metric": "incremental orders per 1k reached, 14-day window",
      "min_sample_size": 24000,
      "rationale_refs": ["signals.trends.orders_30d_delta_pct", "signals.past_campaigns[0]",
                         "signals.contact_history.fatigue_score"]
    }
  ]
}
```

**Hard requirements on every candidate** (hub rejects the whole batch with `422` and a field-level error list if violated):

| Field | Requirement |
|---|---|
| `mechanic` | must be in the hub's offer-ladder enum |
| `channel` | non-empty subset of `email, push, inapp, sms, onsite` |
| `expected.uplift_pct` | `[low, high]`, `0 < low ≤ high ≤ 3.0` |
| `expected.basis` | non-empty; must reference a real campaign count or model |
| `min_sample_size` | ≥ 1000 |
| `success_metric` | must be a goal the hub can measure (order / add_to_cart / visit / opt-in) |
| `rationale_refs` | ≥ 1; each must resolve to a real field in the signal pack |
| any numeric claim | must not appear in `content_direction` as a hard price — prices are injected at render time from Magento |

**Deterministic scoring in the hub** (LLM output never self-scores):

```
prior_similarity   = weighted k-NN over idea_library on
                     (segment archetype, mechanic, channel, offer depth bucket)
                     weighted by audience overlap, recency (180-day half-life)
reachability       = reachable[chosen channels] / segment size
margin_impact      = expected.incremental_margin midpoint − cost_estimate total
strategic_fit      = 0..1 from a configurable matrix (e.g. inventory pressure,
                     category targets, campaign calendar gaps)
risk_penalty       = 0.15 per risk flag with margin/cannibalisation/consent impact

score = 0.35·prior_similarity + 0.20·reachability + 0.20·norm(margin_impact)
      + 0.15·strategic_fit − risk_penalty        (0..1, displayed as 0..100)
```

**Human review states:** `draft → in_review → approved → scheduled → running → completed`, or `rejected` with a reason code (`margin`, `brand`, `timing`, `duplicate`, `data_quality`, `other`). Rejection reasons are fed back as negative priors — this is how the engine learns your taste, not just your maths.

---

## 5. Outcomes export — `GET /v1/outcomes:export`

The feedback loop. The hub is the only system that knows *what actually happened*, so it must hand that back to the DS layer.

```json
{
  "contract_version": "1.0",
  "window": { "from": "2026-08-01", "to": "2026-10-31" },
  "campaigns": [
    {
      "campaign_id": "CMP-0231",
      "idea_candidate_id": "c-001",
      "segment_key": "at_risk",
      "mechanic": "free_shipping_threshold",
      "offer_depth_pct": 0,
      "channels": ["email", "push"],
      "variant": "A",
      "audience_size": 41230,
      "treated": 37107,
      "holdout": 4123,
      "exposure_events": 40122,
      "measured": {
        "conversion_treated": 0.0612,
        "conversion_holdout": 0.0478,
        "incremental_orders": 498,
        "incremental_revenue_minor": 5876400,
        "discount_cost_minor": 0,
        "channel_cost_minor": 380000,
        "returns_minor": 184000,
        "incremental_margin_minor": 1475800,
        "confidence": 0.94,
        "method": "difference_in_differences"
      },
      "fatigue_side_effects": { "unsubs": 71, "complaints": 3, "spam_reports": 1 },
      "model_versions_used": { "churn_prob_90d": "churn-xgb-2026.10.3", "rfm": "rfm-2026.10.1" }
    }
  ],
  "idea_library_updates": [
    { "segment_archetype": "at_risk_skincare", "mechanic": "free_shipping_threshold",
      "channel": "email", "n_campaigns": 3, "median_measured_lift": 0.062,
      "ci_90": [0.031, 0.088], "median_incremental_margin_per_1k": 39700 }
  ]
}
```

`confidence < 0.8` means the hub considers the result inconclusive — the DS layer must not use it as a strong prior. Say so explicitly in the model.

---

## 6. Recommendations — `POST /v1/recommendations:bulk`

```json
{
  "customer_key": "magento:12345",
  "context": "onsite",
  "generated_at": "2026-11-04T02:00:00Z",
  "model_version": "hybrid-recsys-2026.10",
  "items": [
    { "sku": "SER-30", "score": 0.74,
      "reason_code": "basket_affinity", "reason_ref": "serum-30ml→spf-50",
      "reason_text_ar": "لأنك اشتريت السيروم",
      "reason_text_en": "Because you bought the serum" }
  ],
  "excluded": [{ "sku": "SPF-50", "reason": "out_of_stock" }]
}
```

Rules: ≤ 20 items; scores sorted descending; `reason_code` ∈ `basket_affinity | category_affinity | popularity_segment | replenishment | price_match`; **the hub re-checks stock and price at render time** and drops anything that fails. `reason_text` is the only pre-generated prose allowed in the pipeline, and it is capped at 60 characters and must be template-derived from `reason_code` — not free-form LLM creative writing.

---

## 7. LLM serving requirements (your DS team's side)

| Requirement | Value | Why |
|---|---|---|
| Endpoint | OpenAI-compatible `/v1/chat/completions`, internal network only | Hub calls it the same way it would call any provider |
| Structured output | `response_format: {type: "json_schema"}` **or** vLLM `guided_json` | Grammar-constrained decoding removes 90% of parsing failures |
| Concurrency | ≥ 4 parallel requests | Idea batch generation should take < 3 min |
| Timeout budget | 120s per batch, hard | Hub retries once then surfaces a clear error |
| Logging | prompt hash, prompt version, model, seed, tokens, latency, refusal flag returned to hub | Auditability of generated content |
| PII | none in prompt; enforce with a regex/PII scrubber before the request leaves the DS layer | Compliance |
| Cost ceiling | per-run token budget, enforced by caller | Prevents runaway batches |
| Fallback | if LLM unavailable, hub shows the last batch + a rules-based idea list | Marketing never blocked by GPU availability |

---

## 8. Error semantics

| Code | Meaning | Caller action |
|---|---|---|
| `200 accepted` | fully processed | — |
| `207` | partial: see `rejected[]` | fix rows, re-post only rejects |
| `409 duplicate_key` | idempotent no-op | none |
| `422 contract_violation` | schema/semantic error, `errors[]` with JSON pointers | fix payload; do not retry |
| `429 rate_limited` | backoff with `Retry-After` | retry |
| `503 upstream_unavailable` | mart or Magento unreachable | retry with exponential backoff, max 5 |

Every response includes `X-Contract-Version`. A version bump is a breaking change: run both versions for 30 days, then retire.

---

## 9. Change management

- This document is versioned in git next to the code; PRs to either side must update it.
- New `score_name` or `mechanic` requires a PR to this file + a migration in the hub enum.
- Every contract change is announced with a 2-week window and a compatibility shim.
- The hub exposes `GET /v1/contract` returning the live enums (segments, mechanics, channels, goal types) so the DS layer validates before posting.

---

## 10. Minimum acceptable v1 (if the DS team needs a starting point)

If you want the shortest path to a working loop, this is the smallest set that still produces real value:

1. `rfm`, `segment_rfm`, `clv_12m`, `churn_prob_90d` — daily bulk scores.
2. Signal packs for your **top 5 segments only**, with `trends`, `top_categories`, `churn_drivers`, `contact_history`, `past_campaigns`.
3. Idea batches for those same 5 segments, 5 candidates each, run weekly.
4. Outcomes export weekly, even if confidence is low at first.

That is enough for the hub to run targeted campaigns with holdouts, and enough for the learning loop to start compounding. Everything else is refinement.
