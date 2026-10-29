"""ProConnect token boundaries with real asymmetric signatures and synthetic identities."""

import time

from django.core.exceptions import SuspiciousOperation

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from lasuite.oidc_login.backends import OIDCAuthenticationBackend as SuiteBackend

from core.authentication.backends import OIDCAuthenticationBackend


@pytest.fixture(name="signed")
def signed_tokens(settings):
    """No discovery or real credentials are used by these tests."""
    key = generate_private_key(public_exponent=65537, key_size=2048)
    settings.ASSISTANT_RH_ENABLED = True
    settings.ASSISTANT_RH_OIDC_ENABLED = True
    settings.OIDC_RP_CLIENT_ID = "synthetic-client"
    settings.OIDC_OP_URL = "https://issuer.test"
    settings.OIDC_RP_SIGN_ALGO = "RS256"
    settings.OIDC_RP_IDP_SIGN_KEY = (
        key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    )
    now = int(time.time())
    payload = {
        "iss": settings.OIDC_OP_URL,
        "sub": "synthetic-subject",
        "aud": settings.OIDC_RP_CLIENT_ID,
        "iat": now,
        "exp": now + 60,
        "nonce": "synthetic-nonce",
    }

    def sign(**overrides):
        return jwt.encode({**payload, **overrides}, key, algorithm="RS256")

    return sign


def test_valid_proconnect_token_keeps_signature_and_nonce_checks(signed):
    backend = OIDCAuthenticationBackend()
    assert backend.verify_token(signed(), nonce="synthetic-nonce")["sub"] == "synthetic-subject"
    with pytest.raises(SuspiciousOperation, match="Nonce"):
        backend.verify_token(signed(), nonce="wrong-nonce")
    other_key = generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        jwt.decode(signed(), options={"verify_signature": False}), other_key, algorithm="RS256"
    )
    with pytest.raises(SuspiciousOperation, match="verification failed"):
        backend.verify_token(forged, nonce="synthetic-nonce")


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://another-issuer.test"},
        {"aud": "another-client"},
        {"aud": []},
        {"aud": None},
        {"sub": ""},
        {"sub": None},
        {"exp": 1},
        {"exp": True},
        {"iat": "tomorrow"},
        {"iat": 9999999999},
        {"nbf": 9999999999},
        {"nbf": True},
        {"azp": "another-client"},
        {"aud": ["synthetic-client", "other-client"]},
    ],
)
def test_proconnect_rejects_invalid_signed_claims(signed, overrides):
    with pytest.raises(SuspiciousOperation, match="Invalid OIDC token claims"):
        OIDCAuthenticationBackend().verify_token(signed(**overrides), nonce="synthetic-nonce")


def test_proconnect_accepts_explicit_authorized_party(signed):
    assert OIDCAuthenticationBackend().verify_token(
        signed(aud=["synthetic-client", "other-client"], azp="synthetic-client"),
        nonce="synthetic-nonce",
    )


def test_proconnect_userinfo_subject_must_match_id_token(signed, monkeypatch):
    backend = OIDCAuthenticationBackend()
    payload = backend.verify_token(signed(), nonce="synthetic-nonce")
    monkeypatch.setattr(SuiteBackend, "get_userinfo", lambda *args: {"sub": "another-subject"})
    with pytest.raises(SuspiciousOperation, match="subject mismatch"):
        backend.get_userinfo("synthetic-access-token", signed(), payload)
    monkeypatch.setattr(SuiteBackend, "get_userinfo", lambda *args: {"sub": payload["sub"]})
    assert (
        backend.get_userinfo("synthetic-access-token", signed(), payload)["sub"] == payload["sub"]
    )
