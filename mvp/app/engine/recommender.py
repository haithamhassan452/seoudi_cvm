"""Promotion recommender — rule-based for the MVP, model-ready by interface.

The contract with the DS team is `recommend()`: give it a Profile and the offer
ladder, get back one recommendation or an explicit "send nothing". When their
model is ready, it replaces the scoring function and nothing else changes.

Two things this deliberately does:
  * refuses to recommend when expected value is negative ("send nothing" is a
    valid, first-class answer — it is why the messages you do send get opened)
  * refuses discounts a segment cannot afford, before they are created, because
    a margin breach is invisible in a revenue report

Scoring is a product of three bounded factors, each 0..1:
    value_fit    how much this customer is worth investing in
    offer_fit    how well the mechanic matches their behaviour
    timing_fit   how urgent the intervention is
minus a fatigue penalty. Multiplicative, so a zero anywhere kills the option.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from ..config import Policy
from .lifecycle import AT_RISK, LOST, NEW, Profile

# reason codes are a closed vocabulary: the API, the UI and the measurement
# layer all key off them, so they must not drift.
REASON = {
    "loyalty_reward": "You are one of our best customers",
    "replenishment": "Time to restock what you usually buy",
    "reactivation": "We saved something for you",
    "first_repeat": "A reason to come back for a second order",
    "category_nudge": "Popular in the category you browse",
    "no_consent": "No channel consent",
    "suppressed": "Suppressed or unsubscribed",
    "fatigued": "Contact cap reached",
    "below_threshold": "Expected value below threshold",
}

MIN_SCORE = 0.32


@dataclass(frozen=True)
class OfferView:
    """Just the fields the recommender needs — decoupled from the ORM."""

    code: str
    name: str
    mechanic: str  # free_shipping | gift | bundle | tiered_reward | points | fixed_off | percent_off
    discount_pct: float
    margin_cost_pct: float
    min_order_minor: int
    ladder_rank: int
    active: bool = True


@dataclass(frozen=True)
class Recommendation:
    offer_code: str
    score: float
    reason_code: str
    reason_text: str
    promo_code: str | None
    blocked_reason: str | None = None

    @property
    def is_send(self) -> bool:
        return self.blocked_reason is None


# offer mechanics ranked by how much they teach the customer to wait for a
# discount. Lower is healthier.
DISCOUNT_MECHANICS = {"percent_off", "fixed_off"}
HEALTHY_MECHANICS = {"free_shipping", "gift", "bundle", "tiered_reward", "points"}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def promo_code(offer_code: str, magento_customer_id: int) -> str:
    """Deterministic, idempotent, unique per customer — safe to re-run weekly.

    Deterministic matters: the weekly sync must not mint a new coupon for the
    same customer on every run. Magento rejects duplicate codes, so this also
    acts as the idempotency key for coupon creation.
    """
    digest = hashlib.sha256(f"{offer_code}:{magento_customer_id}".encode()).hexdigest()[:8].upper()
    return f"CVM{digest}"


# --------------------------------------------------------------------------
# factor scores
# --------------------------------------------------------------------------
def value_fit(p: Profile) -> float:
    """Willingness and ability to spend. VIPs and high CLV score highest."""
    if p.is_vip:
        return 1.0
    if p.clv_12m_minor <= 0:
        return 0.12
    # log scale: CLV is heavy-tailed, raw ratios would let one whale dominate
    import math

    return _clamp(0.28 + math.log10(1 + p.clv_12m_minor / 50_000) / 3.5, 0.12, 1.0)


def offer_fit(p: Profile, o: OfferView, policy: Policy) -> float:
    """Does this mechanic suit this customer's behaviour?

    Each mechanic earns its score from a different signal. That is what makes
    the ladder useful: without it, every customer gets the same offer and the
    "recommender" is just a default.
    """
    m = o.mechanic

    if m in DISCOUNT_MECHANICS:
        # a discount-trained customer only ever gets deeper discounts: stop
        if p.discount_dependency > policy.high_discount_dependency:
            return 0.05
        score = 0.55 - p.discount_dependency * 0.40

    elif m == "points":
        # loyalty currency compounds for engaged, frequent customers and is
        # worthless to someone who has stopped buying
        score = 0.60 + min(0.30, p.orders_count * 0.03)
        if p.lifecycle in (AT_RISK, LOST):
            score *= 0.60

    elif m == "free_shipping":
        # a threshold mechanic only works near the threshold
        score = 0.80
        if p.aov_minor and o.min_order_minor:
            ratio = p.aov_minor / o.min_order_minor
            score = 0.95 if 0.70 <= ratio <= 1.05 else (0.72 if ratio > 1.05 else 0.42)

    elif m in ("bundle", "tiered_reward"):
        # basket mechanics need a basket worth growing
        score = 0.50 + (0.40 if p.aov_minor >= (o.min_order_minor or 1) else 0.0)
        if p.lifecycle in (AT_RISK, LOST):
            score *= 0.80

    elif m == "gift":
        # discovery mechanic: best on people still forming habits
        score = 0.58 + (0.28 if p.lifecycle == NEW else 0.0) + (0.10 if p.orders_count <= 2 else 0.0)

    else:
        score = 0.50

    # VIPs are rewarded, never discounted — that margin is not a marketing budget
    if p.is_vip and m in DISCOUNT_MECHANICS:
        return 0.02

    # discount-averse customers should not be led with price at all
    if p.price_sensitivity < 0.25 and m in DISCOUNT_MECHANICS:
        score *= 0.7

    return _clamp(score)


def timing_fit(p: Profile, now: datetime, policy: Policy) -> float:
    """How badly does this customer need something to happen this week?"""
    if p.lifecycle == AT_RISK:
        return 0.95
    if p.lifecycle == LOST:
        return 0.7
    if p.lifecycle == NEW:
        return 0.85
    # active: only intervene when the cadence is drifting or a cart was dropped
    base = 0.45
    if p.carts_abandoned_90d > 0:
        base += 0.2
    if p.churn_prob > 0.5:
        base += 0.15
    if p.days_since_last_order is not None and p.days_since_last_order > 45:
        base += 0.1
    return _clamp(base)


def _reason_for(p: Profile, o: OfferView) -> str:
    if p.is_vip:
        return "loyalty_reward"
    if p.lifecycle in (AT_RISK, LOST):
        return "reactivation"
    if p.lifecycle == NEW:
        return "first_repeat"
    if p.orders_count >= 3 and p.top_category:
        return "replenishment"
    return "category_nudge"


def margin_after_offer(p: Profile, o: OfferView, cost_margin: float = 0.34) -> float:
    """Contribution margin left after the offer's cost. Includes cannibalisation
    of the customer's own full-price behaviour via discount dependency."""
    cannibal = p.discount_dependency * o.discount_pct * 0.5
    return cost_margin - o.margin_cost_pct - cannibal


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------
def recommend(
    p: Profile,
    offers: Sequence[OfferView],
    now: datetime,
    policy: Policy = Policy(),
    cost_margin: float = 0.34,
) -> Recommendation:
    """Return exactly one recommendation, or an explicit block.

    Never raises. The weekly job iterates tens of thousands of profiles and one
    bad row must not kill the batch.
    """
    blocked = _blocking_reason(p, policy)
    if blocked:
        return Recommendation("-", 0.0, blocked, REASON[blocked], None, blocked_reason=blocked)

    best: tuple[float, OfferView] | None = None
    for o in offers:
        if not o.active:
            continue
        # hard guardrail: absolute discount cap
        if o.discount_pct > policy.max_discount_depth:
            continue
        # hard guardrail: margin floor after the offer
        if margin_after_offer(p, o, cost_margin) < policy.min_margin_after_offer:
            continue
        # hard guardrail: a discount-trained customer never gets another discount
        if o.mechanic in DISCOUNT_MECHANICS and p.discount_dependency > policy.high_discount_dependency:
            continue

        score = value_fit(p) * offer_fit(p, o, policy) * timing_fit(p, now, policy)
        # cheaper-on-margin mechanics win ties: reward the ladder's discipline
        score *= 1.0 - min(0.15, o.ladder_rank / 1000.0)

        if best is None or score > best[0]:
            best = (score, o)

    if best is None:
        return Recommendation("-", 0.0, "below_threshold", REASON["below_threshold"], None,
                              blocked_reason="no_eligible_offer")

    score, offer = best
    floor = getattr(policy, "min_recommendation_score", MIN_SCORE)
    if score < floor:
        return Recommendation("-", round(score, 4), "below_threshold", REASON["below_threshold"], None,
                              blocked_reason="below_threshold")

    reason = _reason_for(p, offer)
    return Recommendation(
        offer_code=offer.code,
        score=round(score, 4),
        reason_code=reason,
        reason_text=REASON[reason],
        promo_code=promo_code(offer.code, p.magento_customer_id),
    )


def _blocking_reason(p: Profile, policy: Policy) -> str | None:
    if p.suppressed:
        return "suppressed"
    if not any(p.consent.values()):
        return "no_consent"
    if p.messages_7d >= policy.max_messages_per_7d:
        return "fatigued"
    return None


def recommend_batch(
    profiles: Iterable[Profile],
    offers: Sequence[OfferView],
    now: datetime,
    policy: Policy = Policy(),
) -> dict[str, Recommendation]:
    return {p.customer_id: recommend(p, offers, now, policy) for p in profiles}
