# Prototype notes — what to click and why it's built that way

**File:** `prototype/cvm-hub.html` · open in any browser · no server needed
**Data:** 40,000 seeded synthetic customer profiles (`mulberry32` seed `20261104`), an Egyptian beauty + lifestyle + electronics catalogue, 11 RFM segments. Every aggregate, campaign P&L and idea estimate is **derived from that dataset at load**, so numbers reconcile if you check them.

> The demo base is 40,000 profiles — deliberately mid-market, not "500k contacts". It makes the arithmetic honest: a campaign to 2,171 at-risk customers really does produce ~EGP 243k revenue and ~EGP 79k contribution. If you see a tool claiming EGP 5M from 2,000 sends, the number is aspirational.

---

## A 15-minute demo path

| # | Screen | What to show | The point being made |
|---|---|---|---|
| 1 | **Overview** | KPI band → revenue trend → RFM matrix → cohort retention | Opens and clicks are not on this screen on purpose. Retention is. |
| 2 | **Segments** | Click `At Risk` → drawer opens with the **signal pack** | This is exactly the payload the DS team posts. It's the input to everything downstream. |
| 3 | **Customers** | Search a name, open the 360 drawer | One profile, one truth: scores, consent, timeline, next best action, recommended SKU with a reason. |
| 4 | **Engagement Ideas** | Read the score ring, the rationale, the provenance block. Open one idea. | LLM proposes *what + why*; code scores *how much*; a human approves. Nothing auto-sends. |
| 5 | **Offers & Promotions** | Drag **discount depth** from 5% → 8% | At 8% the post-offer margin drops below the 25% floor and the rule is **rejected**. This is the single most valuable screen in the product. |
| 6 | **Campaigns** | Sort by contribution, then look at "revenue vs incremental" | A campaign with positive revenue and negative contribution is a loss dressed as a win. |
| 7 | **Journeys** | Read the node tracks | Boring on purpose. Trigger → wait → branch → action → goal → exit. |
| 8 | **Recommendations** | The negative-affinity row | Never recommend the citrus mini to an Oud buyer. Most engines miss substitutions. |
| 9 | **Channels** | Fatigue policy | Caps are checked at send time against the exposure log — not against the audience. |
| 10 | **Integrations** | The "what is deliberately NOT built" card | Scope discipline is a feature. |

---

## Design decisions worth defending

**1. The idea engine never self-scores.**
The LLM produces candidates with a hypothesis, mechanic, channels, expected range and rationale references. A deterministic scorer computes:

```
score = 0.35·prior_similarity + 0.20·reachability
      + 0.20·margin_impact + 0.15·strategic_fit − risk_penalty
```

`prior_similarity` is a k-NN over your own executed ideas and their **measured** lift. The LLM is good at mechanics and copy, bad at arithmetic and self-assessment. Keep the numbers in code.

**2. "Send nothing" is a first-class outcome.**
The Recommendations screen shows 37% of next-best-action calls return null. If the hub can't clear the expected-value bar, it stays silent. That suppression is *why* the other 63% still get opened.

**3. The simulator blocks on margin, not on budget.**
Break-even uplift is usually tiny (~2–3%), so "will this be profitable?" is the wrong question. The real constraint is the **post-offer margin floor** and discount dependency. The prototype models both: at 34% margin and 0% depth you fail on channel cost; at 5% you pass; at 8% you breach the 25% floor.

**4. Holdouts are not optional.**
Every campaign row carries treated vs. holdout conversion and a difference-in-differences lift. The "revenue vs incremental" chart exists to show the gap between what you can claim and what you actually caused.

**5. Consent is checked at send time, not at audience-build time.**
The customer drawer shows four channel opt-ins plus a suppression flag, and says so explicitly. This is a compliance requirement (Egypt PDPL 151/2020) and a data-quality one — people opt out between build and send.

**6. The hub reads the mart; it does not re-ingest Magento.**
Integrations screen states the read path and the four permitted write paths. Nothing else writes to Magento.

---

## What's deliberately missing (and why)

| Missing | Why it's missing |
|---|---|
| Drag-and-drop journey canvas | v1 needs 5 node types and a queue; a canvas is a 3-week distraction that doesn't send anything |
| Real-time 1:1 personalisation on every page slot | Needs storefront latency budgets the hub shouldn't own in v1 |
| Ad-network activation | Consent gating + attribution complexity; year two |
| Message template editor | Use MJML files in git; marketers edit copy, engineers own layout |
| Probabilistic identity merge | Deterministic keys (email hash → phone hash → session link) cover ~99% of your volume |
| Streaming infrastructure | At your scale a queue + cron is correct. Kafka would be resume-driven development |

---

## How to extend this into the real thing

1. **Freeze the contract.** `docs/02-analysis-and-llm-contract.md` is the file your DS team codes against. Version it in git.
2. **Phase 0 sprint:** Laravel app + `customer`, `customer_consent`, `customer_score` tables + the `POST /v1/scores:bulk` endpoint reading from your gold mart.
3. **Replace the prototype's data functions with API calls,** screen by screen, in the Phase 1 → 3 order from the blueprint. The UI structure survives; only the data source changes.
4. **Ship the discount simulator before the LLM.** It requires no models, saves margin immediately, and earns the team's trust for the harder pieces.
