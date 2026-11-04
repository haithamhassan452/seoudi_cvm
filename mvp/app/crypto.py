"""PII vault: encryption + blind indexes.

Design
------
* Every PII field is encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before it
  touches the database. If the database leaks, names, emails and phones do not.
* A *blind index* is a keyed HMAC of the normalised value. It is deterministic,
  so you can look a customer up by email without storing the email in a way
  anyone can read or rainbow-table.
* `key_version` is stored per row so keys can be rotated without a flag day:
  decrypt with the version on the row, re-encrypt with the current key.

The customer is never identified by phone. The only external identifier is the
Magento customer id; phone and email are attributes, encrypted, at most usable
as a blind index for de-duplication.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

_EMAIL_RE = re.compile(r"[^a-z0-9@._+-]")
_PHONE_RE = re.compile(r"[^0-9]")


def normalise_email(value: str) -> str:
    v = (value or "").strip().lower()
    # gmail-style dot/plus normalisation is a policy decision; keep it simple
    # and documented rather than surprising: trim + lowercase only.
    return _EMAIL_RE.sub("", v)


def normalise_phone(value: str) -> str:
    digits = _PHONE_RE.sub("", value or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0"):          # Egyptian local format 01xxxxxxxxx
        digits = "20" + digits[1:]
    return digits


class PIIVault:
    def __init__(self, key: str, blind_index_key: str, key_version: int = 1) -> None:
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        self._blind_key = blind_index_key.encode()
        self.key_version = key_version

    # ---------- encryption ----------
    def encrypt(self, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return None
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            # Wrong key or corrupted row: never crash a page load over one row.
            return None

    # ---------- blind indexes ----------
    def blind_index(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        return hmac.new(self._blind_key, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def email_index(self, email: Optional[str]) -> Optional[str]:
        return self.blind_index(normalise_email(email)) if email else None

    def phone_index(self, phone: Optional[str]) -> Optional[str]:
        return self.blind_index(normalise_phone(phone)) if phone else None

    def mask_email(self, email: Optional[str]) -> Optional[str]:
        """Return a display-safe form: a***@domain.com — for UI lists."""
        if not email or "@" not in email:
            return None
        local, domain = email.split("@", 1)
        return f"{local[:1]}{'*' * max(1, len(local) - 1)}@{domain}"

    def mask_phone(self, phone: Optional[str]) -> Optional[str]:
        digits = normalise_phone(phone)
        if len(digits) < 6:
            return None
        return f"+{digits[:-6]}{'*' * 5}{digits[-1]}"
