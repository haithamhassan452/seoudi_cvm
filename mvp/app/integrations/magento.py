"""Magento (Adobe Commerce) read client + a deterministic mock for local dev.

Read-only. The hub never writes to the Magento database — on Cloud you cannot —
and write-backs (customer tags, coupons, cart price rules) go through the REST
endpoints listed in the blueprint. This MVP only needs reads, so it only
implements reads plus a `MockMagentoClient` so the whole pipeline runs offline.

Rate-limit posture: sequential page requests with a small backoff. Magento Cloud
rate-limits integrations, and a weekly job has no reason to be aggressive.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator, Protocol

import requests

PAGE_SIZE = 100


@dataclass
class CustomerRecord:
    magento_customer_id: int
    email: str
    firstname: str
    lastname: str
    phone: str | None
    created_at: datetime
    city: str | None = None
    store_view: str = "default"
    locale: str = "ar"
    consent_email: bool = True
    consent_push: bool = False
    consent_sms: bool = False
    consent_inapp: bool = True


@dataclass
class OrderRecord:
    magento_order_id: int
    increment_id: str
    magento_customer_id: int
    placed_at: datetime
    grand_total_minor: int
    discount_minor: int = 0
    items_count: int = 1
    categories: list = field(default_factory=list)
    skus: list = field(default_factory=list)
    status: str = "complete"


class OrderSource(Protocol):
    def iter_customers(self, updated_since: datetime | None = None) -> Iterator[CustomerRecord]: ...
    def iter_orders(self, updated_since: datetime | None = None) -> Iterator[OrderRecord]: ...


# --------------------------------------------------------------------------
# real client
# --------------------------------------------------------------------------
class MagentoClient:
    """Adobe Commerce REST API (v1).

    Auth: a dedicated integration token with least-privilege ACLs. Never an
    admin user's password, never a token with write scope it does not need.
    """

    def __init__(self, base_url: str, token: str, timeout: int = 30, page_size: int = PAGE_SIZE):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.page_size = page_size
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # ---------- plumbing ----------
    def _get(self, path: str, params: dict | None = None, retries: int = 3) -> dict:
        url = f"{self.base_url}/rest/V1/{path.lstrip('/')}"
        last: Exception | None = None
        for attempt in range(retries):
            try:
                r = self._session.get(url, params=params, timeout=self.timeout)
                if r.status_code == 429:
                    time.sleep(2 ** attempt * 2)
                    continue
                r.raise_for_status()
                return r.json()
            except Exception as exc:  # pragma: no cover - network path
                last = exc
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Magento GET {path} failed: {last}")

    def _paged(self, path: str, updated_since: datetime | None, extra_filters: list[dict] | None = None) -> Iterator[dict]:
        page = 1
        while True:
            filters = list(extra_filters or [])
            if updated_since:
                filters.append({"field": "updated_at", "value": _iso(updated_since), "condition_type": "gteq"})
            params: dict = {
                "searchCriteria[pageSize]": self.page_size,
                "searchCriteria[currentPage]": page,
            }
            for i, f in enumerate(filters):
                prefix = f"searchCriteria[filter_groups][{i}][filters][0]"
                params[f"{prefix}[field]"] = f["field"]
                params[f"{prefix}[value]"] = f["value"]
                params[f"{prefix}[condition_type]"] = f.get("condition_type", "eq")

            data = self._get(path, params)
            items = data.get("items", []) or []
            if not items:
                return
            yield from items
            if len(items) < self.page_size:
                return
            page += 1

    # ---------- domain ----------
    def iter_customers(self, updated_since: datetime | None = None) -> Iterator[CustomerRecord]:
        for raw in self._paged("customers/search", updated_since):
            yield parse_customer(raw)

    def iter_orders(self, updated_since: datetime | None = None) -> Iterator[OrderRecord]:
        for raw in self._paged("orders", updated_since):
            rec = parse_order(raw)
            if rec is not None:
                yield rec


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_customer(raw: dict) -> CustomerRecord:
    created = raw.get("created_at") or "1970-01-01 00:00:00"
    return CustomerRecord(
        magento_customer_id=int(raw["id"]),
        email=raw.get("email", ""),
        firstname=raw.get("firstname", ""),
        lastname=raw.get("lastname", ""),
        phone=raw.get("custom_attributes", {}).get("phone") if isinstance(raw.get("custom_attributes"), dict) else None,
        created_at=_parse_dt(created),
        city=raw.get("extension_attributes", {}).get("city") if isinstance(raw.get("extension_attributes"), dict) else None,
        store_view=raw.get("store_id") and str(raw["store_id"]) or "default",
    )


def parse_order(raw: dict) -> OrderRecord | None:
    cid = raw.get("customer_id")
    if not cid or int(cid) == 0:          # guest order: staged, not journied
        return None
    items = raw.get("items", []) or []
    skus = [i.get("sku") for i in items if i.get("sku")]
    cats = sorted({(i.get("product_type") or "") for i in items if i.get("product_type")})
    return OrderRecord(
        magento_order_id=int(raw["entity_id"]),
        increment_id=str(raw.get("increment_id", "")),
        magento_customer_id=int(cid),
        placed_at=_parse_dt(raw.get("created_at") or "1970-01-01 00:00:00"),
        grand_total_minor=int(round(float(raw.get("grand_total", 0)) * 100)),
        discount_minor=int(round(abs(float(raw.get("discount_amount", 0))) * 100)),
        items_count=len(items) or int(raw.get("total_qty_ordered", 1) or 1),
        categories=cats,
        skus=skus,
        status=raw.get("status", "complete"),
    )


def _parse_dt(value: str) -> datetime:
    value = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:  # pragma: no cover
        dt = datetime.strptime(value.split(".")[0], "%Y-%m-%d %H:%M:%S")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# mock client — deterministic, so the demo and the tests agree
# --------------------------------------------------------------------------
CATEGORIES = ["Skincare", "Haircare", "Fragrance", "Makeup", "Audio", "Home & Living", "Fitness"]
FIRST = ["Ahmed", "Mona", "Karim", "Salma", "Omar", "Nour", "Youssef", "Aya", "Mostafa", "Dina",
         "Hassan", "Yasmin", "Ali", "Hala", "Tarek", "Marwa"]
LAST = ["El-Sayed", "Fouad", "Hassan", "Kamel", "Nabil", "Zaki", "Rashed", "El-Masry", "Amer", "Tawfik"]
CITIES = ["Cairo", "Giza", "Alexandria", "Mansoura", "New Cairo", "Tanta"]


class MockMagentoClient:
    """Generates a base with a realistic lifecycle mix so all five journeys fire."""

    def __init__(self, n_customers: int = 400, seed: int = 20261104, now: datetime | None = None):
        self.n = n_customers
        self.rng = random.Random(seed)
        self.now = now or datetime.now(timezone.utc)
        self._customers: list[CustomerRecord] = []
        self._orders: list[OrderRecord] = []
        self._build()

    def _build(self) -> None:
        rng = self.rng
        order_id = 500_000
        for i in range(self.n):
            cid = 100_000 + i
            registered = self.now - timedelta(days=rng.randint(5, 900))
            rec = CustomerRecord(
                magento_customer_id=cid,
                email=f"customer{cid}@example.com",
                firstname=rng.choice(FIRST),
                lastname=rng.choice(LAST),
                phone=f"+2010{rng.randint(10_000_000, 99_999_999)}",
                created_at=registered,
                city=rng.choice(CITIES),
                store_view="default",
                locale=rng.choice(["ar", "en"]),
                consent_email=rng.random() > 0.05,
                consent_push=rng.random() > 0.45,
                consent_sms=rng.random() > 0.55,
                consent_inapp=rng.random() > 0.2,
            )
            self._customers.append(rec)

            # Lifecycle mix: ~15% never bought, rest spread across recency bands
            roll = rng.random()
            if roll < 0.12:
                orders = 0
            elif roll < 0.30:
                orders = rng.randint(1, 2)
            elif roll < 0.65:
                orders = rng.randint(3, 9)
            else:
                orders = rng.randint(6, 20)

            if orders == 0:
                continue

            # recency: recent (active), mid (at risk), long (lost)
            band = rng.random()
            if band < 0.45:
                last_gap = rng.randint(1, 50)
            elif band < 0.72:
                last_gap = rng.randint(62, 170)
            else:
                last_gap = rng.randint(185, 400)
            last_gap = min(last_gap, max(2, (self.now - registered).days - 1))

            aov = rng.randint(45_000, 320_000)
            for k in range(orders):
                # earlier orders walked back from the last order date
                gap = last_gap + int(k * rng.uniform(12, 45))
                placed = self.now - timedelta(days=gap)
                if placed < registered:
                    break
                order_id += 1
                cat = rng.choice(CATEGORIES)
                disc = int(aov * rng.choice([0, 0, 0, 0.1, 0.15]))
                self._orders.append(OrderRecord(
                    magento_order_id=order_id,
                    increment_id=f"EG{order_id}",
                    magento_customer_id=cid,
                    placed_at=placed,
                    grand_total_minor=aov,
                    discount_minor=disc,
                    items_count=rng.randint(1, 4),
                    categories=[cat],
                    skus=[f"{cat[:3].upper()}-{rng.randint(1, 40):02d}"],
                ))

    def iter_customers(self, updated_since: datetime | None = None):
        for c in self._customers:
            if updated_since and c.created_at < updated_since:
                continue
            yield c

    def iter_orders(self, updated_since: datetime | None = None):
        for o in self._orders:
            if updated_since and o.placed_at < updated_since:
                continue
            yield o
