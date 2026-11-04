import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Deterministic dev keys + isolated sqlite before anything imports Settings
os.environ.setdefault("ENV", "dev")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

from app.crypto import PIIVault  # noqa: E402
from app.config import Settings  # noqa: E402
from app.engine.lifecycle import Profile  # noqa: E402

NOW = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def vault() -> PIIVault:
    s = Settings()
    return PIIVault(s.pii_key, s.blind_index_key, s.pii_key_version)


def make_profile(**kw) -> Profile:
    """A reasonable 'active customer' that individual tests mutate."""
    base = dict(
        customer_id="c-1",
        magento_customer_id=1001,
        registered_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        orders_count=4,
        first_order_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        last_order_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        lifetime_revenue_minor=400_000,
        aov_minor=100_000,
        days_since_last_order=15,
        days_since_first_order=276,
        clv_12m_minor=600_000,
        price_sensitivity=0.5,
        discount_dependency=0.2,
        top_category="Skincare",
        consent={"email": True, "push": True, "sms": False, "inapp": True},
    )
    base.update(kw)
    return Profile(**base)


@pytest.fixture
def profile() -> Profile:
    return make_profile()


@pytest.fixture
def db_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path/'t.db'}"
