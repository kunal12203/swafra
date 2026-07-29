"""Trust half of cloud auth — every request re-proves its bearer credential.

Two credential types, one identity:
  - JWT (Cognito access token): verified against the issuer's JWKS on every
    request (signature, iss, exp, token_use, client_id). Tenant/workspace are
    read from verified claims — never from tool arguments.
  - API key (``sk_`` prefix): SHA-256 hash lookup in the key registry.

Both produce a SwafraAccessToken, so tools see exactly one identity type.
The login half (Cognito Managed Login / OAuth 2.1 + PKCE) happens in the
browser between the MCP client and Cognito; this server never sees passwords.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import threading
import time
from pathlib import Path

import anyio.to_thread
import jwt as pyjwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier

from cloud.config import CloudConfig

log = logging.getLogger("swafra.cloud.auth")

API_KEY_PREFIX = "sk_"


class SwafraAccessToken(AccessToken):
    """AccessToken enriched with verified tenancy — the only identity tools see."""

    user_id: str
    tenant_id: str
    workspace_id: str
    auth_method: str  # "jwt" | "api_key"


# ---------------------------------------------------------------------------
# JWT / JWKS (Cognito-shaped, works with any RS256 OIDC issuer)
# ---------------------------------------------------------------------------
class JwtVerifier(TokenVerifier):
    def __init__(self, config: CloudConfig):
        self._cfg = config
        # PyJWKClient caches keys and handles kid rotation.
        self._jwks = PyJWKClient(config.jwks_url, cache_keys=True)

    def _verify_sync(self, token: str) -> SwafraAccessToken | None:
        cfg = self._cfg
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = pyjwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=cfg.jwt_issuer,
                audience=cfg.jwt_audience or None,
                options={
                    "require": ["exp", "iss", "sub"],
                    "verify_aud": bool(cfg.jwt_audience),
                },
            )
        except pyjwt.PyJWTError as e:
            log.info("JWT rejected: %s", e)
            return None

        # Cognito issues both id and access tokens; only access tokens grant API access.
        token_use = claims.get("token_use")
        if token_use is not None and token_use != "access":
            log.info("JWT rejected: token_use=%s", token_use)
            return None

        client_id = claims.get("client_id") or claims.get("aud") or "unknown"
        if cfg.allowed_client_ids and client_id not in cfg.allowed_client_ids:
            log.info("JWT rejected: client_id %s not allowed", client_id)
            return None

        sub = claims["sub"]
        return SwafraAccessToken(
            token=token,
            client_id=str(client_id),
            scopes=claims.get("scope", "").split(),
            expires_at=claims.get("exp"),
            resource=cfg.public_url,
            user_id=sub,
            # No tenant claim (e.g. no PreToken lambda yet) → personal tenant.
            tenant_id=str(claims.get(cfg.tenant_claim) or sub),
            workspace_id=str(claims.get(cfg.workspace_claim) or "default"),
            auth_method="jwt",
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        # JWKS fetch on cold cache is blocking I/O — keep it off the event loop.
        return await anyio.to_thread.run_sync(self._verify_sync, token)


# ---------------------------------------------------------------------------
# API keys — hashed at rest, same AuthContext as JWT
# ---------------------------------------------------------------------------
class ApiKeyRegistry(TokenVerifier):
    """SQLite-backed key registry. Plaintext is shown once at mint time;
    only the SHA-256 hash is stored."""

    def __init__(self, db_path: Path | str):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    key_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    scopes TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    revoked_at REAL
                )
            """)
            self._conn.commit()

    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def mint(self, tenant_id: str, workspace_id: str = "default",
             user_id: str | None = None, scopes: str = "") -> str:
        key = API_KEY_PREFIX + secrets.token_hex(24)
        with self._lock:
            self._conn.execute(
                "INSERT INTO api_keys (key_hash, user_id, tenant_id, workspace_id, scopes, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (self._hash(key), user_id or f"key:{tenant_id}", tenant_id,
                 workspace_id, scopes, time.time()))
            self._conn.commit()
        return key

    def revoke(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE key_hash = ? AND revoked_at IS NULL",
                (time.time(), self._hash(key)))
            self._conn.commit()
            return cur.rowcount > 0

    def lookup(self, key: str) -> SwafraAccessToken | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
                (self._hash(key),)).fetchone()
        if row is None:
            return None
        return SwafraAccessToken(
            token=key,
            client_id=row["user_id"],
            scopes=row["scopes"].split() if row["scopes"] else [],
            expires_at=None,
            resource=None,
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            auth_method="api_key",
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token.startswith(API_KEY_PREFIX):
            return None
        return self.lookup(token)


# ---------------------------------------------------------------------------
# Composite — one entry point for the MCP auth middleware
# ---------------------------------------------------------------------------
class CompositeVerifier(TokenVerifier):
    """Dispatch on credential shape: ``sk_…`` → key registry, else JWT."""

    def __init__(self, config: CloudConfig):
        self._api_keys = ApiKeyRegistry(config.data_dir / "api-keys.db")
        self._jwt = JwtVerifier(config) if config.jwt_enabled else None

    @property
    def api_keys(self) -> ApiKeyRegistry:
        return self._api_keys

    async def verify_token(self, token: str) -> AccessToken | None:
        if token.startswith(API_KEY_PREFIX):
            return await self._api_keys.verify_token(token)
        if self._jwt is not None:
            return await self._jwt.verify_token(token)
        log.info("Bearer token rejected: JWT verification disabled and not an API key")
        return None
