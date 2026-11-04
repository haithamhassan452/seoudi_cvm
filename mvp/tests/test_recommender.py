"""The promotion recommender: guardrails first, personalisation second."""
from datetime import datetime, timezone

from app.config import Policy
from app.engine.recommender import (
    OfferView, margin_after_offer, promo_code, recommend,
)
from tests.conftest import NOW, make_profile

P = Policy()

OFFERS = [
    OfferView("SHIP75", "Free shipping over EGP 750", "free_shipping", 0.0, 0.021, 75_000, 10),
    OfferView("PTS2X", "Double points", "points", 0.0, 0.012, 0, 20),
    OfferView("BUNDLE3", "Bundle", "bundle", 0.0, 0.034, 0, 30),
    OfferView("GIFT", "Free gift", "gift", 0.0, 0.058, 0, 50),
    OfferView("FIX50", "EGP 50 off", "fixed_off", 0.05, 0.048, 50_000, 60),
    OfferView("PCT15", "15% off", "percent_off", 0.15, 0.150, 0, 70),
]


def test_returns_one_of_the_ladder():
    rec = recommend(make_profile(), OFFERS, NOW, P)
    assert rec.is_send
    assert rec.offer_code in {o.code for o in OFFERS}
    assert 0 < rec.score <= 1


def test_percent_off_is_blocked_by_the_margin_floor():
    """34% margin, 25% floor => any offer costing >9 points is unaffordable.
    A 15% discount must never be recommended, whatever it might do to revenue."""
    for p in (make_profile(), make_profile(is_vip=True), make_profile(lifecycle="lost")):
        rec = recommend(p, OFFERS, NOW, P)
        assert rec.offer_code != "PCT15"


def test_margin_floor_math():
    p = make_profile(discount_dependency=0.0)
    deep = OfferView("PCT20", "20% off", "percent_off", 0.20, 0.20, 0, 10)
    assert margin_after_offer(p, deep, cost_margin=0.34) < P.min_margin_after_offer


def test_vip_never_gets_a_discount_mechanic():
    vip = make_profile(is_vip=True, clv_12m_minor=5_000_000)
    rec = recommend(vip, OFFERS, NOW, P)
    assert rec.offer_code not in {"FIX50", "PCT15"}
    assert rec.reason_code == "loyalty_reward"


def test_discount_dependent_customer_is_not_given_another_discount():
    dependent = make_profile(discount_dependency=0.85, price_sensitivity=0.9)
    rec = recommend(dependent, OFFERS, NOW, P)
    assert rec.is_send
    assert rec.offer_code not in {"FIX50", "PCT15"}


def test_suppressed_customer_gets_nothing():
    rec = recommend(make_profile(suppressed=True), OFFERS, NOW, P)
    assert not rec.is_send
    assert rec.blocked_reason == "suppressed"


def test_no_consent_gets_nothing():
    rec = recommend(make_profile(consent={"email": False, "push": False,
                                          "sms": False, "inapp": False}), OFFERS, NOW, P)
    assert not rec.is_send
    assert rec.blocked_reason == "no_consent"


def test_weekly_contact_cap_gets_nothing():
    rec = recommend(make_profile(messages_7d=3), OFFERS, NOW, P)
    assert not rec.is_send
    assert rec.blocked_reason == "fatigued"


def test_low_value_customer_returns_send_nothing():
    """'Send nothing' must be a real outcome, not a fallback to a default offer."""
    rec = recommend(make_profile(clv_12m_minor=0, orders_count=0,
                                 lifecycle="lost", price_sensitivity=1.0,
                                 days_since_last_order=300), OFFERS, NOW, P)
    assert not rec.is_send
    assert rec.blocked_reason in {"below_threshold", "no_eligible_offer"}


def test_promo_code_is_deterministic_and_per_customer():
    a = promo_code("SHIP75", 1001)
    b = promo_code("SHIP75", 1001)
    c = promo_code("SHIP75", 1002)
    assert a == b          # idempotent across weekly runs
    assert a != c          # unique per customer
    assert a.startswith("CVM") and len(a) == 11


def test_recommendation_is_stable_across_runs():
    p = make_profile()
    assert recommend(p, OFFERS, NOW, P) == recommend(p, OFFERS, NOW, P)


def test_reason_codes_come_from_the_closed_vocabulary():
    from app.engine.recommender import REASON
    for p in (make_profile(), make_profile(is_vip=True), make_profile(lifecycle="at_risk"),
              make_profile(lifecycle="lost"), make_profile(lifecycle="new", orders_count=0)):
        rec = recommend(p, OFFERS, NOW, P)
        if rec.is_send:
            assert rec.reason_code in REASON


def test_no_active_offers_degrades_gracefully():
    rec = recommend(make_profile(), [], NOW, P)
    assert not rec.is_send
    assert rec.blocked_reason == "no_eligible_offer"


def test_different_segments_get_different_offers():
    """If every customer gets the same offer, it isn't a recommender."""
    picks = {
        recommend(make_profile(is_vip=True, clv_12m_minor=5_000_000), OFFERS, NOW, P).offer_code,
        recommend(make_profile(orders_count=9, clv_12m_minor=900_000,
                               aov_minor=250_000, lifecycle="active"), OFFERS, NOW, P).offer_code,
        recommend(make_profile(orders_count=0, lifecycle="new", aov_minor=60_000,
                               clv_12m_minor=200_000), OFFERS, NOW, P).offer_code,
    }
    assert len(picks) >= 2
