"""Lifecycle classification, RFM and VIP flagging.

Pure functions over a plain `Profile` dataclass — no database, no ORM, no clock
dependency. That is what makes the thresholds in `Policy` testable.

Lifecycle vocabulary (deliberately small for the MVP):
    new       -> registered recently, 0-1 orders
    active    -> ordered within the active window
    at_risk   -> was buying, has gone quiet past the risk window
    lost      -> silent longer than the lost window, or never activated

VIP is orthogonal to lifecycle: a customer can be `active` and VIP, or
`at_risk` and VIP. The VIP journey therefore has the highest priority.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from ..config import Policy

NEW, ACTIVE, AT_RISK, LOST = "new", "active", "at_risk", "lost"
LIFECYCLES = (NEW, ACTIVE, AT_RISK, LOST)


@dataclass
class Profile:
    """A denormalised customer view used by every engine."""

    customer_id: str
    magento_customer_id: int
    registered_at: datetime
    orders_count: int = 0
    last_order_at: datetime | None = None
    first_order_at: datetime | None = None
    lifetime_revenue_minor: int = 0
    aov_minor: int = 0
    discount_share: float = 0.0
    price_sensitivity: float = 0.5
    discount_dependency: float = 0.2
    days_since_last_order: int | None = None
    days_since_first_order: int | None = None
    sessions_30d: int = 0
    carts_abandoned_90d: int = 0
    views_30d: int = 0
    top_category: str | None = None
    category_mix: dict = field(default_factory=dict)
    messages_7d: int = 0
    consent: dict = field(default_factory=lambda: {
        "email": False, "push": False, "sms": False, "inapp": False})
    suppressed: bool = False
    # scores written back by the DS layer (or by this module as a placeholder)
    clv_12m_minor: int = 0
    churn_prob: float = 0.0
    lifecycle: str = NEW
    is_vip: bool = False
    rfm_r: int | None = None
    rfm_f: int | None = None
    rfm_m: int | None = None


def _days_between(earlier: datetime | None, later: datetime) -> int | None:
    if earlier is None:
        return None
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=timezone.utc)
    return max(0, (later - earlier).days)


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------
def classify_lifecycle(p: Profile, now: datetime, policy: Policy = Policy()) -> str:
    """Return the lifecycle bucket. Order of checks is the business rule."""

    if p.orders_count == 0:
        idle = _days_between(p.registered_at, now) or 0
        return NEW if idle <= policy.never_activated_after_days else LOST

    days = _days_between(p.last_order_at, now)
    if days is None:
        return NEW
    if days > policy.lost_after_days:
        return LOST
    if days >= policy.at_risk_after_days and p.orders_count >= 2:
        return AT_RISK
    # a single-purchase customer who has gone quiet never formed a habit:
    # they belong in win-back, not in "at risk" — the two get different offers
    if p.orders_count == 1 and days >= policy.at_risk_after_days:
        return LOST
    # still inside the onboarding window and has not bought twice yet
    if p.orders_count <= 1 and (_days_between(p.first_order_at, now) or 0) <= policy.new_window_days:
        return NEW
    return ACTIVE


def apply_lifecycle(profiles: Iterable[Profile], now: datetime, policy: Policy = Policy()) -> dict[str, str]:
    out = {}
    for p in profiles:
        out[p.customer_id] = classify_lifecycle(p, now, policy)
    return out


# --------------------------------------------------------------------------
# churn probability — placeholder until the DS model replaces it
# --------------------------------------------------------------------------
def heuristic_churn_prob(p: Profile, now: datetime) -> float:
    """Compare silence to the customer's own buying cadence.

    A customer who buys every 20 days and has been silent 60 days is far more
    at risk than one who buys every 120 days and has been silent 60. Until the
    real churn model lands, this beats a fixed recency threshold.
    """
    if p.orders_count == 0:
        return 0.5
    days = _days_between(p.last_order_at, now) or 0
    span = _days_between(p.first_order_at, now) or days or 1
    cadence = max(7.0, span / max(1, p.orders_count - 1))
    ratio = days / cadence
    return round(min(0.99, max(0.01, 1 - math.exp(-ratio / 2.0))), 4)


# --------------------------------------------------------------------------
# RFM (cohort-quantile based, like a real RFM)
# --------------------------------------------------------------------------
def _quintile(value: float, cuts: list[float]) -> int:
    """1..5 from ascending cut points (20/40/60/80 percentiles)."""
    for i, c in enumerate(cuts):
        if value <= c:
            return i + 1
    return 5


def _percentiles(values: list[float]) -> list[float]:
    if not values:
        return [0, 0, 0, 0]
    s = sorted(values)
    n = len(s)
    return [s[min(n - 1, int(q * (n - 1)))] for q in (0.2, 0.4, 0.6, 0.8)]


def score_rfm(profiles: list[Profile], now: datetime) -> dict[str, tuple[int, int, int]]:
    """RFM 1-5. R and F use fixed cuts; M uses cross-cohort spend quintiles."""
    spends = [float(p.lifetime_revenue_minor) for p in profiles]
    cuts = _percentiles(spends)
    out: dict[str, tuple[int, int, int]] = {}
    for p in profiles:
        days = _days_between(p.last_order_at, now)
        if p.orders_count == 0 or days is None:
            r = 1
        elif days <= 30:
            r = 5
        elif days <= 60:
            r = 4
        elif days <= 120:
            r = 3
        elif days <= 180:
            r = 2
        else:
            r = 1

        o = p.orders_count
        f = 1 if o <= 1 else 2 if o == 2 else 3 if o <= 4 else 4 if o <= 8 else 5

        m = _quintile(float(p.lifetime_revenue_minor), cuts) if cuts else 1
        out[p.customer_id] = (r, f, m)
    return out


# --------------------------------------------------------------------------
# VIP
# --------------------------------------------------------------------------
def flag_vip(profiles: list[Profile], policy: Policy = Policy()) -> dict[str, bool]:
    """Top `1 - vip_percentile` by 12-month CLV, with a floor of 100 customers."""
    if not profiles:
        return {}
    ranked = sorted(profiles, key=lambda p: p.clv_12m_minor, reverse=True)
    k = max(1, int(round(len(ranked) * (1 - policy.vip_percentile))))
    k = min(k, len(ranked))
    vip_ids = {p.customer_id for p in ranked[:k]}
    return {p.customer_id: (p.customer_id in vip_ids) for p in profiles}


def estimate_clv_minor(p: Profile, horizon_months: int = 12) -> int:
    """Simple placeholder CLV: expected orders in horizon x AOV x retention.

    Replaced by the DS model; here so the hub is useful before the model exists.
    """
    if p.orders_count == 0 or not p.aov_minor:
        return 0
    span = max(1, (_days_between(p.first_order_at, p.last_order_at or p.registered_at) or 30))
    orders_per_day = (p.orders_count - 1) / span if p.orders_count > 1 else 1 / 90
    expected = orders_per_day * horizon_months * 30
    retention = max(0.15, 1 - p.churn_prob)
    return int(p.aov_minor * expected * retention)


def enrich(profiles: list[Profile], now: datetime, policy: Policy = Policy()) -> None:
    """Run the whole scoring pass in place, in dependency order."""
    for p in profiles:
        p.churn_prob = heuristic_churn_prob(p, now)
        if p.days_since_last_order is None:
            p.days_since_last_order = _days_between(p.last_order_at, now)
        if p.days_since_first_order is None:
            p.days_since_first_order = _days_between(p.first_order_at, now)
    rfm = score_rfm(profiles, now)
    for p in profiles:
        p.rfm_r, p.rfm_f, p.rfm_m = rfm[p.customer_id]
        p.clv_12m_minor = estimate_clv_minor(p)
    vip = flag_vip(profiles, policy)
    for p in profiles:
        p.is_vip = vip.get(p.customer_id, False)
    for p in profiles:
        p.lifecycle = classify_lifecycle(p, now, policy)
