"""Lifecycle, RFM, churn and VIP — the thresholds that drive every journey."""
from datetime import datetime, timedelta, timezone

from app.config import Policy
from app.engine.lifecycle import (
    ACTIVE, AT_RISK, LOST, NEW,
    apply_lifecycle, classify_lifecycle, estimate_clv_minor,
    flag_vip, heuristic_churn_prob, score_rfm,
)
from tests.conftest import NOW, make_profile

P = Policy()


def test_never_bought_recently_is_new():
    p = make_profile(orders_count=0, last_order_at=None, first_order_at=None,
                     registered_at=NOW - timedelta(days=5))
    assert classify_lifecycle(p, NOW, P) == NEW


def test_never_activated_after_30_days_is_lost():
    p = make_profile(orders_count=0, last_order_at=None, first_order_at=None,
                     registered_at=NOW - timedelta(days=45))
    assert classify_lifecycle(p, NOW, P) == LOST


def test_recent_buyer_is_active():
    p = make_profile(orders_count=5, last_order_at=NOW - timedelta(days=10),
                     first_order_at=NOW - timedelta(days=300))
    assert classify_lifecycle(p, NOW, P) == ACTIVE


def test_quiet_multi_order_customer_is_at_risk():
    p = make_profile(orders_count=4, last_order_at=NOW - timedelta(days=90),
                     first_order_at=NOW - timedelta(days=400))
    assert classify_lifecycle(p, NOW, P) == AT_RISK


def test_one_order_quiet_customer_is_not_at_risk():
    # a customer who bought once and went quiet is "lost", not "at risk":
    # you cannot be at risk of losing a habit that never formed
    p = make_profile(orders_count=1, last_order_at=NOW - timedelta(days=90),
                     first_order_at=NOW - timedelta(days=90))
    assert classify_lifecycle(p, NOW, P) == LOST


def test_long_silence_is_lost():
    p = make_profile(orders_count=6, last_order_at=NOW - timedelta(days=200))
    assert classify_lifecycle(p, NOW, P) == LOST


def test_single_order_inside_onboarding_window_is_new():
    p = make_profile(orders_count=1, last_order_at=NOW - timedelta(days=10),
                     first_order_at=NOW - timedelta(days=10))
    assert classify_lifecycle(p, NOW, P) == NEW


def test_churn_prob_uses_own_cadence():
    # same 60-day silence; the frequent buyer is far more at risk
    frequent = make_profile(orders_count=12, first_order_at=NOW - timedelta(days=240),
                            last_order_at=NOW - timedelta(days=60))
    sporadic = make_profile(orders_count=3, first_order_at=NOW - timedelta(days=700),
                            last_order_at=NOW - timedelta(days=60))
    assert heuristic_churn_prob(frequent, NOW) > heuristic_churn_prob(sporadic, NOW)


def test_churn_prob_is_bounded():
    for days, orders in [(0, 1), (400, 2), (30, 20), (180, 5)]:
        p = make_profile(orders_count=orders,
                         last_order_at=NOW - timedelta(days=days),
                         first_order_at=NOW - timedelta(days=days + 100))
        v = heuristic_churn_prob(p, NOW)
        assert 0.0 <= v <= 1.0


def test_rfm_scores_are_in_range_and_monotonic():
    profiles = [
        make_profile(customer_id=f"c{i}", magento_customer_id=1000 + i,
                     orders_count=i, lifetime_revenue_minor=i * 50_000,
                     last_order_at=NOW - timedelta(days=i * 10))
        for i in range(1, 11)
    ]
    rfm = score_rfm(profiles, NOW)
    for r, f, m in rfm.values():
        assert 1 <= r <= 5 and 1 <= f <= 5 and 1 <= m <= 5
    # most recent buyer has the best R
    assert rfm["c1"][0] == 5
    # highest spender has the best M
    assert rfm["c9"][2] == 5


def test_vip_is_top_percentile_only():
    profiles = [
        make_profile(customer_id=f"c{i}", magento_customer_id=i,
                     clv_12m_minor=i * 1000) for i in range(1, 201)
    ]
    flags = flag_vip(profiles, Policy(vip_percentile=0.95))
    vips = [k for k, v in flags.items() if v]
    assert len(vips) == 10            # top 5% of 200
    assert "c200" in vips and "c1" not in vips


def test_clv_is_zero_without_orders():
    assert estimate_clv_minor(make_profile(orders_count=0, aov_minor=0)) == 0


def test_apply_lifecycle_covers_every_profile():
    profiles = [make_profile(customer_id="a", magento_customer_id=1),
                make_profile(customer_id="b", magento_customer_id=2, orders_count=0)]
    out = apply_lifecycle(profiles, NOW, P)
    assert set(out) == {"a", "b"}
    assert all(v in (NEW, ACTIVE, AT_RISK, LOST) for v in out.values())
