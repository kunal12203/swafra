"""swafra cloud MCP server — Streamable HTTP, stateless, bearer-authenticated.

Request path:
  Bearer credential → CompositeVerifier (JWKS or API-key hash)
  → SwafraAccessToken {user, tenant, workspace}
  → per-workspace store bound via engine.store.use_store()
  → engine tools; heavy ingest goes through the job queue (ADR-0004).

The local stdio server (swafra/server.py) is untouched.
Run: python -m cloud.server   (config via SWAFRA_CLOUD_* env vars)
"""
from __future__ import annotations

import logging

import anyio.to_thread
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from engine.store import use_store

from cloud.auth import CompositeVerifier, SwafraAccessToken
from cloud.config import get_config
from cloud.jobs import JobQueue
from cloud.tenancy import StoreRegistry

log = logging.getLogger("swafra.cloud")


def _identity() -> SwafraAccessToken:
    """The verified caller. Auth middleware rejects unauthenticated requests
    before tools run, so a missing/foreign token here is a server bug."""
    token = get_access_token()
    if not isinstance(token, SwafraAccessToken):
        raise RuntimeError("no verified swafra identity on this request")
    return token


def build_server() -> FastMCP:
    config = get_config()
    if config.jwt_enabled and not (config.allowed_client_ids or config.jwt_audience):
        log.warning(
            "JWT enabled with no allowed_client_ids/audience: ANY valid access "
            "token issued by %s will be accepted. Set "
            "SWAFRA_CLOUD_ALLOWED_CLIENT_IDS before production.", config.jwt_issuer)
    verifier = CompositeVerifier(config)
    registry = StoreRegistry(config)
    queue = JobQueue(config, registry)
    if config.ingest_workers > 0:
        # In-process workers (single-node). For fleets set SWAFRA_CLOUD_
        # DATABASE_URL, run edges with INGEST_WORKERS=0, and scale
        # `python -m cloud.worker` processes against the shared queue.
        queue.start_workers(config.ingest_workers)

    async def run_in_workspace(fn, *args):
        """Run blocking engine work off the event loop, bound to the caller's
        workspace store. anyio copies contextvars into the worker thread, so
        the use_store binding stays request-scoped."""
        store = registry.get(_identity())

        def work():
            with use_store(store):
                return fn(*args)

        return await anyio.to_thread.run_sync(work)

    # NOTE: FastMCP's `lifespan` is per *session*, and stateless HTTP creates a
    # transient session per request — never tie process-wide resources (queue,
    # store registry) to it. They live for the process; SQLite WAL makes
    # abrupt exit safe, and pending jobs are requeued on next start.
    mcp = FastMCP(
        "swafra-cloud",
        instructions="swafra semantic memory (cloud). Ingest is asynchronous: "
                     "add_knowledge returns a job id; poll get_ingest_status "
                     "until status is 'ready'.",
        token_verifier=verifier,
        auth=AuthSettings(
            # When JWT is disabled (API-key-only dev mode) we still must
            # publish RFC 9728 metadata; point issuer at ourselves.
            issuer_url=config.jwt_issuer or config.public_url,
            resource_server_url=config.public_url,
            required_scopes=config.required_scopes or None,
        ),
        host=config.host,
        port=config.port,
        stateless_http=True,
        json_response=True,
    )

    # -- tools ---------------------------------------------------------------

    @mcp.tool()
    async def add_knowledge(text: str, title: str = "untitled") -> dict:
        """Store text in persistent memory. Asynchronous: returns a job id
        immediately; chunks become searchable when the job status is 'ready'.
        Idempotent — resubmitting identical content returns the same job.
        May be rejected when tenant quotas are exceeded."""
        return queue.submit(_identity(), title, text)

    @mcp.tool()
    async def get_ingest_status(job_id: str) -> dict:
        """Status of an ingest job: pending, processing, ready, or failed."""
        job = queue.get(_identity(), job_id)
        return job if job is not None else {"job_id": job_id, "status": "not_found"}

    @mcp.tool()
    async def search_knowledge(query: str, k: int = 8) -> list[dict]:
        """Semantic search over this workspace's stored knowledge."""
        from engine.graph import search_knowledge as _search
        return await run_in_workspace(_search, query, k)

    @mcp.tool()
    async def get_context(query: str, k: int = 5, hops: int = 1) -> list[dict]:
        """Retrieve memory relevant to a topic (search + graph walk).
        Call before answering anything about prior work or preferences."""
        from engine.graph import get_context as _get_context
        return await run_in_workspace(_get_context, query, k, hops)

    @mcp.tool()
    async def graph_walk(chunk_id: str, hops: int = 2, k: int = 10) -> list[dict]:
        """Traverse the knowledge graph from a chunk in this workspace."""
        from engine.graph import graph_walk as _walk
        return await run_in_workspace(_walk, chunk_id, hops, k)

    @mcp.tool()
    async def list_sources() -> list[dict]:
        """List knowledge sources in this workspace."""
        from engine.graph import list_sources as _list
        return await run_in_workspace(_list)

    @mcp.tool()
    async def delete_source(source_id: str) -> dict:
        """Delete a source and its chunks/edges from this workspace."""
        from engine.graph import delete_source as _delete
        return await run_in_workspace(_delete, source_id)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request):
        """Public liveness probe for load balancers / Lightsail (no auth)."""
        from starlette.responses import JSONResponse
        return JSONResponse({"ok": True, "service": "swafra-cloud"})

    # Exposed for ops tooling (key minting) without re-opening the registry db.
    mcp._swafra_verifier = verifier  # type: ignore[attr-defined]
    return mcp


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    config = get_config()
    log.info("swafra cloud edge on %s:%s (jwt=%s, data=%s)",
             config.host, config.port,
             "on" if config.jwt_enabled else "off (api keys only)",
             config.data_dir)
    build_server().run(transport="streamable-http")


if __name__ == "__main__":
    main()
