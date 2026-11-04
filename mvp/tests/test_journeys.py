"""Journey definitions and the execution state machine."""
from datetime import timedelta
from pathlib import Path

import pytest

from app.config import Policy
from app.engine import journey as J
from app.engine.lifecycle import Profile
from tests.conftest import NOW, make_profile

JOURNEY_DIR = Path(__file__).resolve().parents[1] / "journeys"
JOURNEYS = J.load_journeys(JOURNEY_DIR)
P = Policy()


def by_key(key):
    return next(j for j in JOURNEYS if j.key == key)


# --------------------------------------------------------------------------
# definitions
# --------------------------------------------------------------------------
def test_all_five_journeys_load():
    assert {j.key for j in JOURNEYS} == {
        "vip", "churn_risk", "active", "new_registered", "lost_winback"}


def test_every_journey_passes_validation():
    problems = [e for j in JOURNEYS for e in J.validate(j)]
    assert problems == []


def test_priority_order_is_vip_first_lost_last():
    ordered = [j.key for j in JOURNEYS]
    assert ordered.index("vip") < ordered.index("churn_risk") < ordered.index("lost_winback")


def test_every_journey_declares_a_goal_and_cooldown():
    for j in JOURNEYS:
        assert j.goal
        assert j.cooldown_days > 0


def test_validation_catches_a_journey_with_no_exit():
    bad = J.parse_journey({
        "key": "bad", "priority": 1, "entry": {"lifecycle": ["active"]},
        "steps": [{"type": "send", "channel": "email", "template": "x"}],
    })
    assert any("no exit rules" in e for e in J.validate(bad))


def test_validation_catches_send_without_template():
    bad = J.parse_journey({
        "key": "bad2", "priority": 1, "entry": {"lifecycle": ["active"]},
        "exit": [{"field": "suppressed", "op": "is_true"}],
        "steps": [{"type": "send", "channel": "email"}],
    })
    assert any("send without channel/template" in e for e in J.validate(bad))


# --------------------------------------------------------------------------
# eligibility & selection
# --------------------------------------------------------------------------
def test_new_customer_enters_the_onboarding_journey():
    p = make_profile(lifecycle="new", orders_count=1)
    j, why = J.pick_journey(p, JOURNEYS, NOW, policy=P)
    assert j.key == "new_registered"


def test_at_risk_customer_enters_rescue_not_nurture():
    p = make_profile(lifecycle="at_risk", orders_count=4, days_since_last_order=90)
    j, _ = J.pick_journey(p, JOURNEYS, NOW, policy=P)
    assert j.key == "churn_risk"


def test_lost_customer_enters_winback():
    p = make_profile(lifecycle="lost", orders_count=3, days_since_last_order=240)
    j, _ = J.pick_journey(p, JOURNEYS, NOW, policy=P)
    assert j.key == "lost_winback"


def test_vip_wins_over_the_at_risk_journey():
    """A VIP at risk of churning must get VIP treatment, not a rescue campaign."""
    p = make_profile(lifecycle="at_risk", is_vip=True)
    j, _ = J.pick_journey(p, JOURNEYS, NOW, policy=P)
    assert j.key == "vip"


def test_suppressed_customer_matches_nothing():
    p = make_profile(lifecycle="active", suppressed=True)
    j, why = J.pick_journey(p, JOURNEYS, NOW, policy=P)
    assert j is None


def test_customer_already_in_a_journey_is_not_enrolled_again():
    p = make_profile(lifecycle="active")
    j, why = J.pick_journey(p, JOURNEYS, NOW, active_keys={"active"}, policy=P)
    assert j is None and why == "already_in_journey"


def test_priority_resolves_overlapping_matches():
    """Overlap is normal (a VIP can also be at risk). Priority must resolve it
    to exactly one journey — never zero, never two."""
    for lifecycle, vip in [("active", False), ("new", False), ("at_risk", True),
                           ("lost", False), ("at_risk", False), ("active", True)]:
        p = make_profile(lifecycle=lifecycle, is_vip=vip)
        matches = [j for j in JOURNEYS if J.is_eligible(p, j, NOW, P)[0]]
        picked, _ = J.pick_journey(p, JOURNEYS, NOW, policy=P)
        if not matches:
            assert picked is None
        else:
            assert picked is not None
            assert picked.priority == min(m.priority for m in matches)


# --------------------------------------------------------------------------
# state machine
# --------------------------------------------------------------------------
def test_first_send_fires_immediately_then_waits():
    p = make_profile(lifecycle="at_risk", days_since_last_order=90, orders_count=4)
    j = by_key("churn_risk")
    state = J.start(p, j, NOW)

    first = J.advance(state, j, p, NOW)
    assert len(first.actions) == 1
    assert first.actions[0].channel == "email"
    assert first.state.next_run_at == NOW + timedelta(hours=96)

    # an early tick does nothing at all
    early = J.advance(first.state, j, p, NOW + timedelta(hours=10))
    assert early.actions == []

    # the scheduled tick sends the next message
    due = J.advance(first.state, j, p, NOW + timedelta(hours=96))
    assert len(due.actions) == 1
    assert due.actions[0].channel == "push"


def test_journey_completes_and_stops():
    p = make_profile(lifecycle="new", orders_count=0)
    j = by_key("new_registered")
    state = J.start(p, j, NOW)
    clock = NOW
    sends = 0
    for _ in range(40):
        res = J.advance(state, j, p, clock)
        sends += len([a for a in res.actions if a.type == "send"])
        state = res.state
        if state.status != "active":
            break
        clock = (state.next_run_at or clock) + timedelta(seconds=1)
    assert state.status == "completed"
    assert sends == 4          # the YAML has four send steps


def test_exit_fires_before_the_next_step():
    p = make_profile(lifecycle="at_risk", days_since_last_order=90, orders_count=4)
    j = by_key("churn_risk")
    state = J.start(p, j, NOW)
    first = J.advance(state, j, p, NOW)

    # they bought yesterday -> journey exits instead of sending step 2
    p_after = make_profile(lifecycle="at_risk", days_since_last_order=2, orders_count=5)
    res = J.advance(first.state, j, p_after, NOW + timedelta(hours=96))
    assert res.exit_reason == "reactivated"
    assert res.state.status == "exited"
    assert res.actions == []


def test_suppression_exits_immediately():
    p = make_profile(lifecycle="at_risk", days_since_last_order=90, orders_count=4)
    j = by_key("churn_risk")
    state = J.start(p, j, NOW)
    p.suppressed = True
    res = J.advance(state, j, p, NOW)
    assert res.state.status == "exited"
    assert res.exit_reason == "suppressed"


def test_wait_steps_never_loop_forever():
    """A malformed journey with 100 waits must terminate, not hang the worker."""
    steps = tuple(J.Step(type="wait", hours=1) for _ in range(100)) + (J.Step(type="end"),)
    j = J.Journey(key="loop", name="loop", priority=1, goal="x", entry_lifecycles=("active",),
                  entry_all=(), entry_any=(), exit_conditions=(), cooldown_days=1, steps=steps)
    p = make_profile()
    res = J.advance(J.start(p, j, NOW), j, p, NOW)
    assert res.state.step_index <= 51


def test_operator_vocabulary():
    p = make_profile(lifecycle="at_risk", days_since_last_order=90, orders_count=4)
    assert J.compare(J.get_field(p, "days_since_last_order"), "gte", 61)
    assert J.compare(J.get_field(p, "lifecycle"), "in", ["at_risk", "lost"])
    assert J.compare(J.get_field(p, "consent.email"), "is_true", None)
    assert not J.compare(J.get_field(p, "consent.sms"), "is_true", None)
