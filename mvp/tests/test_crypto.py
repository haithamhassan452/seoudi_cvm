"""Encryption and blind indexes — the security decision the user asked for."""
from app.crypto import PIIVault, normalise_email, normalise_phone


def test_roundtrip(vault):
    token = vault.encrypt("Mona Fouad")
    assert token and token.startswith("gAAAA")
    assert "Mona" not in token
    assert vault.decrypt(token) == "Mona Fouad"


def test_none_and_empty_are_safe(vault):
    assert vault.encrypt(None) is None
    assert vault.encrypt("") is None
    assert vault.decrypt(None) is None
    assert vault.decrypt("not-a-token") is None  # never raises


def test_ciphertext_is_non_deterministic(vault):
    # Fernet includes a random IV: the same value encrypts differently each time.
    a, b = vault.encrypt("same@example.com"), vault.encrypt("same@example.com")
    assert a != b
    assert vault.decrypt(a) == vault.decrypt(b)


def test_blind_index_is_deterministic_and_normalised(vault):
    i1 = vault.email_index("Mona@Example.com ")
    i2 = vault.email_index("mona@example.com")
    assert i1 == i2
    assert len(i1) == 64
    assert "mona" not in i1


def test_blind_index_does_not_leak_plaintext(vault):
    assert vault.email_index("secret@x.com") != "secret@x.com"


def test_phone_normalisation_local_to_e164():
    assert normalise_phone("01012345678") == "201012345678"
    assert normalise_phone("+20 101 234 5678") == "201012345678"
    assert normalise_phone("00201012345678") == "201012345678"


def test_email_normalisation():
    assert normalise_email("  A.User+tag@Example.COM ") == "a.user+tag@example.com"


def test_masking():
    v = PIIVault("x" * 0, "y") if False else None
    vault_like = __import__("app.crypto", fromlist=["PIIVault"]).PIIVault(
        "Z" * 43 + "=", "pepper")
    assert vault_like.mask_email("mona@example.com") == "m***@example.com"
    assert vault_like.mask_phone("+201012345678").endswith("8")
    assert "*" in vault_like.mask_phone("+201012345678")
