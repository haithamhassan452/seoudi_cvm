# CVM Hub — MVP

Customer value management & engagement hub for an Adobe Commerce (Magento) store.
Five lifecycle journeys, a weekly promotion recommender, and a security model where
**the Magento customer id is the only identifier and personal data is encrypted at rest**.

Runs on GitHub + free-tier services. No paid infrastructure, no SaaS marketing suite.

```
Magento Cloud ──REST──► weekly_sync ──► Postgres ──► FastAPI ──► you
                        (GitHub Action)   (Neon)      (Fly/Render)
                            │
                            ├─ purchase facts    → who buys what, how often, how recently
                            ├─ lifecycle + RFM   → new / active / at risk / lost / VIP
                            ├─ recommender       → one offer per customer, or nothing
                            └─ journey runner    → enrol, wait, send, exit, cap
```

---

## Quickstart (60 seconds, no accounts needed)

```bash
pip install -r requirements.txt

# run the whole pipeline against a deterministic mock Magento
DATABASE_URL=sqlite:///./cvm.db python -m jobs.weekly_sync --mock --seed-events

# poke at the result
DATABASE_URL=sqlite:///./cvm.db uvicorn app.main:app --reload
#  → http://localhost:8000/docs
```

The first run ingests ~400 mock customers and ~1,800 orders, classifies every
lifecycle, issues ~350 recommendations, enrols ~360 customers into journeys and
writes ~220 message intents. Run it a second time and **nothing changes** — that
is the idempotency guarantee the weekly schedule depends on.

```bash
pytest -q        # 66 tests: crypto, lifecycle, recommender, journeys, sync
```

---

## The identity change you asked for

| | Before | MVP |
|---|---|---|
| Unique identifier | phone number | **`magento_customer_id`** |
| Internal primary key | — | surrogate UUID (never leaves the database) |
| Name / email / phone | columns on the customer row | **encrypted in a separate `customer_pii` table** |
| Lookup by email | plain index | blind index (keyed HMAC-SHA256) |
| What the DS layer sees | hashed phone/email | `magento:<id>` only |

**Three things this gets right, and one nuance worth knowing:**

1. **PII is off the golden record.** `customers` has zero personal columns. Every
   analytics query, the recommender and the journey engine run without the
   ability to decrypt anything. That is least privilege enforced by schema, not
   by promise.
2. **Phone is never a key.** It is an encrypted attribute with a blind index at
   most, so a phone-number change (or a shared family number) can't merge two people.
3. **Encryption is versioned.** `key_version` is stored per row, so keys rotate
   without a flag day.
4. **Nuance — use *both*.** `magento_customer_id` is the right *business* key, but
   it is a small sequential integer, so it must never appear in a URL, a JWT or an
   email link that a customer can see. That is why there is also an internal UUID:
   `magento_customer_id` is what you join and reconcile on; `id` is what you
   reference in your own systems. Both are in the schema already.

Guest checkouts have no `magento_customer_id`, so they are staged by the Magento
parser and only become journey-eligible when the shopper registers. Do not
invent an identifier for a guest — you will end up with duplicate humans.

---

## The five journeys

All five live in `journeys/*.yaml` — versioned, reviewed like code, validated in CI.

| Journey | Priority | Enters when | Goal | Cooldown | Channels |
|---|---|---|---|---|---|
| **VIP** | 10 | `is_vip` (top 5% by 12-mo CLV) | retention | 120 d | email, in-app, push |
| **At-risk rescue** | 20 | 2+ orders and silent ≥ 61 days | reactivation | 90 d | email, push, SMS |
| **Active nurture** | 30 | 2+ orders, ordered within 60 days | repeat order | 60 d | in-app, email, push |
| **New onboarding** | 40 | registered ≤ 45 d, 0–1 orders | first repeat | 90 d | email, push, in-app |
| **Lost win-back** | 50 | silent ≥ 180 d, or never activated | reactivation | 180 d | email, SMS, push |

Priority matters because **a VIP can also be at risk**. Overlap is normal; the
engine resolves it to exactly one journey, highest priority wins. A customer is
never in two journeys at once — that is how you get three emails in one week.

Every journey has: entry rules, exit rules, a cooldown, a goal, and step-level
timing. Exit rules are evaluated before every step, so a customer who buys
yesterday leaves the rescue journey rather than receiving step 2.

**Separate working strategy note:** the Seoudi proposal introduces A–D value tiers
(assumed to be AOV bands), which are an overlay on lifecycle—not new journeys and
not the same as the CLV-based VIP flag. They are intentionally **not wired into
MVP code yet**: the source draft's Segment D mean basket (EGP 706) conflicts with
D < EGP 500. See [`../cvm-hub/docs/04-segment-strategy-working-draft.md`](../cvm-hub/docs/04-segment-strategy-working-draft.md)
for the tier table and reconciliation questions.

### Why these use different offers

The VIP journey carries **no discount mechanic at all** — rewarding your best
customers with a percentage off teaches the rest of the base to wait for a sale.
At-risk customers get timing, not depth. New customers get free-shipping
thresholds, which raise AOV instead of cutting it. The recommender enforces this
in code (`app/engine/recommender.py`), not in a playbook.

---

## The weekly sync

Runs **Monday 00:00 UTC (~02:00 Cairo)** via `.github/workflows/weekly-sync.yml`.
Six phases, one transaction, one report:

| Phase | What it does | Why it's safe to re-run |
|---|---|---|
| **Ingest** | pulls customers + orders updated since the watermark (with a 3-day overlap) | orders keyed on `magento_order_id`; upserts |
| **Facts** | rebuilds `customer_facts`: orders, revenue, AOV, discount share, cadence, abandoned carts, top category, messages/7d | full recompute, no incremental drift |
| **Score** | lifecycle, RFM, CLV, churn, VIP flag | deterministic given the clock |
| **Recommend** | one offer per reachable customer, or an explicit "send nothing" | unchanged recommendations are **carried forward**, not re-minted — no new coupon, no duplicate email |
| **Journey** | advances due runs, enrols newly eligible customers within a per-run cap | `next_run_at` gate + cooldown table |
| **Report** | writes `sync_run.stats` and prints JSON | it's the artefact you audit |

**Output you actually read on Monday:**

```json
{
  "recommendations": { "issued": 10, "kept": 346, "revoked": 10,
                       "blocked": { "below_threshold": 43, "no_consent": 1 },
                       "by_offer": { "SHIP75": 170, "BUNDLE3": 134, "PTS2X": 30, "GIFT": 22 } },
  "journeys": { "entered": 2, "exited": 0, "deferred": 6,
                "actions_queued": 297, "actions_suppressed": 61 },
  "behaviour_signals": { "high_churn_risk": 80, "repeat_cart_abandoners": 6, "vip_at_risk": 1 },
  "lifecycle_counts": { "lost": 129, "at_risk": 77, "active": 166, "new": 28 }
}
```

`kept: 346 / issued: 10` on the second week is the whole point: the recommender
is stable, so the customer sees continuity instead of a new coupon every Monday.

`actions_suppressed: 61` is consent and contact caps working at send time.

---

## Free stack

| Layer | Service | Free tier | Watch out for |
|---|---|---|---|
| Code & CI/CD | **GitHub** | unlimited public / 2,000 Actions min/mo private | a weekly job uses ~3 min/month |
| Scheduler | **GitHub Actions cron** | included | UTC only, can be delayed minutes; `workflow_dispatch` for manual runs |
| Database | **Neon** or **Supabase** Postgres | Neon: 0.5 GB + autosuspend; Supabase: 500 MB | Neon suspends idle branches — first query after a week is slow, harmless |
| API | **Fly.io** / **Render** / **Koyeb** | Fly: small always-on allowance; Render free sleeps after 15 min | cold starts ~30 s; keep the API internal or accept it |
| Email | **Resend** (100/day) or **Brevo** (300/day) | enough for a pilot | verify your sending domain (SPF/DKIM/DMARC) before the first send |
| Push | **Firebase Cloud Messaging** | free | needs a service worker on the storefront |
| SMS | **your existing vendor** | — | Adobe Commerce has **no native SMS gateway**; keep the vendor behind an adapter |
| Secrets | **GitHub Actions secrets** | free | store `PII_KEY` here, never in the repo, never in the DB |

Total infrastructure cost for the pilot: **EGP 0**, plus SMS at cost.

---

## GitHub setup

```bash
git init && git add . && git commit -m "CVM Hub MVP"
git remote add origin git@github.com:<org>/cvm-hub.git
git push -u origin main
```

Then:

1. **Create the database** — Neon → copy the connection string.
2. **Generate the PII key** once, and store it in your password manager too:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   python -c "import secrets; print(secrets.token_urlsafe(48))"   # blind index pepper
   ```
3. **Add repository secrets** (Settings → Secrets → Actions):
   `DATABASE_URL`, `PII_KEY`, `PII_KEY_VERSION`, `BLIND_INDEX_KEY`,
   `MAGENTO_BASE_URL`, `MAGENTO_TOKEN`, `API_TOKEN`, `RESEND_API_KEY`.
4. **Create the Magento integration** with read-only ACLs: Customers, Orders,
   Invoices, Products. Never reuse an admin account.
5. **Run the workflow manually** (Actions → Weekly sync → Run workflow), check the
   report, then let the cron take over.
6. **Protect `main`** — require the CI check to pass. Journey YAML is business
   logic; treat changes to it like code changes.

> ⚠️ If you lose `PII_KEY`, every encrypted name and email is unrecoverable.
> Back it up before the first real ingest, and rotate with `PII_KEY_VERSION`
> rather than replacing the key.

---

## API

Bearer token auth (`Authorization: Bearer $API_TOKEN`).

| Endpoint | Purpose |
|---|---|
| `GET /health` | liveness + the active policy thresholds |
| `GET /v1/segments` | lifecycle counts, VIP count, value at risk |
| `GET /v1/customers?lifecycle=&vip=` | masked customer list, ordered by CLV |
| `GET /v1/customers/{magento_customer_id}` | 360: scores, facts, consent, live recommendation, journeys, orders |
| `GET /v1/offers` | the offer ladder with margin costs |
| `GET /v1/recommendations?status=live` | this week's recommendations |
| `GET /v1/journeys` | journey definitions + active/completed run counts |
| `GET /v1/journeys/{key}/runs` | who is in a journey and when they move next |
| `POST /v1/events` | storefront tracker target (batched) |
| `GET /v1/sync-runs` | run history with the full stats payload |
| `GET /v1/messages` | exposure log: what was queued vs suppressed, and why |

---

## Layout

```
app/
  config.py            Policy (all thresholds in one frozen dataclass) + Settings
  crypto.py            Fernet PII vault + blind indexes + masking
  models.py            SQLAlchemy schema (UUID pk, magento id unique, PII separated)
  main.py              FastAPI surface
  engine/
    lifecycle.py       lifecycle, RFM, churn heuristic, CLV, VIP — pure functions
    recommender.py     offer ladder scoring + margin guardrails — pure functions
    journey.py         YAML loader + step state machine — pure functions
  integrations/
    magento.py         REST client + deterministic mock
jobs/weekly_sync.py    the six-phase job
journeys/*.yaml        the five journeys
sql/001_init.sql       Postgres DDL for review
tests/                 66 tests
```

**Design rule throughout:** the engines are pure functions over a `Profile`
dataclass. No database, no clock, no HTTP. That is why the tests are fast and the
behaviour is predictable — and why your DS team can replace the scoring functions
without touching the plumbing.

---

## Deliberately not built yet

| Missing | Why |
|---|---|
| Real channel senders | email/push/SMS adapters are thin; wire them once the audience logic is trusted |
| Measurement / holdouts | the next thing to build — without it you can't tell lift from coincidence |
| Visual journey builder | YAML + git beats a canvas you can't code-review |
| Branching in journeys | linear + exit rules covers all five MVP journeys |
| Auto-send of anything | every send is currently an intent in `message_log`, status `queued` |
| Multi-user auth / RBAC | single token is fine behind a VPN; add roles before teammates log in |

## Go-live checklist before the first real email

- [ ] `PII_KEY` generated, backed up, and **not** in the repo
- [ ] `ENV=production` (forces `PII_KEY` to be set — the app refuses to start otherwise)
- [ ] Magento integration is read-only and ACL-scoped
- [ ] Sending domain verified; SPF/DKIM/DMARC passing
- [ ] A 5–10% holdout allocated on the first campaign
- [ ] Unsubscribe link + preference centre live before the first non-transactional send
- [ ] `--dry-run` executed against production data and the report reviewed
- [ ] One person owns the Monday report

See `docs/mvp-plan.md` for the two-week build plan and the free-tier limits.
The product design, full architecture and Magento integration details are in
`../cvm-hub/docs/`.
