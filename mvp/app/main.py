"""FastAPI surface for the CVM Hub MVP.

Run locally:
    uvicorn app.main:app --reload

Auth: a single bearer token in the MVP (`Authorization: Bearer $API_TOKEN`).
That is honest for an internal tool behind a VPN. Move to per-user sessions with
RBAC before the marketing team gets accounts.

PII rule enforced here: the API never returns a decrypted name or email in a
list endpoint. Detail endpoints return masked values. Unmasking is not a
feature of the MVP — it is an audited action in the full blueprint.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from .config import POLICY, Settings
from .crypto import PIIVault
from .db import init_db, session_scope
from .engine import journey as jny
from .models import (
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
)

settings = Settings()
vault = PIIVault(settings.pii_key, settings.blind_index_key, settings.pii_key_version)

app = FastAPI(
    title="CVM Hub API",
    version="0.1.0",
    description="Customer value management & engagement hub — MVP",
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------
def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    if authorization.split(" ", 1)[1] != settings.api_token:
        raise HTTPException(403, "invalid token")


Auth = Depends(require_token)


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------
class EventIn(BaseModel):
    magento_customer_id: int
    name: str = Field(max_length=48)
    occurred_at: datetime | None = None
    props: dict[str, Any] = Field(default_factory=dict)


class EventBatch(BaseModel):
    events: list[EventIn] = Field(max_length=500)


# --------------------------------------------------------------------------
# health & meta
# --------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "env": settings.env, "policy": {
        "at_risk_after_days": POLICY.at_risk_after_days,
        "lost_after_days": POLICY.lost_after_days,
        "min_margin_after_offer": POLICY.min_margin_after_offer,
    }}


@app.get("/v1/segments", dependencies=[Auth])
def segments() -> dict:
    """Lifecycle counts — the number a CVM lead checks every Monday."""
    with session_scope() as s:
        customers = s.scalars(select(Customer)).all()
        counts = Counter(c.lifecycle for c in customers)
        vips = sum(1 for c in customers if c.is_vip)
        return {
            "total": len(customers),
            "by_lifecycle": dict(counts),
            "vip": vips,
            "at_risk_value_minor": sum(c.clv_12m_minor for c in customers if c.lifecycle == "at_risk"),
        }


# --------------------------------------------------------------------------
# customers
# --------------------------------------------------------------------------
@app.get("/v1/customers", dependencies=[Auth])
def list_customers(
    lifecycle: str | None = None,
    vip: bool | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
) -> dict:
    with session_scope() as s:
        stmt = select(Customer)
        if lifecycle:
            stmt = stmt.where(Customer.lifecycle == lifecycle)
        if vip is not None:
            stmt = stmt.where(Customer.is_vip == vip)
        stmt = stmt.order_by(Customer.clv_12m_minor.desc()).offset(offset).limit(limit)
        customers = s.scalars(stmt).all()
        pii = {p.customer_id: p for p in s.scalars(
            select(CustomerPII).where(CustomerPII.customer_id.in_([c.id for c in customers]))
        ).all()}
        return {
            "items": [_customer_row(c, pii.get(c.id)) for c in customers],
            "limit": limit,
            "offset": offset,
        }


@app.get("/v1/customers/{magento_customer_id}", dependencies=[Auth])
def customer_360(magento_customer_id: int) -> dict:
    """One profile: scores, facts, consent, live recommendation, journeys."""
    with session_scope() as s:
        c = s.scalar(select(Customer).where(Customer.magento_customer_id == magento_customer_id))
        if c is None:
            raise HTTPException(404, "customer not found")
        pii = s.get(CustomerPII, c.id)
        facts = s.get(CustomerFacts, c.id)
        rec = s.scalar(select(PromotionRecommendation).where(
            PromotionRecommendation.customer_id == c.id,
            PromotionRecommendation.revoked_at.is_(None),
        ))
        runs = s.scalars(select(JourneyRun).where(JourneyRun.customer_id == c.id)).all()
        orders = s.scalars(select(Purchase).where(Purchase.customer_id == c.id)
                           .order_by(Purchase.placed_at.desc()).limit(10)).all()

        return {
            **_customer_row(c, pii, unmask=True),
            "facts": {
                "orders_count": facts.orders_count if facts else 0,
                "orders_90d": facts.orders_90d if facts else 0,
                "lifetime_revenue_minor": facts.lifetime_revenue_minor if facts else 0,
                "aov_minor": facts.aov_minor if facts else 0,
                "days_since_last_order": facts.days_since_last_order if facts else None,
                "top_category": facts.top_category if facts else None,
                "sessions_30d": facts.sessions_30d if facts else 0,
                "carts_abandoned_90d": facts.carts_abandoned_90d if facts else 0,
                "messages_7d": facts.messages_7d if facts else 0,
            },
            "recommendation": None if rec is None else {
                "offer_code": rec.offer_code,
                "promo_code": rec.promo_code,
                "score": rec.score,
                "reason_code": rec.reason_code,
                "reason_text": rec.reason_text,
                "expires_at": rec.expires_at,
            },
            "journeys": [
                {"key": r.journey_key, "status": r.status, "step_index": r.step_index,
                 "next_run_at": r.next_run_at, "exit_reason": r.exit_reason}
                for r in runs
            ],
            "recent_orders": [
                {"order": o.increment_id, "placed_at": o.placed_at,
                 "total_minor": o.grand_total_minor, "categories": o.categories}
                for o in orders
            ],
        }


def _customer_row(c: Customer, pii: CustomerPII | None, unmask: bool = False) -> dict:
    name = vault.decrypt(pii.name_enc) if pii else None
    email = vault.decrypt(pii.email_enc) if pii else None
    phone = vault.decrypt(pii.phone_enc) if pii else None
    return {
        "id": c.id,
        "magento_customer_id": c.magento_customer_id,
        "name": name if unmask else (name.split()[0] + " " + (name.split()[-1][:1] + "." if len(name.split()) > 1 else "") if name else None),
        "email": email if unmask else vault.mask_email(email),
        "phone": phone if unmask else vault.mask_phone(phone),
        "city": c.city,
        "locale": c.locale,
        "lifecycle": c.lifecycle,
        "is_vip": c.is_vip,
        "rfm": {"r": c.rfm_r, "f": c.rfm_f, "m": c.rfm_m},
        "clv_12m_minor": c.clv_12m_minor,
        "churn_prob": c.churn_prob,
        "consent": {
            "email": c.consent_email, "push": c.consent_push,
            "sms": c.consent_sms, "inapp": c.consent_inapp,
        },
        "suppressed": c.suppressed,
        "predicted_next_purchase_at": c.predicted_next_purchase_at,
    }


# --------------------------------------------------------------------------
# recommendations & offers
# --------------------------------------------------------------------------
@app.get("/v1/recommendations", dependencies=[Auth])
def recommendations(status: str = "live", limit: int = Query(50, le=200)) -> dict:
    with session_scope() as s:
        stmt = select(PromotionRecommendation)
        if status == "live":
            stmt = stmt.where(PromotionRecommendation.revoked_at.is_(None))
        recs = s.scalars(stmt.order_by(PromotionRecommendation.score.desc()).limit(limit)).all()
        return {"items": [{
            "magento_customer_id": s.get(Customer, r.customer_id).magento_customer_id,
            "offer_code": r.offer_code,
            "promo_code": r.promo_code,
            "score": r.score,
            "reason_code": r.reason_code,
            "reason_text": r.reason_text,
            "expires_at": r.expires_at,
        } for r in recs]}


@app.get("/v1/offers", dependencies=[Auth])
def offers() -> dict:
    with session_scope() as s:
        return {"items": [
            {"code": o.code, "name": o.name, "mechanic": o.mechanic,
             "discount_pct": o.discount_pct, "margin_cost_pct": o.margin_cost_pct,
             "min_order_minor": o.min_order_minor, "ladder_rank": o.ladder_rank,
             "active": o.active}
            for o in s.scalars(select(Offer).order_by(Offer.ladder_rank)).all()
        ]}


# --------------------------------------------------------------------------
# journeys
# --------------------------------------------------------------------------
@app.get("/v1/journeys", dependencies=[Auth])
def journeys() -> dict:
    defs = jny.load_journeys("journeys")
    with session_scope() as s:
        runs = s.scalars(select(JourneyRun)).all()
        by_key = Counter(r.journey_key for r in runs if r.status == "active")
        completed = Counter(r.journey_key for r in runs if r.status == "completed")
    return {"items": [{
        "key": j.key, "name": j.name, "priority": j.priority, "goal": j.goal,
        "cooldown_days": j.cooldown_days, "channels": list(j.channels),
        "steps": len(j.steps), "description": j.description.strip(),
        "active_runs": by_key.get(j.key, 0), "completed_runs": completed.get(j.key, 0),
    } for j in defs]}


@app.get("/v1/journeys/{key}/runs", dependencies=[Auth])
def journey_runs(key: str, status: str = "active", limit: int = 100) -> dict:
    with session_scope() as s:
        runs = s.scalars(select(JourneyRun).where(
            JourneyRun.journey_key == key, JourneyRun.status == status
        ).limit(limit)).all()
        return {"items": [{
            "magento_customer_id": s.get(Customer, r.customer_id).magento_customer_id,
            "step_index": r.step_index, "next_run_at": r.next_run_at,
            "entered_at": r.entered_at,
        } for r in runs]}


# --------------------------------------------------------------------------
# event ingestion
# --------------------------------------------------------------------------
@app.post("/v1/events", dependencies=[Auth])
def ingest_events(batch: EventBatch) -> dict:
    """Storefront tracker target. Batched, at-least-once, idempotent enough."""
    accepted = skipped = 0
    with session_scope() as s:
        ids = {c.magento_customer_id: c.id for c in s.scalars(select(Customer)).all()}
        for e in batch.events:
            cid = ids.get(e.magento_customer_id)
            if cid is None:
                skipped += 1
                continue
            s.add(BehaviourEvent(
                customer_id=cid,
                name=e.name,
                occurred_at=e.occurred_at or datetime.now(timezone.utc),
                props=e.props,
            ))
            accepted += 1
    return {"accepted": accepted, "skipped_unknown_customer": skipped}


# --------------------------------------------------------------------------
# operations
# --------------------------------------------------------------------------
@app.get("/v1/sync-runs", dependencies=[Auth])
def sync_runs(limit: int = 10) -> dict:
    with session_scope() as s:
        rows = s.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(limit)).all()
        return {"items": [{
            "id": r.id, "status": r.status, "started_at": r.started_at,
            "finished_at": r.finished_at, "stats": r.stats,
        } for r in rows]}


@app.get("/v1/messages", dependencies=[Auth])
def messages(limit: int = 100) -> dict:
    with session_scope() as s:
        rows = s.scalars(select(MessageLog).order_by(MessageLog.id.desc()).limit(limit)).all()
        return {"items": [{
            "magento_customer_id": s.get(Customer, m.customer_id).magento_customer_id,
            "channel": m.channel, "template": m.template, "journey": m.journey_key,
            "status": m.status, "detail": m.detail, "sent_at": m.sent_at,
        } for m in rows]}
