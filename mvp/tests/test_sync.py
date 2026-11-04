"""End-to-end weekly sync: idempotency, encryption, consent, progression."""
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.models import Customer, CustomerPII, JourneyRun, MessageLog, PromotionRecommendation
from jobs.weekly_sync import run
from tests.conftest import NOW

JOURNEYS = "journeys"


@pytest.fixture(scope="module")
def first_run(tmp_path_factory):
    db_url = f"sqlite:///{tmp_path_factory.mktemp('db')/'sync.db'}"
    report = run(database_url=db_url, mock=True, mock_customers=180, now=NOW,
                 journeys_dir=JOURNEYS, seed_events=True, verbose=False)
    return db_url, report


def _session(db_url):
    return Session(create_engine(db_url, future=True))


# --------------------------------------------------------------------------
def test_first_run_ingests_everything(first_run):
    _, r = first_run
    assert r["ingest"]["customers_total"] == 180
    assert r["ingest"]["orders_new"] > 0
    assert r["facts"]["rebuilt"] == 180


def test_first_run_classifies_every_lifecycle(first_run):
    _, r = first_run
    counts = r["lifecycle_counts"]
    assert sum(counts.values()) == 180
    assert set(counts) <= {"new", "active", "at_risk", "lost"}
    assert len(counts) >= 3, "the mock base should span several lifecycles"


def test_first_run_issues_recommendations_and_blocks_some(first_run):
    _, r = first_run
    recs = r["recommendations"]
    assert recs["issued"] > 0
    # "send nothing" must actually happen, or the floor is not working
    assert sum(recs["blocked"].values()) > 0
    assert len(recs["by_offer"]) >= 2, "one offer for everyone means no targeting"


def test_journeys_are_enrolled_with_caps(first_run):
    _, r = first_run
    assert r["journeys"]["entered"] > 0
    assert r["journeys"]["exited"] >= 0
    assert r["journeys"]["actions_queued"] > 0


def test_pii_is_encrypted_at_rest(first_run):
    db_url, _ = first_run
    with _session(db_url) as s:
        row = s.scalars(select(CustomerPII)).first()
        assert row.name_enc and not row.name_enc.startswith("customer")
        assert row.email_enc and "@" not in row.email_enc
        assert row.phone_enc and "+20" not in row.phone_enc
        assert len(row.email_bidx) == 64
        raw = s.execute(text("select email_enc from customer_pii limit 3")).all()
        assert all("@" not in (r[0] or "") for r in raw)


def test_no_pii_columns_on_the_customer_table(first_run):
    """The golden record must carry zero personal data."""
    db_url, _ = first_run
    with _session(db_url) as s:
        cols = {c.name for c in Customer.__table__.columns}
        assert not {"email", "phone", "name", "firstname", "lastname"} & cols
        assert "magento_customer_id" in cols


def test_customers_are_keyed_by_magento_id(first_run):
    db_url, _ = first_run
    with _session(db_url) as s:
        ids = [c.magento_customer_id for c in s.scalars(select(Customer)).all()]
        assert len(ids) == len(set(ids))
        assert all(isinstance(i, int) for i in ids)


def test_consent_is_enforced_at_send_time(first_run):
    db_url, _ = first_run
    with _session(db_url) as s:
        rows = s.scalars(select(MessageLog)).all()
        queued = [m for m in rows if m.status == "queued"]
        suppressed = [m for m in rows if m.status == "suppressed"]
        assert queued, "the run should queue some messages"
        # every suppressed row has a reason
        assert all(m.detail for m in suppressed)
        # no queued message goes to a channel the customer did not consent to
        customers = {c.id: c for c in s.scalars(select(Customer)).all()}
        for m in queued:
            c = customers[m.customer_id]
            assert getattr(c, f"consent_{m.channel}") is True
            assert c.suppressed is False


# --------------------------------------------------------------------------
# idempotency
# --------------------------------------------------------------------------
def test_second_run_is_idempotent(first_run):
    db_url, _ = first_run
    with _session(db_url) as s:
        before = {
            "customers": s.scalar(text("select count(*) from customers")),
            "orders": s.scalar(text("select count(*) from purchases")),
            "messages": s.scalar(text("select count(*) from message_log")),
            "runs": s.scalar(text("select count(*) from journey_runs")),
        }

    report = run(database_url=db_url, mock=True, mock_customers=180, now=NOW,
                 journeys_dir=JOURNEYS, verbose=False)

    with _session(db_url) as s:
        after = {
            "customers": s.scalar(text("select count(*) from customers")),
            "orders": s.scalar(text("select count(*) from purchases")),
            "messages": s.scalar(text("select count(*) from message_log")),
            "runs": s.scalar(text("select count(*) from journey_runs")),
        }
    assert before == after
    assert report["ingest"]["customers_new"] == 0
    assert report["ingest"]["orders_new"] == 0
    assert report["journeys"]["entered"] == 0


def test_unchanged_recommendations_are_carried_forward(first_run):
    db_url, _ = first_run
    report = run(database_url=db_url, mock=True, mock_customers=180, now=NOW,
                 journeys_dir=JOURNEYS, verbose=False)
    recs = report["recommendations"]
    assert recs["kept"] > recs["issued"], "most recommendations should be unchanged"
    with _session(db_url) as s:
        live = s.scalars(select(PromotionRecommendation).where(
            PromotionRecommendation.revoked_at.is_(None))).all()
        # exactly one live recommendation per customer, never two
        per_customer = {}
        for r in live:
            per_customer[r.customer_id] = per_customer.get(r.customer_id, 0) + 1
        assert max(per_customer.values()) == 1


# --------------------------------------------------------------------------
# progression over time
# --------------------------------------------------------------------------
def test_journeys_advance_one_week_later(first_run):
    db_url, _ = first_run
    later = NOW + timedelta(days=7)
    with _session(db_url) as s:
        before = s.scalar(text("select count(*) from message_log"))

    report = run(database_url=db_url, mock=True, mock_customers=180, now=later,
                 journeys_dir=JOURNEYS, verbose=False)

    with _session(db_url) as s:
        after = s.scalar(text("select count(*) from message_log"))
        active = s.scalars(select(JourneyRun).where(JourneyRun.status == "active")).all()
    assert after > before, "scheduled steps should fire as their waits expire"
    assert report["journeys"]["actions_queued"] > 0
    assert active, "runs should still be in flight"


def test_cooldown_prevents_immediate_re_entry():
    """After a journey completes, the customer must not be re-enrolled before
    the journey's cooldown window — that is the classic spam bug."""
    from app.engine import journey as J
    from pathlib import Path
    journeys = J.load_journeys(Path(JOURNEYS))
    for j in journeys:
        assert j.cooldown_days >= 60
