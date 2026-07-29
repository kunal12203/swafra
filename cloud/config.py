"""Cloud edge configuration — all knobs from environment, SWAFRA_CLOUD_* prefix."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class CloudConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SWAFRA_CLOUD_")

    # Where tenant/workspace SQLite files, the API key registry, and the
    # ingest job queue live. One directory per (tenant, workspace).
    data_dir: Path = Path.home() / ".swafra-cloud"

    # Public URL of this MCP server — used as the OAuth resource identifier
    # (RFC 9728 protected resource metadata) and for RFC 8707 binding.
    public_url: str = "http://localhost:8788"
    host: str = "0.0.0.0"
    port: int = 8788

    # Trust half (JWT/JWKS). For Cognito the issuer is
    # https://cognito-idp.<region>.amazonaws.com/<user_pool_id> and the JWKS
    # URL is derived as <issuer>/.well-known/jwks.json. Leave issuer empty to
    # run API-key-only (dev / machine-to-machine).
    jwt_issuer: str = ""
    jwt_audience: str = ""  # optional; Cognito access tokens use client_id instead
    allowed_client_ids: list[str] = []
    tenant_claim: str = "custom:tenantId"
    workspace_claim: str = "custom:workspaceId"
    required_scopes: list[str] = []

    # Shared Postgres (pool model, ADR-0002). Empty → SQLite-per-workspace files.
    # The app role in this DSN must be a non-owner, non-superuser role without
    # BYPASSRLS — RLS is the isolation guarantee (see cloud/schema.sql).
    database_url: str = ""

    # Ingest jobs (ADR-0004): pending → processing → ready | failed.
    # Workers claim via SKIP LOCKED on Postgres; lease expiry recovers crashes.
    ingest_workers: int = 2
    job_max_attempts: int = 3
    job_lease_seconds: float = 300.0
    job_poll_seconds: float = 0.5

    # Per-tenant fairness / abuse limits, enforced at submit time.
    quota_max_text_bytes: int = 1_000_000
    quota_max_active_jobs: int = 20

    @property
    def jwks_url(self) -> str:
        return f"{self.jwt_issuer.rstrip('/')}/.well-known/jwks.json"

    @property
    def jwt_enabled(self) -> bool:
        return bool(self.jwt_issuer)


@lru_cache(maxsize=1)
def get_config() -> CloudConfig:
    cfg = CloudConfig()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return cfg
