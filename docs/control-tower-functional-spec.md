# CVM Control Tower — Functional Specification

**Version** 1.0 for review · **Date** 4 Nov 2026 · **Owner** CVM function
**Supersedes** `04-segment-strategy-working-draft.md` (strategy framing dropped — this is the management tool)
**Builds on** the Seoudi *CVM Strategy Framework and Growth Plan* (v1.1) and the confirmed answers of 4 Nov 2026

---

## 0. What this tool is, and what it is not

**It is a control tower.** It decides *which store needs which treatment this week, against which target, using which activity*, records everything that happened, and holds the A/B test registry. It is the place a CVM manager opens on Monday morning and closes on Friday.

**It is not** a campaign sender. Sending stays with the current stack (push platform, SMS vendor, call-centre dialler). The tool issues the activity instruction, absorbs the results, and measures whether it worked.

**It is not** a strategy deck. Your strategy already exists (v1.1); this tool operationalises it.

The organising principle, taken from your brief:

> Every screen resolves to one question: **is this store's CVM on target, and if not, what is the next best action?**
> Acquisition, activities, tests and DS analysis are all subordinate to that question — never a separate dashboard with its own definition of success.

### The one design decision that makes the flag possible

Acquisition is **deliberately excluded** from the CVM Index. It is tracked as a leading input.

That is what makes this sentence possible: *"Store 14 is +18% on new customers and −4.2 points on CVM."* If new customers were inside the index, a flood of low-value first orders would mask value decay — which is precisely the failure you described. The index measures **value retention**; acquisition measures **value creation**. The tool reports both and flags the gap.

---

## 1. Scope

### In scope for v1

| # | Capability | Why it exists |
|---|---|---|
| 1 | Store/area CVM scorecards vs target | The north star, per store, weekly |
| 2 | Lifecycle movement from weekly Magento data | Shows *where* value leaked this week |
| 3 | Four-treatment allocation per store | Turns diagnosis into an instruction |
| 4 | Activities Log | The control tower's ledger — planned, running, measured |
| 5 | Exceptional segments (private label, brand migration) | Commercial asks that don't fit lifecycle |
| 6 | A/B test registry | Absorb results, issue new tests, stop repeating failures |
| 7 | Signals & correlations (DS layer surface) | Explains *why* CVM moved, not just that it did |
| 8 | Targets & planning per store, monthly/weekly/yearly | Ties every activity to a number |

### Out of scope for v1

Journey execution, message rendering, channel integrations, attribution modelling, real-time data, customer-facing personalisation. All of those already exist or belong to the engagement hub (`../cvm-mvp/`).

---

## 2. Object model and hierarchy

```
Group (Seoudi)
 └─ Area (district / zone)            e.g. Nasr City, Maadi, Alexandria
     └─ Store (physical branch)       ← THE UNIT OF TARGETING AND TARGETS
         ├─ Customer base (assigned customers + visiting customers)
         ├─ Weekly metrics → CVM Index
         └─ Activities (planned / running / measured)
              └─ Tests (A/B cells + holdout)
```

### 2.0 How this compares to MoEngage, WebEngage — and the recommendation

**Short answer: their object model is for *sending*, ours is for *running a business*. They are complementary, and this tool should adopt their vocabulary for customers while owning the hierarchy they don't have.**

#### What those platforms actually model

| Concept | MoEngage | WebEngage | What it is for |
|---|---|---|---|
| Tenancy | Account → Business Unit | Account → Project / Workspace | Separating brands or teams, not operations |
| Person | User profile + event stream | User profile + event stream | 1:1 targeting |
| Cohort | Segment (rule-based, predictive, affinity) | Segment (rule-based, uploaded list) | Building an audience |
| Action | Campaign / Flow | Campaign / Journey | Sending |
| Success | Campaign goal, journey goal | Goal | Per-campaign attribution |

Their spine is **user → event → segment → campaign**. The hierarchy that exists is organisational (who logs in, which brand), not operational (which branch, which district, which target).

#### Where that breaks for you

Neither product has, natively:

1. **A store/branch hierarchy with per-branch targets.** Retail users model store as a *user attribute* and then hand-build per-store segments. At 40 stores that is 40 segments maintained by hand, and no store-level scorecard exists at all.
2. **A target object.** Campaign goals are per campaign. There is no concept of "Store 14 must hit CVM 96 this month", so nothing can flag *before* the month ends.
3. **A treatment layer.** You have four standing treatments, not discrete campaigns. A tool built for campaigns will represent T3 frequency-pushing as 200 separate campaigns.
4. **A test registry tied to a commercial target.** Both run A/B tests; neither accumulates results into an uplift library that re-prices the next decision automatically.
5. **A service-quality explanatory layer.** No concept of dismissal rate or fulfilment correlating to value loss.

That is the gap. It is not a gap in sending — MoEngage/WebEngage are better senders than anything you should build. It is a gap in **deciding and accounting**.

#### Recommendation

**Do not replace MoEngage/WebEngage — put this tool above them** (or above the push/SMS/dialler stack you actually run today):

```
        CVM Control Tower          ← decides: which store, which treatment, which target
        (this spec)                   accounts: activities, results, tests, insights
                 │ issues activity instructions / audience definitions
                 ▼
        Execution engines          ← MoEngage / WebEngage / push platform / SMS vendor / dialler
                                      sends, delivers, reports opens and clicks
                 ▲
                 │ results, exposures, conversions
                 └──────────────────────────────
```

Three concrete rules follow from that:

1. **Adopt their vocabulary for the customer axis.** A segment is a segment. An audience is an audience. A journey is a journey. Inventing new names for standard concepts makes every conversation with a vendor or a new hire harder, for no benefit.
2. **Own the operational axis here.** Group → Area → Store, with targets, treatments and activities hanging off the store. Push `store_id`, `area_id`, `lifecycle_stage`, `value_tier`, `pl_share_bucket` down to whichever execution tool you use as **custom user attributes**, so its segment builder can filter by store without duplicating your hierarchy. The control tower remains the source of truth; the sender receives a flattened copy.
3. **Read results back.** The Activities Log import (§6.5) is the return path. Without it the tower is planning blind.

#### One structural recommendation on the hierarchy itself

Implement the hierarchy as a **closure table, not a fixed three levels.** Adjacency (`parent_id`) plus a closure table (`ancestor_id, descendant_id, depth`) means:

- you can insert a **Region** between Group and Area later without a migration or a rewrite of 40 reports;
- a store can be re-parented (moved between areas) with full history, so last month's figures stay true;
- roll-ups (store → area → group) are one query at any depth.

Also **effective-date every node**: `valid_from` / `valid_to` on stores and area assignments. When a branch closes or a new dark store opens, historic scorecards must not silently re-compute. A store that closed in March should still show March.

#### Glossary to avoid teams talking past each other

| Control Tower | MoEngage / WebEngage equivalent | Relationship |
|---|---|---|
| Customer | User / profile | Same object, more attributes here |
| Segment | Segment | Same |
| Audience | Segment (a filter) or List | Ours = segment + store scope + filters |
| Activity | Campaign / Flow | Ours is a management record, theirs is the send |
| Journey | Flow / Journey | Execution only; not in this tool's v1 |
| Treatment | *(no equivalent)* | The control tower's core invention |
| Target | *(no equivalent)* | Per store, per period |
| Test | Experiment | Ours links to the uplift library |

---

### 2.1 Store attribution rules

Every order carries a fulfilling store from Magento — confirmed available.

| Metric | Attribution rule |
|---|---|
| Order-level facts (basket, items, dismissals) | Fulfilling store, as reported by Magento. No inference. |
| Store customer base | **Home store** = the store that fulfilled the most of the customer's orders in the trailing 90 days. Tie-break: most recent order, then highest value. |
| A customer's value | Counted **once**, in their home store. Prevents double counting when a customer shops across branches. |
| Visiting customers | Shown separately as a diagnostic ("this store serves 1,240 customers who live in other stores' catchments") — never added to the base. |
| Reassignment | Home store is recomputed weekly. A move is logged in `fact_customer_store_move` and appears on the Store Detail screen — a store losing 300 customers to a new branch is a CVM event, not a data change. |

### 2.2 Customer key

`magento_customer_id` remains the identifier (per the MVP decision). Name, phone and email stay encrypted and are **not needed by this tool** — the control tower works on segment aggregates. Only the activity owner sees contact lists, and that happens in the sending platform, not here.

---

## 3. Segmentation

### 3.1 Lifecycle stages (from v1.1, adopted unchanged)

| Stage | Definition | Treatment owner |
|---|---|---|
| **S0** Registered, no order | Registered, 0 orders | T1 New demand → T2 Activate |
| **S1** New | 1 order | T2 Activate new |
| **S2** Developing | 2 orders | T2 Activate new |
| **S3** Active | 3+ orders, last order < 14 days | T3 Push frequency |
| **S4** At risk | 3+ orders, last order 14–27 days | T4 Poke at risk |
| **S5** Churned | Last order 28–89 days | T4 Recover churn |
| **S6** Lost | Last order 90+ days | T4 Recover lost |

Stage thresholds are grocery-cadence thresholds (14/28/90). Configurable in Settings, stored with an effective date so historic reports don't move retroactively.

### 3.2 Value tiers (confirmed 4 Nov)

Tier is set by **average basket value (AOV) in EGP** over the customer's last 3 orders (rolling). Last-3 rather than lifetime, so tier reflects current behaviour and can migrate both ways.

| Tier | Rule (AOV, EGP) | Label |
|---|---:|---|
| **VIP** | ≥ 3,000 *(provisional — see §12)* | Voice-first, service-led |
| **A** | 1,500 – 2,999 | Highest basket value |
| **B** | 1,000 – 1,499 | Upper-middle |
| **C** | 500 – 999 | Lower-middle |
| **D** | < 500 | Lowest basket value |

Tier and lifecycle are independent axes. A VIP can be Lost; a D-tier customer can be Active. The grid is 7 × 5 = 35 cells per store, which is the atomic reporting grain.

### 3.3 Exceptional segments (commercial asks)

Separate from lifecycle × tier, because these answer commercial questions, not CRM questions. A customer belongs to zero or more.

| Segment | Definition | Commercial question it answers |
|---|---|---|
| **Private label core** | PL share of items ≥ 50% in last 90 days | Are we building own-brand loyalty? |
| **Private label switcher** | PL share moved +20 points in 90 days | Who is converting, and what did it cost? |
| **PL-resistant** | 10+ orders, PL share < 5% | Who won't convert, and is it worth trying? |
| **Brand migrator** | Top brand changed between two 90-day windows, spend ≥ EGP 500/mo | Are we losing a brand's shoppers to a competitor or to ourselves? |
| **Single-brand loyalist** | ≥ 80% of spend in one brand | Which brands own our customers? |
| **Scheduled-order customer** | ≥ 60% of orders are scheduled | Highest-value habit; protect it |
| **On-demand only** | 100% on-demand orders, 3+ orders | Convert to scheduled = frequency lever |
| **Dismissal-affected** | ≥ 1 dismissal in last 3 orders | Service-driven churn risk — feeds the DS layer |
| **Multi-store shopper** | Orders in ≥ 2 stores in 90 days | Branch-level cannibalisation risk |

Brand migration is measured on **sub-category level** as well as total, because "moved from Brand X to Brand Y" inside the same sub-category is a real switching event, whereas across sub-categories it is usually just a category mix change.

### 3.4 Segment administration — editable in Admin, owned by Data Science

Your requirement: **the DS team controls segment definitions without a code deploy.** That is the right call — segments are analysis, and analysis changes weekly. But self-service segmentation is how reporting quietly breaks, so it needs guardrails, not gatekeeping.

#### The rule: Data Science owns definitions, the system owns the guardrails

| Who | Can do | Cannot do |
|---|---|---|
| **DS analyst** | create, edit, version, retire segment definitions; publish rule-based or model-computed segments; set refresh schedule | change target thresholds, approve activities, or see PII |
| **CVM manager** | build *audiences* (segment + store scope + extra filters) for activities; request segment changes | edit a segment definition |
| **CVM head** | approve segments that drive **targets or scorecards** | — |
| **Admin** | field catalog, permissions, system thresholds | — |

Marketing never needs to touch DS logic, because an **audience** is a segment plus scope, not a new definition. DS never needs to touch commercial settings.

#### Two kinds of segment, both first-class

Most DS teams with their own models will hit the limits of a visual rule builder within a month. So support both:

1. **Rule-defined** — a JSON rule tree built in Admin: `AND/OR/NOT` over the curated field catalog, with operators, value lists and lookback windows. Editable in a UI or pasted as JSON.
2. **Membership-defined** — DS computes membership in their own pipeline (propensity > 0.7, k-means cluster, LLM-derived behaviour cluster) and **pushes a list of `magento_customer_id`s** via API. The tool stores membership by version, with the model name and version attached. This is the one that matters for your local-LLM work.

A rule-defined segment and a membership-defined segment are consumed identically downstream — a segment produces a **membership set** with a version and a timestamp.

#### Lifecycle of a segment definition

```
draft ──► shadow ──► live ──► deprecated
  │         │          │
  │         │          └─ may drive activities, scorecards, targets
  │         └─ evaluated daily, size + movement tracked, cannot drive anything
  └─ editable, invisible to marketing
```

**`shadow` is the important state.** A new or edited segment is evaluated alongside the live one for at least one refresh cycle, and the Admin screen shows:

- size now vs size of the version it would replace
- **customers entering and leaving** (count and %), with a sample
- overlap with every live segment that drives an activity
- which live activities would change audience

Only then can it be promoted to `live`. This single rule prevents the classic failure: DS tweaks a definition, marketing's campaign goes to 3× the audience, and nobody knows why until the SMS bill arrives.

#### Guardrails enforced by the system (not by policy)

| Guardrail | Behaviour |
|---|---|
| **Curated field catalog** | DS can only use catalogued fields, each with type, allowed operators, freshness SLA and a PII flag. PII fields can filter but can never be *output*. No raw SQL against production tables. |
| **Validation** | Unknown field, bad operator, type mismatch or circular reference → cannot save. |
| **Cost estimate** | Each definition shows estimated scan cost and runtime before it can be scheduled. |
| **Impact preview** | Mandatory before promotion from `shadow` → `live`: membership delta, overlap, affected activities. |
| **Required metadata** | Name, plain-language description, owner, purpose, refresh cadence. An undescribed segment cannot be published — the description is what the marketing team reads. |
| **Expiry for ad-hoc segments** | Ad-hoc / experimental segments get a TTL (default 90 days) and auto-retire; DS is prompted to promote or delete. Prevents 400 orphan segments. |
| **Immutable versions** | Every publish creates an immutable version with a diff against the last. Activities record the version they used, so a March campaign can be explained in December. |
| **One-click rollback** | Repoint to the previous live version; activities keep the version they were created with. |
| **No deletion, only retirement** | Historic reporting must remain reproducible. |

#### What stays out of DS control (and why)

The **system segments** — lifecycle stage (S0–S6) and value tier (VIP/A–D) — are threshold-driven and come from Settings, not from segment definitions. Reason: they are the axes of the CVM Index and every target. If they were editable without ceremony, a threshold change would silently rewrite history and move the north star.

DS **can** propose threshold changes (e.g. "S3 should be 10 days, not 14, for our cadence") — the proposal runs in `shadow`, shows the impact on every store's CVM index, and takes effect as a versioned policy with an effective date at the start of a period.

#### What DS sees in Admin

- **Segment library** — every segment, owner, version, size, trend, refresh status, last evaluated, health warnings.
- **Builder** — visual rule tree and JSON side by side, live size estimate, masked 10-profile sample, overlap matrix against live segments.
- **Shadow comparison** — the delta report described above.
- **Membership push endpoint** — `POST /v1/segments/{key}/membership` with a version and model metadata.
- **Freshness board** — which segments are stale (their source facts haven't refreshed), because a stale segment driving a weekly activity is the most expensive silent failure in the system.

#### The data-science contract (unchanged from `02-analysis-and-llm-contract.md`)

Segments and their membership still reference `magento:<id>` only. DS never receives names, phones or emails, and segment previews return **masked** samples. The control tower works on aggregates; only the sending platform holds contactable identities.

---

## 4. The CVM Index — the north star

You chose a composite index. Here is the definition, its arithmetic, and the rule that makes it un-gameable.

### 4.1 Components

For store *s* in period *m* (weekly or monthly — the index is defined for both, reported weekly):

| Code | Component | Formula | Weight |
|---|---|---|---:|
| **CON** | Monthly customer continuation | active(s,m−1) ∩ active(s,m) ÷ active(s,m−1) | **0.40** |
| **VAL** | Value depth | total basket value of active customers ÷ orders of active customers (AOV) | **0.25** |
| **FRQ** | Frequency | orders of active customers ÷ active customers | **0.20** |
| **BSE** | Base health | customers with ≥1 order in trailing 28 days ÷ total assigned customers | **0.15** |

"Active" = ≥1 order in the period. Weights sum to 1.0 and are versioned per year in Settings.

#### Why these weights

**They are a starting prior, not a discovered truth.** The honest position: 0.40/0.25/0.20/0.15 is a defensible opening position derived from reasoning plus your own stated north star. §4.6 defines exactly how to replace it with weights fitted to Seoudi's data within three months. Ship with the prior so the tool works on day one, then let the data argue.

The reasoning has four parts.

**(a) The index must agree with the metric management is already judged on.** In v1.1 you declared *monthly customer continuation* as the north star. If the composite index were not dominated by continuation, you would eventually have a store that is "above target on CVM" and below target on continuation in the same week, and the tool's credibility would be gone. CON therefore carries the largest weight — but not all of it, because a continuation-only index is just the north star metric with extra arithmetic, and it cannot tell you whether the base you kept is still worth keeping.

**(b) The components must not double-count each other.** Continuation, frequency and base health all reflect recency; basket value does not. The weights are set so the three recency-family components total 0.75 and no single one dominates:

| Component | Weight | Why this level |
|---|---:|---|
| **CON** | 0.40 | Direct measure of the north star. Least gameable — an order either happened or it did not. High enough to dominate, low enough that basket and frequency still register. |
| **VAL** | 0.25 | Second largest because the basket is the biggest lever on value per customer in grocery, and margin lives in it. Deliberately **below** CON: a basket gain on a shrinking base is a false win — precisely the pattern you asked this tool to catch. |
| **FRQ** | 0.20 | The strongest forward predictor of retention in grocery (weekly buyers churn far less than monthly buyers at the same basket). Weighted below CON because it is largely the *leading* version of the same signal — a frequency fall shows up in continuation 2–4 weeks later, and over-weighting it would count that twice. |
| **BSE** | 0.15 | Catches slow leakage that CON misses, because it measures the whole assigned base rather than just active customers. Lowest weight because it overlaps CON (most of what drags CON down also drags BSE down) and it moves slowly — a higher weight would make the index lag reality. |

**(c) Four constraints bound the choice, and these numbers sit at the centre of the feasible region.**

| # | Constraint | Reason |
|---|---|---|
| C1 | CON ≥ 0.35 | Alignment with the declared north star (part a) |
| C2 | VAL + FRQ ≥ 0.40 | These are the two levers a store manager can actually move; the index must respond to them |
| C3 | BSE ≤ 0.20 | Double-counting control |
| C4 | Every weight ≥ 0.15 | Below 0.15 a component's 10-point collapse moves the index less than 0.8 points — statistically invisible, and therefore not worth reporting on a scorecard |
| C5 | Σ = 1.00 | Definition |

**(d) Alternatives we considered and rejected.**

| Candidate | Why rejected |
|---|---|
| 0.25 / 0.25 / 0.25 / 0.25 | Ignores the declared north star. A store with great baskets and terrible retention scores identically to the reverse — which is the exact confusion the tool exists to remove. |
| 1.00 / 0 / 0 / 0 | Just continuation reported again. No decomposition, no diagnosis, no reason to build an index. |
| 0.60 / 0.20 / 0.10 / 0.10 | Continuation dominates so heavily that ordinary week-to-week noise (±3 points of CON, normal sampling variation) moves the index 2.4 points and trips flags constantly. Fails the stability test in §4.6. |
| 0.40 / 0.30 / 0.20 / 0.10 | Over-weights VAL. In grocery, basket value drifts with inflation and leaflet promotions, so the index would track the price index rather than customer behaviour. |

**One caveat worth deciding now:** VAL is measured in nominal EGP, so it carries inflation. Two options — (i) measure VAL in constant prices using a category price index, or (ii) measure it in **items per order** alongside value. Both are listed as open decisions in §12 because they change the meaning of the index, not just its arithmetic.

### 4.2 Formula

Each component is normalised against the **group baseline** for a fixed reference period (default: FY2026 H1 average), so the index is comparable across stores and stable over time:

```
x̂ = x(store, period) ÷ x_baseline(group)          for x in {CON, VAL, FRQ, BSE}

CVM(s,m) = 100 × CON̂^0.40 × FRQ̂^0.20 × VAL̂^0.25 × BSÊ^0.15
```

**Interpretation:** 100 = group baseline. A store at 92 is running 8% below the group's own normal. A store at 105 is 5% above.

**Why a geometric mean and not a weighted sum:** with a sum, a store can pay for a 20% continuation collapse with a bigger basket and still look flat. That is exactly the masking you want the tool to prevent. With a product of powers, no strength fully compensates a weakness — the weakest component dominates, which is the correct behaviour for a value-retention metric.

### 4.3 Change decomposition (the screen everyone will use)

The index change is additive in logs, which makes attribution exact and defensible:

```
Δ ln CVM = 0.40·Δln CON̂ + 0.20·Δln FRQ̂ + 0.25·Δln VAL̂ + 0.15·Δln BSE
```

The UI shows this as points of CVM and percentages:

> **Store 14 · CVM 91.4 (−4.2 vs last week)**
> −3.1 continuation · −0.9 frequency · −0.4 base health · +0.2 basket value
> *Read: this is a retention problem, not a basket problem. Do not run a basket-value campaign.*

This one component is the difference between a dashboard and a control tower.

### 4.4 Status and flag rules

| Status | Rule |
|---|---|
| 🟢 **On target** | CVM ≥ store target |
| 🟡 **Watch** | target −3 ≤ CVM < target |
| 🟠 **Off target** | target −8 ≤ CVM < target −3, or below target 2 consecutive weeks |
| 🔴 **Critical** | CVM < target − 8, or 3 consecutive weeks below target |

**Special flags** — computed independently of the above and shown as chips on the store row:

| Flag | Trigger | Why it matters |
|---|---|---|
| **Growth masking decay** | new first orders ≥ +10% MoM **and** CVM −3 points or worse | Your headline looks good and your business is getting worse |
| **Continuation cliff** | CON drops ≥5 points WoW | Fastest-moving leading indicator of value loss |
| **Frequency stall** | FRQ drops ≥10% while CON holds | Customers still buy, but less — basket/routine problem, not churn |
| **Dismissal drag** | dismissal rate ≥1.5× group **and** CON below group baseline | Service quality is leaking customers — needs ops, not marketing |
| **PL erosion** | PL share down ≥3 points MoM in a store with a PL target | Margin problem hiding inside stable revenue |
| **Brand leakage** | brand migration out of top-3 brands ≥5% of that brand's base | Competitive loss the CRM team can actually act on |

### 4.5 Guardrails (never traded against the index)

Opt-out rate · NPS after first order · fulfilment rate · dismissal rate · complaint rate · incentive cost per incremental order · contact frequency per customer.

A store cannot "improve" CVM by increasing contact and accepting a higher opt-out rate. The guardrail panel sits next to the index on every scorecard, and a guardrail breach downgrades the store status by one level.

### 4.6 Weight sensitivity, the stability test, and how to replace these weights with your own

#### Sensitivity: what a 5-point fall actually does

Because the index is a product of powers, the effect of a component moving is proportional to *logarithm*, not linear. A 5-point fall in one component moves the index by:

| Component falling from 100 → 95 | at its weight | index effect |
|---|---:|---:|
| CON (0.40) | 0.95^0.40 | **−2.03 points** |
| VAL (0.25) | 0.95^0.25 | −1.27 points |
| FRQ (0.20) | 0.95^0.20 | −1.02 points |
| BSE (0.15) | 0.95^0.15 | −0.77 points |

For comparison, at CON weight 0.30 the same fall costs −1.53 points; at 0.50 it costs −2.53. That range is the entire argument for choosing the weight deliberately rather than by taste.

#### The stability test — the real acceptance criterion

The index exists to decide **where effort goes**, so the test is not "is this the correct formula" but **"would a different reasonable formula send effort to different stores?"**

Procedure, run once before go-live and again at every weight review:

1. Compute every store's index for the last 12 periods under the chosen weights.
2. Recompute under each of four perturbations: CON ±0.05, VAL ±0.05, FRQ ±0.05, BSE ±0.05 (re-normalise each time).
3. Compare store **rankings**, not values.
4. **Accept** if fewer than 10% of store pairs swap order across all perturbations. **Reject and re-weight** if more.

If rankings swing when weights wobble, the weights are carrying too much explanatory load and the index is fragile — effort allocation would lurch on formula changes rather than on business reality. This is why 0.60/0.20/0.10/0.10 fails and 0.40/0.25/0.20/0.15 passes: the latter is flat around its own optimum.

#### Replacing the prior with Seoudi's own weights (weeks 4–12 of P0)

The index is meant to be a *leading summary of durable customer value*. So fit it to predict future value, not to describe the present one:

```
Target variable:   R(s,m) = V(s, m+3) ÷ V(s, m)
                   where V(s,m) = total basket value of store s's assigned base in period m

Model:             ln R(s,m) = β_CON·ln CON̂ + β_FRQ·ln FRQ̂ + β_VAL·ln VAL̂ + β_BSE·ln BSÊ + ε

Weights:           w_i = β_i ÷ Σβ,  subject to the constraints C1–C5 in §4.1
```

Using a **forward** ratio as the target removes the circularity of fitting an index that already contains value: you are asking "which combination of today's four signals best predicts the value this base still holds in three months".

Practical rules:

- **Fit on H1 2026, validate on H2 2026.** Never fit and validate on the same window.
- **Re-fit quarterly, adopt annually.** Weights are frozen for a fiscal year; a re-fit that suggests a change is published as a proposal with 90 days' notice.
- **Re-base history when weights change.** Recompute all historic index values under the new weights and publish both versions for one quarter, or store trends will jump at the changeover date and every store manager will (rightly) complain.
- **Report the gap.** When the empirical weights differ from the prior by more than 0.05 on any component, the tool should show *both* indices side by side for a quarter before switching. If they disagree about which stores are failing, that disagreement is itself the most interesting finding of the quarter — investigate it before adopting either.
- **Governance:** weights are agreed by the CVM head **and Finance**, because the index allocates budget and ranks store managers. A metric that determines bonuses must not be tunable by the team it scores.

#### What would make us change the formula itself

Watch for these in the first two quarters; each is a genuine signal that the composite is mis-specified, not just mis-weighted:

| Symptom | Likely cause | Response |
|---|---|---|
| Index stable while group revenue falls | Value is moving to stores/segments outside the assigned-base definition (guests, cross-store shoppers) | Re-examine home-store attribution (§2.1) |
| Index rises in a month when NPS and complaints worsen | Guardrails are not biting hard enough | Widen guardrail downgrade rules (§4.5) |
| Two components move in lockstep for 6+ weeks | Redundant component | Reduce to three components; re-fit |
| Every store tracks the group average closely | Weights too flat, or the components are dominated by a common seasonal factor | Seasonally adjust components before indexing |

---

## 5. The four treatments

### 5.1 Definitions and mappings

| # | Treatment | Owns stages | Objective | Primary KPI | Secondary KPI |
|---|---|---|---|---|---|
| **T1** | **New demand** | S0 and the acquisition funnel around each store | Registration → first order | Registration→first-order rate | Cost per first order |
| **T2** | **Activate new** | S1, S2 | First → second → third order | Second order by day 14/28 | Third-order conversion |
| **T3** | **Push active frequency** | S3 | Continuation and orders per active | CON, FRQ | Basket, category breadth |
| **T4** | **Poke at-risk & recover churn/lost** | S4, S5, S6 | Rescue and reactivation | 14-day rescue rate; 28-day reactivation | Churn-base coverage |

T1/T2 split matters: **new demand** is about creating demand in a store's catchment (leaflet, geo, offline-to-online), **activate new** is about turning a first order into a habit. They have different owners and different budgets.

### 5.2 Treatment allocation algorithm (per store, weekly)

The tool does not paste one label on a store. It computes a **need score** for all four treatments and outputs a ranked list plus an effort mix.

```
For treatment t, store s:

  addressable(t,s) = size of the base in that treatment's stages
  gap(t,s)         = benchmark rate − store rate        (benchmark = group median)
  value(t,s)       = avg basket of that base
  uplift(t,s)      = observed uplift from the test registry
                     for the best-performing activity of that type
                     on the closest comparable segment
                     (fallback: group plan assumption, flagged as "assumed")

  expected_value(t,s) = addressable × gap × value × uplift
  cost(t,s)           = channel cost × addressable × contact plans
  need_score(t,s)     = expected_value − cost          (EGP per week)
```

Output on the Store Detail screen:

> **Store 14 — recommended effort mix**
> 🟠 T4 recovery **42%** · EGP 61k/wk · 1,840 customers at risk, rescue rate 6 pts below group
> 🟡 T3 frequency **28%** · EGP 34k/wk · frequency 0.9 pts below group
> 🟡 T2 activation **20%** · EGP 22k/wk · day-14 second order 4 pts below group
> 🟢 T1 new demand **10%** · EGP 9k/wk · acquisition above plan; do not increase spend

**Primary treatment** = highest need score. **Effort mix** = need scores normalised, then constrained by channel capacity (voice minutes, SMS budget) and the contact policy in §7.

Rationale: your existing plan already differentiates by tier and stage. What is missing is a **store-level allocation** that changes weekly as the movement matrix changes. That is what this algorithm produces.

### 5.3 Treatment × tier playbook (from v1.1, encoded as rules)

| | VIP | A | B | C | D |
|---|---|---|---|---|---|
| **T1** | — | — | — | First-order CTA | First-order CTA + free slot |
| **T2** | Service call | Light incentive day 21 | Category offer | Item-type offer | Bundle / essentials |
| **T3** | Concierge, no discount | No-offer reminder first | Leaflet categories | Personalised items | Bulk savings |
| **T4** | Voice → SMS, queue priority 1 | Voice → SMS | Voice if capacity | SMS / push | SMS / push, no deep discounts |

The matrix is editable in Settings and versioned, and every activity records which cell it belongs to — so performance accumulates per cell rather than per campaign name.

---

## 6. Screens

Ten screens. Each is defined by purpose, components, and the rules it enforces.

### 6.1 Control Tower (home)

**Purpose:** in 30 seconds, know what needs attention this week.

| Component | Contents |
|---|---|
| Group header | Group CVM index + trend, target attainment, stores on/off target (x of y), value at risk |
| **Needs attention** | Ranked list of ≤8 stores with the worst gap-to-target, each with its primary treatment and the one-line reason ("CVM 88.2; continuation −7 pts; T4 recovery") |
| **This week's flags** | All special flags from §4.4, grouped, with store and magnitude |
| **Activity calendar** | Gantt strip of the current month: planned / running / measured activities by treatment, with gaps highlighted |
| **Test board** | Running tests, days remaining, early read (blinded until minimum sample) |
| **Sync status** | Last Magento sync, rows ingested, data-quality warnings (stores with no orders, missing store attribution) |

**Rule:** every row is clickable and lands on the store or activity that caused it. No dead-end numbers.

### 6.2 Store scorecards

**Purpose:** compare all stores, sort by where the money is.

**Table columns:** Store · Area · CVM index · Δ vs last week (decomposed on hover) · status · CON · FRQ · VAL · BSE · target attainment · primary treatment · flags · value at risk.

**Features:** filter by area / status / primary treatment; toggle "show only off-target"; column preset for area managers (their stores only); CSV export.

**Rule:** the default sort is **gap-to-target × base value**, not raw CVM — a small store at 80 is less urgent than a large store at 90.

### 6.3 Store detail

The most important screen. Structure, top to bottom:

1. **Header** — store, area, manager, CVM index, target, status, trend sparkline.
2. **Decomposition** — the §4.3 waterfall: which component moved the index, by how much.
3. **Guardrails strip** — opt-out, NPS, fulfilment, dismissal, complaints, contact frequency; breach highlighted.
4. **Lifecycle movement** — this store's 7×5 grid, last week vs prior, with flows (see §6.4).
5. **Tier migration** — A→B→C→D and VIP movements, with value attached.
6. **Treatment recommendation** — the §5.2 output: ranked needs, effort mix, and a one-click "create activities from this plan".
7. **Activity history** — every activity run in this store, with measured result and whether it beat the assumption used in the need score.
8. **DS insights** — the correlations that are statistically meaningful for this store, plain language ("dismissal rate above 4% in this store is associated with a 6-point continuation drop 3 weeks later, r=0.61, n=41 weeks").
9. **Store notes** — human context: new competitor, road closure, Ramadan hours. Timestamped, authored. Data rarely explains a drop on its own.

### 6.4 Lifecycle movement

**Purpose:** the weekly answer to *where did value go?*

**Layout:** a 7-stage × 5-tier grid. Each cell shows: customers now, and Δ vs prior period. Arrows show the top three flows in and out. A value panel shows EGP leaking out of each stage.

```
                 VIP      A        B        C        D        Total
S0 no order      —        —        1,204↑   8,940↓   12,301   22,445  → T1
S1 new           92       410↓     1,880    6,204    9,112    17,698  → T2
S2 developing    140      622      2,410    7,882↓   8,004    19,058  → T2
S3 active        310↑     1,204    3,880↑   9,441    7,120    21,955  → T3
S4 at risk       88       402↓     1,510↓   4,220    5,880    12,100  → T4
S5 churned       41       210      980      2,884    4,102    8,217   → T4
S6 lost          120      590      2,140    6,004    9,880    18,734  → T4
```

**Rules:**
- Movement is computed on **stage change week-over-week** and stored in `fact_customer_movement`, never recomputed on read.
- A customer who moves S3→S5 in one week is flagged as a **fast-drop** — these are the highest-value rescue targets and get their own list.
- Value on the move is the customer's trailing-90-day value, so the "EGP leaking" figure is meaningful.
- Every cell links to the customer list behind it (export to the sending platform).

### 6.5 Activities Log

The ledger. Every intervention, planned or executed, in one table.

**Two entry paths:**
1. **Planned in the tool** — created from a treatment recommendation, a test promotion, or manually.
2. **Imported** — a weekly CSV/API pull from the push, SMS and dialler platforms, matched to a planned activity by ID or by (store, treatment, channel, period). Unmatched rows land in an **Unmatched inbox** for a human to classify — never silently dropped.

**Columns:** Activity ID · Period (week/month) · Treatment · Store(s) · Stage × tier cell(s) · Channel · Offer type · Audience size · Holdout % · Contacts · Conversions · Revenue · Incremental (holdout-adjusted) · Cost · CVM component targeted · Owner · Status · Linked test · Notes.

**Status workflow:** `draft → approved → running → completed → measured → archived`. Approval requires a target component and a success metric — an activity that cannot state which CVM component it intends to move cannot be approved.

**Weekly update ritual:** every Monday the sync refreshes the movement; the Activities Log highlights **activities with no result rows** — the control tower's core accountability loop is that an activity cannot sit unmeasured.

### 6.6 Targets & planning

**Purpose:** the north star made explicit, per store, per period.

| Level | What is set | Cadence |
|---|---|---|
| Group | CVM index target, continuation target, reactivation target | Annual, reviewed quarterly |
| Area | Index target, effort budget | Quarterly |
| Store | Index target, component targets, activity budget | Monthly, locked |

**Rules:**
- Targets are stored per period with a lock date; changing a locked target requires a reason code and is written to the audit log ("target moved after results were known" is exactly what a control tower must make visible).
- Each store shows **target vs plan vs actual** — three lines, not two. Plan = the expected effect of the activities already scheduled. Actual = what happened. If plan is below target, the store is flagged *before* the month ends.
- Yearly view: rolling 12-month index with target bands.

### 6.7 Exceptional segments

**Purpose:** the commercial asks that lifecycle segmentation can't answer.

**Panels:**
1. **Private label share** — by store, by tier, by lifecycle stage. Trend line, target line. Split of PL **penetration** (share of customers buying any PL) vs **intensity** (share of items that are PL) — the two need different actions.
2. **Brand migration matrix** — from-brand × to-brand, last 90 days vs prior 90, with customer counts and value. Highlights: brands gaining, brands losing, and where the loss is going (competitor vs internal substitution).
3. **Category / sub-category mix shift** — which categories are growing or declining per store.
4. **Core type mix** — where "core type" is the merchandising classification from Magento; shows whether a store's value mix is moving toward or away from the strategic types.
5. **Order-type behaviour** — scheduled vs on-demand share by store and tier, with conversion opportunity sizing ("1,840 on-demand-only customers at ≥3 orders = 1,840 candidates for scheduled conversion at 12% observed uplift").
6. **Dismissal-affected customers** — the crossover between bad service and value loss.

Every panel exports to a customer list and can seed an activity directly.

### 6.8 Test registry (A/B)

**Purpose:** absorb results, issue new tests, and stop repeating failures.

**Record fields:** test ID · hypothesis (must name the CVM component and the mechanism) · treatment · store(s) · segment cells · cells & allocation (control/holdout + variants) · primary metric · guardrails · sample required · MDE · start/end · result · confidence · decision (`adopt`, `iterate`, `reject`, `inconclusive`) · linked activities · owner.

**Three flows:**

1. **Absorb** — results arrive from the tool (if it ran the split) or by import; the record moves to `measured` and the result feeds the **uplift library** used by the treatment allocator (§5.2).
2. **Recommend** — the tool proposes a test when an activity's measured result differs materially from its assumption, or when a store's need is high and the uplift library has no trustworthy prior for that cell. Proposal card: *"T4 rescue on Store 14, A-tier, voice-first: no prior for this cell; suggested test 12,000 customers, 10% holdout, 3 weeks, MDE 2.5 pts on rescue rate."*
3. **Accept as activity** — a test is promoted into the Activities Log with cells and holdout pre-filled, so execution and measurement stay in one record.

**Rules:**
- No activity above a size threshold can run without a holdout, unless a reason code is recorded.
- Results are **blinded** below minimum sample — the tool will not show partial readouts that tempt premature decisions.
- A cell with 3 rejected hypotheses is flagged: stop testing, the answer is structural.

### 6.9 Signals & correlations (DS surface)

**Purpose:** explain the *why*. This is the DS team's output made operational.

**Data the DS layer supplies** (weekly, per store, with period alignment):

| Signal | Source | Used for |
|---|---|---|
| Fulfilment rate, on-time rate | Ops / Magento | Service → CVM correlation |
| Dismissal rate (items dismissed ÷ items ordered), substitution rate | Magento order lines | The strongest single driver hypothesis you named |
| Complaint rate, complaint categories | Customer service | Root-cause attribution |
| NPS (overall, after first order) | Survey platform | Guardrail + explanatory |
| Delivery slot availability, order cancellations | Ops | Capacity-driven churn |
| Leaflet cycle timing and category coverage | Marketing | Activity confound control |

**Screen contents:**
- **Correlation explorer** — pick a CVM component, pick a signal, see the scatter with lags (0–6 weeks), r, n, and a plain-language verdict. Lag defaults to the best-fitting lag, not zero.
- **Anomaly feed** — automatic surfacing when a signal breaks its own band in a store *before* CVM moves. This is the early-warning system.
- **Driver ranking** — for the selected store and period, a ranked list of candidate drivers with effect size and confidence, produced by the DS layer and displayed as-is. The tool does not compute statistics; it displays them, records the model version, and lets humans act.
- **Insight records** — every analytical finding is stored with a date, an owner, the data window and the model version, so a decision taken in March can be explained in December.

**Hard rule:** the tool shows correlation, never causation, and every insight carries its n and confidence. Insights without a confidence value are not displayed.

### 6.10 Settings

Store hierarchy · tier thresholds (versioned) · stage thresholds (versioned) · CVM weights (versioned, per year) · treatment playbook matrix · contact policy · channel costs · activity approval thresholds · test size thresholds · user roles.

---

## 7. Cross-cutting rules

### 7.1 Contact policy (from v1.1, enforced in the tool)

Priority order when several programs apply to one customer in one week:

```
1. Service messages (outside caps entirely)
2. Abandoned cart
3. Activation ladder (S0, S1)
4. At-risk / churned / lost waves
5. Personalised lifecycle messages
6. Global daily push (lowest; suppressed if a higher-priority message went out that day)
```

**One owner program per customer per week.** The tool allocates the week before activities are approved, and refuses an activity that would breach a customer's weekly allowance. Holdout customers receive no program message but remain eligible for global push, so results stay comparable.

### 7.2 Data quality gates

The Monday sync must pass these before scorecards update; failures are shown, not hidden:

| Gate | Rule | If it fails |
|---|---|---|
| Store attribution completeness | ≥99% of orders have a store | Store-level pages show a data warning banner |
| Item-level completeness | ≥98% of order lines have brand + category | Exceptional-segment panels disabled |
| Dismissal capture | ≥95% of orders have dismissal fields | Dismissal-drag flag suppressed |
| Duplicate orders | 0 duplicate `magento_order_id` | Sync aborts |
| Customer linkage | ≥95% of orders have a customer id | Guest share reported as a metric |

### 7.3 Audit

Every state change on targets, activities, tests and settings writes an append-only record: who, what, before, after, reason code, timestamp. The control tower's credibility depends on being able to reconstruct any decision.

### 7.4 Roles

| Role | Can do |
|---|---|
| **Viewer** | read dashboards |
| **Store/Area manager** | read own stores, create activities for own stores, cannot change targets |
| **CVM manager** | create/approve activities, propose tests, request target changes |
| **CVM head** | approve target changes and above-threshold activities |
| **DS analyst** | publish signals and insight records; cannot change commercial settings |
| **Admin** | settings, hierarchy, weights, users |

---

## 8. The operational rhythm

This is the part that turns a tool into a control tower.

| Cadence | Ritual | Owner | Output |
|---|---|---|---|
| **Weekly, Monday** | Magento sync → movement → scorecards → flags | Ops | Store flag list |
| **Weekly, Monday 60 min** | CVM stand-up: walk the flags, approve this week's activities and tests | CVM manager | Approved activity set |
| **Weekly, Friday** | Fill activity results; match imported vendor data; clear the unmatched inbox | Marketing ops | Clean ledger |
| **Monthly, week 1** | CVM review: index vs target vs plan by area; test readouts; retire or scale | CVM head | Decisions, target adjustments |
| **Monthly, week 2** | DS insight review: drivers, anomalies, next hypotheses | DS team | Insight records + test proposals |
| **Quarterly** | Target reset, weight review, playbook revision, effort budget reallocation | CVM head | Versioned settings |
| **Yearly** | Strategy and index baseline review | Executive | Next-year targets |

**Rule:** the tool must be closable on Friday and defensible on Monday. If a number cannot be traced to a Magento order, a target, or a recorded decision, it doesn't belong on a screen.

---

## 9. Data model (summary)

Names are indicative; the MVP schema (`../cvm-mvp/app/models.py`) already implements the customer, order, score and journey tables.

**Dimensions**
```
dim_area            area_id, name, region
dim_store           store_id (Magento), area_id, name, format, opening_date, active
dim_customer        customer_id (uuid), magento_customer_id, home_store_id, registered_at, tier, lifecycle
dim_product         sku, brand_id, category, sub_category, core_type, is_private_label, pl_type
dim_treatment       T1..T4, owning stages, kpi
dim_channel         voice, sms, push, in_app, leaflet, ...
dim_period          period_id, type (week|month|quarter|year), start, end, label
```

**Facts**
```
fact_order          order_id (magento), customer_id, store_id, placed_at, order_type
                    (scheduled|on_demand), items_count, dismissed_items_count, grand_total_minor,
                    discount_minor, channel
fact_order_item     order_id, sku, brand_id, category, sub_category, core_type,
                    is_private_label, pl_type, qty, value_minor, dismissed_flag
fact_customer_metric_weekly   customer_id, period, store_id, orders, value_minor, aov_minor,
                              tier, lifecycle, pl_share, scheduled_share, dismissals
fact_customer_movement        customer_id, period, from_stage, to_stage, from_tier, to_tier,
                              value_minor, fast_drop_flag
fact_customer_store_move      customer_id, period, from_store, to_store, reason
fact_segment_weekly           store_id, period, stage, tier, customers, value_minor, avg_aov,
                              pl_share, scheduled_share
fact_store_metric_weekly      store_id, period, con, frq, val, bse, cvm_index,
                              con_contrib, frq_contrib, val_contrib, bse_contrib,
                              status, flags[], guardrails{}
fact_service_quality_weekly   store_id, period, fulfilment_rate, on_time_rate, dismissal_rate,
                              substitution_rate, complaints, complaint_rate, nps
```

**Management objects**
```
cvm_target          scope (group|area|store), scope_id, period_type, period_id,
                    target_index, target_con, target_frq, target_val, target_bse,
                    locked_at, locked_by, reason_code
dim_activity        activity_id, name, treatment, period, stores[], segment_cells[],
                    channel, offer_type, audience_size, holdout_pct, owner, status,
                    target_component, success_metric, linked_test_id, planned_cost_minor
fact_activity_result activity_id, period, contacts, conversions, revenue_minor,
                     incremental_minor, cost_minor, opt_outs, measured_at
dim_test            test_id, hypothesis, treatment, stores[], cells{}, primary_metric,
                    mde, sample_required, start, end, status, decision, linked_activity_ids[]
fact_test_result    test_id, cell, n, conversions, metric_value, confidence, result_payload
dim_insight         insight_id, store_id, period, statement, signal, cvm_component, lag_weeks,
                    r, n, confidence, model_version, published_at, author
treatment_recommendation  store_id, period, treatment, addressable, gap, expected_value,
                          cost, need_score, rank, assumed (bool)
sync_run            id, started_at, finished_at, status, rows{}, gates{}, warnings[]
audit_log           id, at, actor, entity, entity_id, action, before, after, reason_code
```

---

## 10. What the weekly Magento feed must contain

Minimum fields per order, per your confirmation:

```
order:  increment_id, entity_id, customer_id, store_id (fulfilling),
        created_at, status, order_type (scheduled|on_demand),
        grand_total, discount_amount, items_count, dismissed_items_count,
        delivery_slot, cancellation_flag
lines:  sku, qty, row_total, dismissed_qty,
        brand, category, sub_category, core_type,
        is_private_label, private_label_type
```

Everything in §6 depends on `store_id` and `dismissed_items_count` being present and correct. Those two fields are the highest-value data-quality items in the whole project — more valuable than any model.

---

## 11. Build sequence after this spec is approved

| Phase | Delivers | Depends on |
|---|---|---|
| **P0** | Store hierarchy import, weekly Magento ingest, lifecycle + tier assignment, factual movement, store scorecards (index, no treatments) | this spec |
| **P1** | Activities Log (manual + import), targets per store, status workflow, Monday stand-up view | P0 |
| **P2** | Treatment allocation algorithm, store detail with decomposition, flags and guardrails | P1 |
| **P3** | Exceptional segments (PL, brand migration, order-type, dismissal) | P0 + item-level data |
| **P4** | Test registry, uplift library, recommendation cards | P1–P2 |
| **P5** | DS signals surface, correlation explorer, anomaly feed | DS weekly output |

P0 and P1 are what make it a control tower. P3 is what makes it commercially interesting. P5 is what makes it smart. Build in that order — do not start with P5.

---

## 12. Open decisions I need from you

| # | Decision | Default if you don't specify |
|---|---|---|
| 1 | **VIP threshold** — your doc says AOV 5k+, expanding to 3k+. Confirm. | VIP = AOV ≥ 3,000, sitting above A |
| 2 | **CVM weights** — CON .40 / VAL .25 / FRQ .20 / BSE .15. Accept? | As above, versioned for 2026 |
| 3 | **Baseline period** for the index (100 = ?) | FY2026 H1 group average |
| 4 | **"Active" definition** — ≥1 order in the period? Any minimum basket? | ≥1 completed order, cancellations excluded |
| 5 | **Store targets** — set top-down from the group plan, or bottom-up from each store's potential? | Top-down, area-adjusted |
| 6 | **Effort budget unit** — EGP, voice minutes, contacts, or all three? | All three, converted to EGP for ranking |
| 7 | **Area hierarchy** — is it the delivery zone, the district, or the existing ops region? | Ops region, mapped to delivery zones |
| 8 | **Contact policy owner** — does the tool allocate the weekly contact, or does the existing push platform? | The tool allocates; the platform executes |
| 9 | **VAL in nominal EGP or constant prices / items?** Inflation in Egypt moves basket value independently of customer behaviour | Nominal EGP for v1, with an items-per-order variant displayed beside it; decide before the first annual weight review |
| 10 | **Do weights get fitted empirically in P0?** (§4.6) | Yes — run the fit on H1 2026 during weeks 4–12, present prior vs fitted before adopting |
| 11 | **Segment publishing rights** — can DS publish directly to `live`, or must CVM head approve? (§3.4) | DS publishes anything that only feeds reporting or an audience; CVM head approves anything that feeds a **target or scorecard** |
| 12 | **Does the control tower allocate audiences, or just instruct the sending platform?** | Allocate here, push `magento_customer_id` lists with version IDs to the sender |
| 13 | **Hierarchy depth** — is a Region level needed between Group and Area in the next 24 months? | Assume yes; build the closure table so it costs nothing to add |

Answer those eight (or override the defaults) and I'll build the control tower prototype screen by screen against this spec.
