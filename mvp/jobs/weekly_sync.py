"""Weekly sync — the heartbeat of the hub.

One idempotent run does six things, in this order:

  1. INGEST     pull customers + orders updated since the watermark
  2. FACT       rebuild per-customer purchase facts from the order table
  3. SCORE      lifecycle, RFM, CLV, churn, VIP
  4. RECOMMEND  refresh the promotion recommendation for every reachable customer
  5. JOURNEY    enrol newly eligible customers, advance everyone who is due
  6. REPORT     write a diff report so a human can see what changed

Run it with GitHub Actions cron (see .github/workflows/weekly-sync.yml), or
locally:

    python -m jobs.weekly_sync --mock --seed-events
    python -m jobs.weekly_sync --dry-run

Idempotency: re-running with the same source data changes nothing. Orders are
keyed on `magento_order_id`, PII writes are upserts, promo codes are
deterministic, and journey state advances only when `next_run_at` has passed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.config import POLICY, Policy, Settings  # noqa: E402
from app.crypto import PIIVault  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.engine import journey as jny  # noqa: E402
from app.engine.lifecycle import Profile, enrich  # noqa: E402
from app.engine.recommender import OfferView, recommend  # noqa: E402
from app.integrations.magento import (  # noqa: E402
    CustomerRecord,
    MagentoClient,
    MockMagentoClient,
    OrderRecord,
)
from app.models import (  # noqa: E402
    BehaviourEvent,
    Customer,
    CustomerFacts,
    CustomerPII,
    JourneyRun,
    MessageLog,
    Offer,
    PromotionRecommendation,
    Purchase,
    SyncRun,
    SyncState,
)

OFFER_LADDER: list[dict[str, Any]] = [
    # code, name, mechanic, discount_pct, margin_cost_pct, min_order_minor, rank
    dict(code="SHIP75", name="Free shipping over EGP 750", mechanic="free_shipping",
         discount_pct=0.0, margin_cost_pct=0.021, min_order_minor=75_000, ladder_rank=10),
    dict(code="PTS2X", name="Double loyalty points", mechanic="points",
         discount_pct=0.0, margin_cost_pct=0.012, min_order_minor=0, ladder_rank=20),
    dict(code="BUNDLE3", name="Complete the routine bundle", mechanic="bundle",
         discount_pct=0.0, margin_cost_pct=0.034, min_order_minor=0, ladder_rank=30),
    dict(code="TIER500", name="Spend EGP 500, get a reward", mechanic="tiered_reward",
         discount_pct=0.0, margin_cost_pct=0.047, min_order_minor=50_000, ladder_rank=40),
    dict(code="GIFT", name="Free gift with purchase", mechanic="gift",
         discount_pct=0.0, margin_cost_pct=0.058, min_order_minor=0, ladder_rank=50),
    dict(code="FIX50", name="EGP 50 off your order", mechanic="fixed_off",
         discount_pct=0.05, margin_cost_pct=0.048, min_order_minor=50_000, ladder_rank=60),
    dict(code="PCT15", name="15% off", mechanic="percent_off",
         discount_pct=0.15, margin_cost_pct=0.150, min_order_minor=0, ladder_rank=70),
]


# ==========================================================================
# helpers
# ==========================================================================
def _naive_utc(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; normalise everything to aware UTC on the way out."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now_from_arg(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def seed_offers(session) -> None:
    existing = {o.code for o in session.scalars(select(Offer)).all()}
    for spec in OFFER_LADDER:
        if spec["code"] in existing:
            continue
        session.add(Offer(active=True, **spec))


def build_source(args) -> Any:
    if args.mock:
        return MockMagentoClient(n_customers=args.mock_customers, now=_now_from_arg(args.now))
    s = Settings()
    if not s.magento_base_url or not s.magento_token:
        raise SystemExit("MAGENTO_BASE_URL and MAGENTO_TOKEN are required unless --mock is used")
    return MagentoClient(s.magento_base_url, s.magento_token)


# ==========================================================================
# 1. ingest
# ==========================================================================
def ingest(session, source, vault: PIIVault, now: datetime, lookback_days: int) -> dict:
    """Upsert customers and orders. Returns counts."""
    state = session.get(SyncState, "orders_watermark")
    since = None
    if state and state.value:
        since = datetime.fromisoformat(state.value)
        since = since - timedelta(days=lookback_days)   # overlap on purpose

    by_magento_id: dict[int, Customer] = {
        c.magento_customer_id: c for c in session.scalars(select(Customer)).all()
    }

    new_customers = 0
    for rec in source.iter_customers(updated_since=since):
        existing = by_magento_id.get(rec.magento_customer_id)
        if existing is None:
            existing = Customer(
                magento_customer_id=rec.magento_customer_id,
                registered_at=rec.created_at,
                store_view=rec.store_view,
                locale=rec.locale,
                city=rec.city,
                consent_email=rec.consent_email,
                consent_push=rec.consent_push,
                consent_sms=rec.consent_sms,
                consent_inapp=rec.consent_inapp,
            )
            session.add(existing)
            session.flush()
            by_magento_id[rec.magento_customer_id] = existing
            new_customers += 1
        else:
            existing.consent_email = rec.consent_email
            existing.consent_push = rec.consent_push
            existing.consent_sms = rec.consent_sms
            existing.consent_inapp = rec.consent_inapp
        _upsert_pii(session, existing.id, rec, vault)

    new_orders = 0
    known_orders = {oid for (oid,) in session.execute(select(Purchase.magento_order_id)).all()}
    for order in source.iter_orders(updated_since=since):
        if order.magento_order_id in known_orders:
            continue
        cust = by_magento_id.get(order.magento_customer_id)
        if cust is None:            # order for a customer outside the pull window
            continue
        session.add(Purchase(
            customer_id=cust.id,
            magento_order_id=order.magento_order_id,
            increment_id=order.increment_id,
            placed_at=order.placed_at,
            grand_total_minor=order.grand_total_minor,
            discount_minor=order.discount_minor,
            items_count=order.items_count,
            categories=order.categories,
            skus=order.skus,
            status=order.status,
        ))
        known_orders.add(order.magento_order_id)
        new_orders += 1

    session.flush()
    _set_state(session, "orders_watermark", now.isoformat())
    return {"customers_new": new_customers, "orders_new": new_orders,
            "customers_total": len(by_magento_id)}


def _upsert_pii(session, customer_id: str, rec: CustomerRecord, vault: PIIVault) -> None:
    row = session.get(CustomerPII, customer_id)
    full_name = f"{rec.firstname} {rec.lastname}".strip()
    if row is None:
        row = CustomerPII(customer_id=customer_id, key_version=vault.key_version)
        session.add(row)
    row.name_enc = vault.encrypt(full_name)
    row.email_enc = vault.encrypt(rec.email)
    row.phone_enc = vault.encrypt(rec.phone)
    row.email_bidx = vault.email_index(rec.email)
    row.phone_bidx = vault.phone_index(rec.phone)
    row.key_version = vault.key_version


def _set_state(session, key: str, value: str) -> None:
    row = session.get(SyncState, key) or SyncState(key=key)
    row.value = value
    session.merge(row)


# ==========================================================================
# 2. facts
# ==========================================================================
def rebuild_facts(session, now: datetime) -> dict[int, CustomerFacts]:
    """Aggregate purchases + behaviour into `customer_facts`.

    Done in Python rather than SQL so the arithmetic is readable and testable;
    at MVP scale (tens of thousands) it takes a few seconds. Move to SQL when
    the base passes ~500k.
    """
    customers = session.scalars(select(Customer)).all()
    purchases = session.execute(select(Purchase)).scalars().all()
    events = session.execute(select(BehaviourEvent)).scalars().all()

    by_customer: dict[str, list[Purchase]] = {}
    for p in purchases:
        by_customer.setdefault(p.customer_id, []).append(p)

    ev_by_customer: dict[str, list[BehaviourEvent]] = {}
    for e in events:
        ev_by_customer.setdefault(e.customer_id, []).append(e)

    cutoff_30 = now - timedelta(days=30)
    cutoff_90 = now - timedelta(days=90)
    out: dict[int, CustomerFacts] = {}

    for c in customers:
        rows = sorted(by_customer.get(c.id, []), key=lambda r: _naive_utc(r.placed_at) or now)
        count = len(rows)
        revenue = sum(r.grand_total_minor for r in rows)
        discounts = sum(r.discount_minor for r in rows)
        first_at = _naive_utc(rows[0].placed_at) if rows else None
        last_at = _naive_utc(rows[-1].placed_at) if rows else None

        cats: Counter = Counter()
        for r in rows:
            for cat in (r.categories or []):
                cats[cat] += 1
        mix = {k: round(v / max(1, sum(cats.values())), 3) for k, v in cats.most_common(5)}

        evs = ev_by_customer.get(c.id, [])
        sessions = sum(1 for e in evs if e.name == "session_start" and (_naive_utc(e.occurred_at) or now) >= cutoff_30)
        carts = sum(1 for e in evs if e.name == "cart_abandoned" and (_naive_utc(e.occurred_at) or now) >= cutoff_90)
        views = sum(1 for e in evs if e.name == "view_item" and (_naive_utc(e.occurred_at) or now) >= cutoff_30)

        orders_90 = sum(1 for r in rows if (_naive_utc(r.placed_at) or now) >= cutoff_90)
        aov = int(revenue / count) if count else 0
        facts = session.get(CustomerFacts, c.id) or CustomerFacts(customer_id=c.id)

        facts.orders_count = count
        facts.orders_90d = orders_90
        facts.lifetime_revenue_minor = revenue
        facts.aov_minor = aov
        facts.discount_share = round(discounts / revenue, 4) if revenue else 0.0
        facts.first_order_at = first_at
        facts.last_order_at = last_at
        facts.days_since_last_order = (now - last_at).days if last_at else None
        facts.days_since_first_order = (now - first_at).days if first_at else None
        facts.sessions_30d = sessions
        facts.carts_abandoned_90d = carts
        facts.views_30d = views
        facts.top_category = cats.most_common(1)[0][0] if cats else None
        facts.category_mix = mix
        facts.messages_7d = _count_recent_messages(session, c.id, now)
        facts.computed_at = now
        session.merge(facts)
        out[c.magento_customer_id] = facts

    session.flush()
    return out


def _count_recent_messages(session, customer_id: str, now: datetime) -> int:
    since = now - timedelta(days=7)
    stmt = select(MessageLog).where(MessageLog.customer_id == customer_id, MessageLog.sent_at >= since)
    return sum(1 for m in session.scalars(stmt).all() if m.status in ("queued", "sent"))


# ==========================================================================
# 3. profiles + scoring
# ==========================================================================
def load_profiles(session, vault: PIIVault, now: datetime) -> list[Profile]:
    customers = session.scalars(select(Customer)).all()
    facts = {f.customer_id: f for f in session.scalars(select(CustomerFacts)).all()}
    profiles: list[Profile] = []
    for c in customers:
        f = facts.get(c.id)
        profiles.append(Profile(
            customer_id=c.id,
            magento_customer_id=c.magento_customer_id,
            registered_at=_naive_utc(c.registered_at) or now,
            orders_count=f.orders_count if f else 0,
            last_order_at=_naive_utc(f.last_order_at) if f else None,
            first_order_at=_naive_utc(f.first_order_at) if f else None,
            lifetime_revenue_minor=f.lifetime_revenue_minor if f else 0,
            aov_minor=f.aov_minor if f else 0,
            discount_share=f.discount_share if f else 0.0,
            price_sensitivity=c.price_sensitivity,
            discount_dependency=c.discount_dependency,
            days_since_last_order=f.days_since_last_order if f else None,
            days_since_first_order=f.days_since_first_order if f else None,
            sessions_30d=f.sessions_30d if f else 0,
            carts_abandoned_90d=f.carts_abandoned_90d if f else 0,
            views_30d=f.views_30d if f else 0,
            top_category=f.top_category if f else None,
            category_mix=f.category_mix if f else {},
            messages_7d=f.messages_7d if f else 0,
            consent={"email": c.consent_email, "push": c.consent_push,
                     "sms": c.consent_sms, "inapp": c.consent_inapp},
            suppressed=c.suppressed,
            lifecycle=c.lifecycle,
            is_vip=c.is_vip,
        ))
    return profiles


def persist_scores(session, profiles: list[Profile], previous: dict[str, str], now: datetime) -> dict:
    moves: Counter = Counter()
    for p in profiles:
        c = session.get(Customer, p.customer_id)
        if c is None:
            continue
        before = previous.get(p.customer_id, c.lifecycle)
        if before != p.lifecycle:
            moves[f"{before}->{p.lifecycle}"] += 1
        c.lifecycle = p.lifecycle
        c.is_vip = p.is_vip
        c.rfm_r, c.rfm_f, c.rfm_m = p.rfm_r, p.rfm_f, p.rfm_m
        c.clv_12m_minor = p.clv_12m_minor
        c.churn_prob = p.churn_prob
        c.updated_at = now
        if p.last_order_at and p.orders_count:
            cadence = max(14, (p.days_since_first_order or 60) // max(1, p.orders_count - 1))
            c.predicted_next_purchase_at = p.last_order_at + timedelta(days=cadence)
    return {"lifecycle_moves": dict(moves)}


# ==========================================================================
# 4. recommendations
# ==========================================================================
def refresh_recommendations(session, profiles: list[Profile], offers: list[OfferView],
                            now: datetime, policy: Policy, sync_run_id: int) -> dict:
    """Carry forward unchanged recommendations, replace changed ones.

    Why not simply expire-and-recreate: a new recommendation mints a new promo
    code, which means a new coupon in Magento and a new email the customer may
    already have received. Continuity is cheaper and less confusing.
    """
    live = session.scalars(
        select(PromotionRecommendation).where(PromotionRecommendation.revoked_at.is_(None))
    ).all()
    live_by_customer: dict[str, PromotionRecommendation] = {}
    for r in live:
        live_by_customer[r.customer_id] = r

    issued = kept = 0
    blocked: Counter = Counter()
    by_offer: Counter = Counter()
    expires = now + timedelta(days=policy.recommendation_ttl_days)
    keep: set[int] = set()

    for p in profiles:
        rec = recommend(p, offers, now, policy)
        existing = live_by_customer.get(p.customer_id)

        if not rec.is_send:
            blocked[rec.blocked_reason or "unknown"] += 1
            continue

        if existing is not None and existing.offer_code == rec.offer_code \
                and existing.reason_code == rec.reason_code:
            existing.score = rec.score
            existing.expires_at = expires
            keep.add(existing.id)
            kept += 1
        else:
            session.add(PromotionRecommendation(
                customer_id=p.customer_id,
                offer_code=rec.offer_code,
                score=rec.score,
                reason_code=rec.reason_code,
                reason_text=rec.reason_text,
                promo_code=rec.promo_code,
                sync_run_id=sync_run_id,
                created_at=now,
                expires_at=expires,
            ))
            issued += 1
        by_offer[rec.offer_code] += 1

    # anything live that we did not keep is stale: revoke it
    revoked = 0
    for r in live:
        if r.id not in keep:
            r.revoked_at = now
            revoked += 1

    session.flush()
    return {"issued": issued, "kept": kept, "revoked": revoked,
            "blocked": dict(blocked), "by_offer": dict(by_offer)}


# ==========================================================================
# 5. journeys
# ==========================================================================
def run_journeys(session, profiles: list[Profile], journeys: list[jny.Journey],
                 offers: list[OfferView], now: datetime, policy: Policy,
                 dry_run: bool = False) -> dict:
    by_id = {p.customer_id: p for p in profiles}
    all_runs = session.scalars(select(JourneyRun)).all()
    active_runs = [r for r in all_runs if r.status == "active"]
    runs_by_customer: dict[str, list[JourneyRun]] = {}
    for r in active_runs:
        runs_by_customer.setdefault(r.customer_id, []).append(r)

    # cooldown: when did this customer last enter each journey, ever?
    last_entry: dict[tuple[str, str], datetime] = {}
    for r in all_runs:
        at = _naive_utc(r.entered_at)
        if at is None:
            continue
        key = (r.customer_id, r.journey_key)
        if key not in last_entry or at > last_entry[key]:
            last_entry[key] = at

    entered = exited = actions_queued = suppressed = deferred = 0
    seen_customers = set(runs_by_customer)
    entry_budget = policy.max_journey_entries_per_run

    # --- advance what is due ---
    for r in active_runs:
        p = by_id.get(r.customer_id)
        if p is None:
            continue
        j = next((x for x in journeys if x.key == r.journey_key), None)
        if j is None:
            r.status = "exited"
            r.exit_reason = "journey_removed"
            continue
        state = jny.RunState(step_index=r.step_index, status=r.status,
                             next_run_at=_naive_utc(r.next_run_at), entered_at=_naive_utc(r.entered_at))
        result = jny.advance(state, j, p, now)
        r.step_index = result.state.step_index
        r.next_run_at = result.state.next_run_at or now
        if result.state.status != "active":
            r.status = result.state.status
            r.exited_at = now
            r.exit_reason = result.exit_reason
            exited += 1
        for act in result.actions:
            if act.type == "send":
                ok = _dispatch(session, p, j, act, now, dry_run)
                if ok:
                    actions_queued += 1
                else:
                    suppressed += 1

    # --- enrol newly eligible ---
    for p in profiles:
        if p.customer_id in seen_customers:
            continue
        if entry_budget <= 0:
            deferred += 1
            continue
        j, why = jny.pick_journey(p, journeys, now, policy=policy)
        if j is None:
            continue
        # cooldown: no re-entry into the same journey before the window passes
        prev = last_entry.get((p.customer_id, j.key))
        if prev is not None and (now - prev).days < j.cooldown_days:
            deferred += 1
            continue
        entry_budget -= 1
        state = jny.start(p, j, now)
        result = jny.advance(state, j, p, now)
        row = JourneyRun(customer_id=p.customer_id, journey_key=j.key, step_index=result.state.step_index,
                         status=result.state.status, entered_at=now,
                         next_run_at=result.state.next_run_at or now)
        if result.state.status != "active":
            row.status = result.state.status
            row.exited_at = now
            row.exit_reason = result.exit_reason
        session.add(row)
        seen_customers.add(p.customer_id)
        entered += 1
        for act in result.actions:
            if act.type == "send":
                if _dispatch(session, p, j, act, now, dry_run):
                    actions_queued += 1
                else:
                    suppressed += 1

    session.flush()
    return {"entered": entered, "exited": exited, "deferred": deferred,
            "actions_queued": actions_queued, "actions_suppressed": suppressed}


def _dispatch(session, p: Profile, j: jny.Journey, act: jny.Action, now: datetime, dry_run: bool) -> bool:
    """Consent + caps are checked HERE, at send time — never at enrolment time.

    A customer who unsubscribed yesterday must not receive today's scheduled
    email, even though they were in the audience when the journey started.
    """
    channel = act.channel or "email"
    status = "queued"
    detail = None

    if p.suppressed:
        status, detail = "suppressed", "suppressed"
    elif not p.consent.get(channel, False):
        status, detail = "suppressed", f"no_{channel}_consent"
    elif p.messages_7d >= POLICY.max_messages_per_7d:
        status, detail = "suppressed", "weekly_cap"

    if dry_run:
        return status == "queued"

    session.add(MessageLog(
        customer_id=p.customer_id,
        channel=channel,
        template=act.template or "",
        journey_key=j.key,
        offer_code=None,
        sent_at=now,
        status=status,
        detail=detail,
    ))
    if status == "queued":
        p.messages_7d += 1
    return status == "queued"


# ==========================================================================
# 6. behaviour spotting + report
# ==========================================================================
def spot_behaviour(profiles: list[Profile], previous_lifecycle: dict[str, str], now: datetime) -> dict:
    """Signals a human should look at this week, not vanity metrics."""
    signals: Counter = Counter()
    for p in profiles:
        if p.carts_abandoned_90d >= 2 and p.clv_12m_minor > 0:
            signals["repeat_cart_abandoners"] += 1
        if p.churn_prob >= 0.65 and p.orders_count >= 2:
            signals["high_churn_risk"] += 1
        if p.sessions_30d >= 3 and (p.days_since_last_order or 0) > 45:
            signals["browsing_but_not_buying"] += 1
        if p.is_vip and p.churn_prob >= 0.5:
            signals["vip_at_risk"] += 1
        if previous_lifecycle.get(p.customer_id) == "lost" and p.days_since_last_order is not None \
                and p.days_since_last_order <= 7:
            signals["reactivated"] += 1
    return dict(signals)


# ==========================================================================
# orchestration
# ==========================================================================
def run(database_url: str | None = None, mock: bool = True, mock_customers: int = 400,
        now: datetime | None = None, journeys_dir: str | Path = "journeys",
        dry_run: bool = False, seed_events: bool = False, verbose: bool = True) -> dict:
    settings = Settings()
    vault = PIIVault(settings.pii_key, settings.blind_index_key, settings.pii_key_version)
    init_db(database_url)
    now = now or datetime.now(timezone.utc)
    policy = POLICY

    source = MockMagentoClient(n_customers=mock_customers, now=now) if mock else MagentoClient(
        settings.magento_base_url, settings.magento_token)
    journeys = jny.load_journeys(journeys_dir)
    problems = [e for j in journeys for e in jny.validate(j)]
    if problems:
        raise SystemExit("journey validation failed:\n  " + "\n  ".join(problems))

    with session_scope(database_url) as session:
        sync_run = SyncRun(job="weekly_sync", started_at=now, status="running", stats={})
        session.add(sync_run)
        session.flush()

        seed_offers(session)
        offers = [
            OfferView(code=o.code, name=o.name, mechanic=o.mechanic,
                      discount_pct=o.discount_pct, margin_cost_pct=o.margin_cost_pct,
                      min_order_minor=o.min_order_minor, ladder_rank=o.ladder_rank,
                      active=o.active)
            for o in session.scalars(select(Offer)).all()
        ]

        stats: dict[str, Any] = {}
        stats["ingest"] = ingest(session, source, vault, now, settings.sync_lookback_days)
        if seed_events:
            stats["demo_events"] = _seed_demo_events(session, now)

        stats["facts"] = {"rebuilt": len(rebuild_facts(session, now))}

        profiles = load_profiles(session, vault, now)
        previous = {p.customer_id: p.lifecycle for p in profiles}
        enrich(profiles, now, policy)
        stats["scores"] = persist_scores(session, profiles, previous, now)
        stats["scores"]["vip"] = sum(1 for p in profiles if p.is_vip)

        stats["recommendations"] = refresh_recommendations(session, profiles, offers, now, policy, sync_run.id)
        stats["journeys"] = run_journeys(session, profiles, journeys, offers, now, policy, dry_run)
        stats["behaviour_signals"] = spot_behaviour(profiles, previous, now)

        stats["lifecycle_counts"] = dict(Counter(p.lifecycle for p in profiles))
        finished = datetime.now(timezone.utc)
        sync_run.finished_at = max(finished, now)   # --now replay must not look backwards
        sync_run.status = "success"
        sync_run.stats = stats
        session.flush()
        report = {"sync_run_id": sync_run.id, "at": now.isoformat(), **stats}

    if verbose:
        print(json.dumps(report, indent=2, default=str))
    return report


def _seed_demo_events(session, now: datetime) -> int:
    """Demo only: fabricate a little storefront behaviour so the behaviour
    signals on the dashboard are not all zero. Never called in production."""
    import random
    rng = random.Random(7)
    customers = session.scalars(select(Customer)).all()
    n = 0
    for c in customers:
        if rng.random() > 0.35:
            continue
        for _ in range(rng.randint(1, 3)):
            session.add(BehaviourEvent(
                customer_id=c.id,
                name=rng.choice(["session_start", "view_item", "add_to_cart", "cart_abandoned"]),
                occurred_at=now - timedelta(days=rng.randint(0, 25)),
                props={"source": "demo"},
            ))
            n += 1
    session.flush()
    return n


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="CVM Hub weekly sync")
    p.add_argument("--mock", action="store_true", help="use the deterministic mock Magento source")
    p.add_argument("--mock-customers", type=int, default=400)
    p.add_argument("--now", default=None, help="ISO timestamp to pin the clock (tests/replays)")
    p.add_argument("--journeys-dir", default=str(Path(__file__).resolve().parents[1] / "journeys"))
    p.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    p.add_argument("--dry-run", action="store_true", help="evaluate but write no messages")
    p.add_argument("--seed-events", action="store_true", help="demo: fabricate behaviour events")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    run(database_url=args.database_url, mock=args.mock, mock_customers=args.mock_customers,
        now=_now_from_arg(args.now), journeys_dir=args.journeys_dir, dry_run=args.dry_run,
        seed_events=args.seed_events, verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
