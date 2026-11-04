# Online CVM Tower

A control tower for customer value management at Seoudi: every branch, every
activity, every A/B test tied back to one number — **that branch's CVM target**.

The entire application is a single self-contained `index.html`. No build step, no
dependencies, no external requests, no backend. It deploys to Vercel as a static
site and renders identically offline.

---

## The idea in one screen

The home screen answers three questions, in this order:

1. **Is the network on plan?** Group CVM index against the value-weighted target.
2. **Where is the money at risk?** Branches ranked by *gap-to-target × base value* —
   not by raw index, so a large branch missing by 3 points outranks a small one
   missing by 15.
3. **What is happening that a target cannot see?** Flags. The one that matters is
   **growth masking decay**: first orders up while the index falls. Two branches
   show it this week (S11, S14), and the banner names them with both numbers.

## Screens

| Screen | What it answers |
|---|---|
| **Control Tower** | Network status, branches needing attention, flags, activity calendar, running tests, last sync |
| **Branches** | Every branch against its target, with the four index components and its primary treatment |
| **Branch detail** (click any row) | Exact log decomposition of the index move in points, guardrails, recommended effort mix, 35-cell lifecycle grid, activity history |
| **Movement** | Where customers moved between lifecycle stage × value tier last week, priced at stage basket |
| **Activities** | Every intervention with holdout, gross vs incremental revenue, and the ones that cannot be measured |
| **Targets** | Target vs actual vs forecast, the cheapest lever to close each gap, and a what-if that prices extra contact volume |
| **Segments** | Own-brand penetration, brand switching, recurring-basket conversion, service-failure exposure |
| **Tests** | Test registry, decisions including the rejected ones, and the uplift library that prices `need_score` |
| **Signals** | The Data Science contract: signal objects in, flags out |
| **Segment Admin** | Draft → shadow → live → deprecated, with a shadow compare that can block a promotion |
| **Settings** | Index weights with a live rank-stability test, thresholds, hierarchy, data gates, contact policy |

## The index

```
CVM = 100 × CON^0.40 × FRQ^0.20 × VAL^0.25 × BSE^0.15     (baseline = 100)
```

Geometric, so no strength fully compensates a weakness, and logged, so each
component's contribution to a move is exact and additive. **Acquisition is
deliberately outside the index** — that exclusion is what lets "growth masking
decay" fire.

Weights are not a matter of taste: the settings screen runs the acceptance test
live — perturb each weight, renormalise, recount store-pair rank swaps. Above 10%
the change is noise, not strategy.

## Run it locally

```bash
open index.html            # macOS
xdg-open index.html        # Linux
start index.html           # Windows

# or serve it, which is closer to production
python3 -m http.server 8000   # → http://localhost:8000
```

## Deploy to Vercel

**Dashboard (no CLI):** [vercel.com/new](https://vercel.com/new) → import this
repository → Framework preset **Other** → Build command **empty** → Output
directory **empty** → **Deploy**. Every push to `main` then redeploys
automatically and every PR gets its own preview URL.

**CLI:**

```bash
npm i -g vercel
vercel          # preview
vercel --prod   # production
```

No environment variables, no secrets, no build minutes. `vercel.json` already
sets the security headers (CSP, nosniff, referrer policy) and cache behaviour.

> **Note on embedding.** The CSP sends `frame-ancestors 'self'`, so the deployed
> site cannot be embedded in a third-party portal or Notion page. If you need
> that, change it to the allowed origins rather than deleting the header.

## Repository layout

```
index.html                     the entire application
404.html                       branded not-found page
vercel.json                    headers, clean URLs, cache policy
robots.txt                     noindex — internal prototype
docs/
  control-tower-functional-spec.md   the 13-section functional spec
  architecture-blueprint.md          system architecture, Magento integration
  analysis-and-llm-contract.md       DS layer contract
  prototype-notes.md                 early prototype decisions
mvp/                           the Python service (not deployed by Vercel)
.github/workflows/
  ci.yml                       static-app contract + MVP tests
  weekly-sync.yml              Monday Magento sync
```

## About the data

Every figure in the deployed app is **synthetic and internally consistent** —
24 branches, 12 weeks of history, 46 activities, 9 tests. All of it is generated
in-page from one seeded RNG and the index maths above, so the screens reconcile
with each other: change a weight in Settings and every decomposition elsewhere
stays arithmetically correct.

Four branches carry pinned curves so the demo tells the same story on every
reload:

| Branch | Story |
|---|---|
| **S14** New Cairo — Fifth Settlement | The acceptance case: first orders +37% over four weeks, index −3.7 points |
| **S11** 6th of October — Mall of Arabia | Acquisition machine, decelerating into a lower value tier |
| **S07** Maadi — Zahraa | Dismissal drag — a service problem no campaign can fix |
| **S21** Maadi — Sarayat | Quietly excellent, own-brand carrying the base |

## The MVP (`mvp/`)

A separate FastAPI service: five journeys, Magento-ID identity with encrypted
PII, a weekly recommender, 66 tests, GitHub Actions for CI and the Monday sync.
It talks to Postgres and lives on a small always-on host (Fly.io / Render), not
on Vercel — it is a job runner, not a web front end.

```bash
cd mvp
pip install -r requirements.txt
pytest -q
python -m jobs.weekly_sync --mock --seed-events --now 2026-11-04T02:00:00Z
```

## Design system

Sampled to match the Seoudi Stock Availability Control house style.

| Token | Hex | Use |
|---|---|---|
| Brand green | `#0E6E5C` | mark, active nav, matrix fills |
| Action green | `#2F9E66` | primary buttons, on-target |
| Critical red | `#9B1612` | index bars, T4 treatment |
| Alert red | `#D6453D` | nav badges |
| Page cream | `#F2EEE5` | background |
| Card | `#FFFDF8` | surfaces, 14px radius |
| Track | `#EFEADD` | rails, empty matrix cells |

## Status

Prototype — for review, not for decisions. The functional spec in
`docs/control-tower-functional-spec.md` is the source of truth for behaviour;
this app demonstrates it with synthetic data.
