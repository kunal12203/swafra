"""JWT trust-half policy tests — signed with a real RSA key, JWKS stubbed.

These pin the verifier's security decisions: expiry, issuer, token_use,
client allow-list, tenant/workspace claim extraction, personal-tenant
fallback. PyJWKClient itself (key fetch/rotation) is not under test.
"""
from __future__ import annotations

import time

import pytest

cryptography = pytest.importorskip("cryptography")
import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa

from cloud.auth import JwtVerifier
from cloud.config import CloudConfig

ISSUER = "https://cognito-idp.eu-central-1.amazonaws.com/eu-central-1_TEST"

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_key = _private_key.public_key()


class _StubSigningKey:
    key = _public_key


class _StubJwks:
    def get_signing_key_from_jwt(self, token):
        return _StubSigningKey()


def make_verifier(**overrides) -> JwtVerifier:
    cfg = CloudConfig(
        data_dir="/tmp/swafra-test-unused",
        jwt_issuer=ISSUER,
        **overrides,
    )
    verifier = JwtVerifier.__new__(JwtVerifier)
    verifier._cfg = cfg
    verifier._jwks = _StubJwks()
    return verifier


def make_token(**claim_overrides) -> str:
    claims = {
        "sub": "user-123",
        "iss": ISSUER,
        "exp": int(time.time()) + 3600,
        "token_use": "access",
        "client_id": "client-abc",
        "scope": "swafra/read swafra/write",
    }
    claims.update(claim_overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return pyjwt.encode(claims, _private_key, algorithm="RS256")


def test_valid_access_token_maps_identity():
    token = make_verifier()._verify_sync(make_token(
        **{"custom:tenantId": "acme", "custom:workspaceId": "research"}))
    assert token is not None
    assert token.user_id == "user-123"
    assert token.tenant_id == "acme"
    assert token.workspace_id == "research"
    assert token.auth_method == "jwt"
    assert token.scopes == ["swafra/read", "swafra/write"]


def test_missing_tenant_claim_falls_back_to_personal_tenant():
    token = make_verifier()._verify_sync(make_token())
    assert token is not None
    assert token.tenant_id == "user-123"  # sub → personal tenant
    assert token.workspace_id == "default"


def test_rejects_expired_token():
    assert make_verifier()._verify_sync(
        make_token(exp=int(time.time()) - 60)) is None


def test_rejects_wrong_issuer():
    assert make_verifier()._verify_sync(
        make_token(iss="https://evil.example.com")) is None


def test_rejects_id_token():
    # Cognito id tokens must not grant API access — only access tokens.
    assert make_verifier()._verify_sync(
        make_token(token_use="id")) is None


def test_rejects_disallowed_client():
    verifier = make_verifier(allowed_client_ids=["expected-client"])
    assert verifier._verify_sync(make_token(client_id="other-client")) is None
    assert verifier._verify_sync(make_token(client_id="expected-client")) is not None


def test_rejects_garbage_and_unsigned_tokens():
    verifier = make_verifier()
    assert verifier._verify_sync("not-a-jwt") is None
    unsigned = pyjwt.encode({"sub": "x", "iss": ISSUER,
                             "exp": int(time.time()) + 3600},
                            key=None, algorithm="none")
    assert verifier._verify_sync(unsigned) is None
