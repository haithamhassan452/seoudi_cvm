"""SQLAlchemy models.

Identity rule (MVP)
-------------------
* `customers.id`            -> surrogate UUID, internal only, never in a URL or token
* `customers.magento_customer_id` -> the ONLY external business key
* phone / email / name      -> encrypted attributes in a separate table

Why a separate PII table: the recommender, the lifecycle job and any analyst
query can read `customers` + `customer_facts` without the ability to decrypt
anything personal. Least privilege by schema, not by promise.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Customer(Base):
    """Golden record. Contains no PII — only identifiers, scores and status."""

    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    magento_customer_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)

    store_view: Mapped[str] = mapped_column(String(32), default="default")
    locale: Mapped[str] = mapped_column(String(8), default="ar")
    city: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str] = mapped_column(String(2), default="EG")

    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # --- lifecycle & value ---
    lifecycle: Mapped[str] = mapped_column(String(16), default="new", index=True)
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    rfm_r: Mapped[int | None] = mapped_column(Integer)
    rfm_f: Mapped[int | None] = mapped_column(Integer)
    rfm_m: Mapped[int | None] = mapped_column(Integer)
    clv_12m_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    churn_prob: Mapped[float] = mapped_column(Float, default=0.0)
    price_sensitivity: Mapped[float] = mapped_column(Float, default=0.5)
    discount_dependency: Mapped[float] = mapped_column(Float, default=0.2)
    predicted_next_purchase_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- governance ---
    consent_email: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_push: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_sms: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_inapp: Mapped[bool] = mapped_column(Boolean, default=False)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    pii: Mapped["CustomerPII"] = relationship(back_populates="customer", uselist=False, cascade="all, delete-orphan")
    facts: Mapped["CustomerFacts"] = relationship(back_populates="customer", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (Index("ix_customers_lifecycle_vip", "lifecycle", "is_vip"),)


class CustomerPII(Base):
    """Encrypted personal data. Read only by code that needs to render or send."""

    __tablename__ = "customer_pii"

    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True)
    name_enc: Mapped[str | None] = mapped_column(Text)
    email_enc: Mapped[str | None] = mapped_column(Text)
    phone_enc: Mapped[str | None] = mapped_column(Text)

    # blind indexes: deterministic HMAC, safe to look up, impossible to reverse
    email_bidx: Mapped[str | None] = mapped_column(String(64), index=True)
    phone_bidx: Mapped[str | None] = mapped_column(String(64), index=True)

    key_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    customer: Mapped[Customer] = relationship(back_populates="pii")


class Purchase(Base):
    """One row per Magento order. Idempotent on magento_order_id."""

    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    magento_order_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    increment_id: Mapped[str | None] = mapped_column(String(32))

    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    grand_total_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    discount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    items_count: Mapped[int] = mapped_column(Integer, default=0)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    skus: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="complete")

    __table_args__ = (Index("ix_purchases_customer_placed", "customer_id", "placed_at"),)


class BehaviourEvent(Base):
    """Storefront behaviour: view_item, add_to_cart, search, checkout_start…"""

    __tablename__ = "behaviour_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(48), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    props: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (Index("ix_events_customer_name_time", "customer_id", "name", "occurred_at"),)


class CustomerFacts(Base):
    """Recomputed weekly. Everything the journey engine and recommender need."""

    __tablename__ = "customer_facts"

    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True)

    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    orders_90d: Mapped[int] = mapped_column(Integer, default=0)
    lifetime_revenue_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    aov_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    discount_share: Mapped[float] = mapped_column(Float, default=0.0)

    first_order_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_order_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    days_since_last_order: Mapped[int | None] = mapped_column(Integer)
    days_since_first_order: Mapped[int | None] = mapped_column(Integer)

    sessions_30d: Mapped[int] = mapped_column(Integer, default=0)
    carts_abandoned_90d: Mapped[int] = mapped_column(Integer, default=0)
    views_30d: Mapped[int] = mapped_column(Integer, default=0)

    top_category: Mapped[str | None] = mapped_column(String(48))
    category_mix: Mapped[dict] = mapped_column(JSON, default=dict)

    messages_7d: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    customer: Mapped[Customer] = relationship(back_populates="facts")


class Offer(Base):
    """The offer ladder. Ordered by margin cost; the recommender prefers cheap."""

    __tablename__ = "offers"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(96))
    mechanic: Mapped[str] = mapped_column(String(32))
    discount_pct: Mapped[float] = mapped_column(Float, default=0.0)
    margin_cost_pct: Mapped[float] = mapped_column(Float, default=0.0)
    min_order_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    ladder_rank: Mapped[int] = mapped_column(Integer, default=50)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class PromotionRecommendation(Base):
    """Output of the weekly recommender. One live recommendation per customer."""

    __tablename__ = "promotion_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    offer_code: Mapped[str] = mapped_column(String(32), ForeignKey("offers.code"))

    score: Mapped[float] = mapped_column(Float, default=0.0)
    reason_code: Mapped[str] = mapped_column(String(40))
    reason_text: Mapped[str] = mapped_column(String(160))
    promo_code: Mapped[str | None] = mapped_column(String(48))

    sync_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sync_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_promo_customer_live", "customer_id", "expires_at"),)


class JourneyRun(Base):
    """A customer's progress through one journey."""

    __tablename__ = "journey_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    journey_key: Mapped[str] = mapped_column(String(48), index=True)

    step_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|completed|exited
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_reason: Mapped[str | None] = mapped_column(String(48))

    # No unique constraint on (customer_id, journey_key): a customer can and
    # should re-enter the same journey after the cooldown window, and we want
    # that history. "One active run at a time" is enforced in the runner.
    __table_args__ = (Index("ix_journey_customer_status", "customer_id", "status"),)


class MessageLog(Base):
    """Every send, in one table. This is the exposure log measurement depends on."""

    __tablename__ = "message_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(16), index=True)
    template: Mapped[str] = mapped_column(String(64))
    journey_key: Mapped[str | None] = mapped_column(String(48), index=True)
    offer_code: Mapped[str | None] = mapped_column(String(32))

    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|sent|failed|suppressed
    provider_id: Mapped[str | None] = mapped_column(String(96))
    detail: Mapped[str | None] = mapped_column(Text)


class SyncRun(Base):
    """One row per weekly job execution — your audit trail and diff report."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(48), default="weekly_sync")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class SyncState(Base):
    """Watermarks / key-value state for idempotent incremental jobs."""

    __tablename__ = "sync_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
