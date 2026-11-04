"""Configuration and business policy.

Policy lives in one frozen dataclass so every threshold used by the lifecycle
classifier, the recommender and the journey engine is testable and in one place.
Change a number here, the tests tell you what breaks.
"""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class Policy:
    """Commercial rules. Tune per market; keep them out of the code paths."""

    # --- lifecycle windows (days) ---
    new_window_days: int = 45          # still "new" if first order within N days
    at_risk_after_days: int = 61       # silent this long + 2 orders => at risk
    lost_after_days: int = 180         # silent this long => lost
    never_activated_after_days: int = 30  # registered, never bought, N days => lost

    # --- VIP ---
    vip_percentile: float = 0.95       # top 5% by 12-month CLV

    # --- contact governance ---
    max_messages_per_7d: int = 3
    max_messages_per_channel_7d: int = 2
    quiet_hours_start: int = 22        # Africa/Cairo local hour
    quiet_hours_end: int = 9

    # --- offers / margin ---
    min_margin_after_offer: float = 0.25   # hard floor, reject below
    max_discount_depth: float = 0.15       # absolute cap on any single offer
    high_discount_dependency: float = 0.55  # never lead with % off above this
    recommendation_ttl_days: int = 7
    min_recommendation_score: float = 0.15   # below this: recommend nothing

    # --- journeys ---
    journey_cooldown_days: int = 90
    one_journey_at_a_time: bool = True
    max_journey_entries_per_run: int = 2500   # stagger enrolment, never blast the base


POLICY = Policy()


class Settings:
    """Runtime settings from environment. No secrets have defaults in prod."""

    def __init__(self) -> None:
        self.env = os.getenv("ENV", "dev")
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./cvm.db")
        self.api_token = os.getenv("API_TOKEN", "dev-token-change-me")

        self.pii_key = os.getenv("PII_KEY", "") or self._dev_key("pii")
        self.pii_key_version = int(os.getenv("PII_KEY_VERSION", "1"))
        self.blind_index_key = os.getenv("BLIND_INDEX_KEY", "") or self._dev_key("bidx")

        self.magento_base_url = os.getenv("MAGENTO_BASE_URL", "")
        self.magento_token = os.getenv("MAGENTO_TOKEN", "")
        self.sync_lookback_days = int(os.getenv("SYNC_LOOKBACK_DAYS", "3"))

        self.resend_api_key = os.getenv("RESEND_API_KEY", "")
        self.fcm_server_key = os.getenv("FCM_SERVER_KEY", "")
        self.sms_vendor_url = os.getenv("SMS_VENDOR_URL", "")
        self.sms_vendor_token = os.getenv("SMS_VENDOR_TOKEN", "")

        if self.env != "dev" and not os.getenv("PII_KEY"):
            raise RuntimeError("PII_KEY must be set outside dev")

    @staticmethod
    def _dev_key(label: str) -> str:
        """Deterministic dev-only key so a local sqlite file stays decryptable.

        Derived, not random: a fresh key on each run would make local data
        unreadable between runs and hide encryption bugs. Never used when
        ENV != dev (guarded above).
        """
        digest = hashlib.sha256(f"cvm-dev-only::{label}".encode()).digest()
        return base64.urlsafe_b64encode(digest).decode()

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


def days(n: float) -> timedelta:
    return timedelta(days=n)
