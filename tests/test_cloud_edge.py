"""Cloud edge integration — real Streamable HTTP server, real MCP client.

Proves over the wire, not by code reading:
  - unauthenticated / bad credentials → HTTP 401
  - API key → add_knowledge returns a job; job reaches 'ready'; search hits
  - two tenants with different keys cannot see each other's memories
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("fastembed")
pytest.importorskip("mcp")

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

REPO_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def cloud_server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("cloud-data")
    port = _free_port()
    env = dict(
        os.environ,
        SWAFRA_CLOUD_DATA_DIR=str(data_dir),
        SWAFRA_CLOUD_HOST="127.0.0.1",
        SWAFRA_CLOUD_PORT=str(port),
        SWAFRA_CLOUD_PUBLIC_URL=f"http://127.0.0.1:{port}",
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "cloud.server"],
        cwd=REPO_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server died:\n{proc.stdout.read()}")
        try:
            r = httpx.post(f"{base}/mcp", json={}, timeout=1.0)
            if r.status_code in (400, 401, 406):  # up and answering
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("server did not come up in 30s")

    def mint(tenant: str, workspace: str = "default") -> str:
        out = subprocess.run(
            [sys.executable, "-m", "cloud.keys", "mint",
             "--tenant", tenant, "--workspace", workspace],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    yield base, mint
    proc.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)
    proc.kill()


@contextlib.asynccontextmanager
async def mcp_session(base: str, api_key: str):
    headers = {"Authorization": f"Bearer {api_key}"}
    async with streamablehttp_client(f"{base}/mcp", headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call(session: ClientSession, tool: str, **args):
    """Call a tool, returning the plain Python payload regardless of whether
    the server used structured content ({"result": ...} wrapper) or JSON text."""
    result = await session.call_tool(tool, args)
    assert not result.isError, result.content
    sc = result.structuredContent
    if sc is not None:
        return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
    # v1 FastMCP may emit one text block per list item
    texts = [c.text for c in result.content if c.type == "text"]
    if not texts:
        return None
    if len(texts) == 1:
        return json.loads(texts[0])
    return [json.loads(t) for t in texts]


def test_rejects_bad_credentials(cloud_server):
    base, _ = cloud_server
    for headers in ({}, {"Authorization": "Bearer sk_forged"},
                    {"Authorization": "Bearer not.a.jwt"}):
        r = httpx.post(f"{base}/mcp", json={"jsonrpc": "2.0", "id": 1,
                                            "method": "tools/list"},
                       headers=headers, timeout=5.0)
        assert r.status_code == 401, (headers, r.status_code, r.text)


def test_ingest_job_lifecycle_and_search(cloud_server):
    base, mint = cloud_server
    key = mint("acme")

    async def scenario():
        async with mcp_session(base, key) as session:
            job = await call(session, "add_knowledge",
                             text="Our production database is Postgres 16 with pgvector. "
                                  "The primary region is eu-central-1.",
                             title="infra-notes")
            assert job["status"] in ("pending", "processing")

            # Idempotency: identical content → same job, no double ingest.
            dup = await call(session, "add_knowledge",
                             text="Our production database is Postgres 16 with pgvector. "
                                  "The primary region is eu-central-1.",
                             title="infra-notes")
            assert dup["deduplicated"] is True
            assert dup["job_id"] == job["job_id"]

            deadline = time.time() + 60
            status = None
            while time.time() < deadline:
                status = await call(session, "get_ingest_status", job_id=job["job_id"])
                if status["status"] in ("ready", "failed"):
                    break
                await asyncio.sleep(0.5)
            assert status and status["status"] == "ready", status
            assert status["result"]["chunks"] >= 1

            hits = await call(session, "search_knowledge",
                              query="which database do we run in production?", k=3)
            texts = " ".join(h["content"] for h in hits)
            assert "Postgres" in texts

    asyncio.run(scenario())


def test_tenant_isolation_over_http(cloud_server):
    base, mint = cloud_server
    key_a, key_b = mint("tenant-a"), mint("tenant-b")

    async def scenario():
        async with mcp_session(base, key_a) as session_a:
            job = await call(session_a, "add_knowledge",
                             text="tenant-a internal: the rollout password hint is kept offline.",
                             title="tenant-a-secret")
            deadline = time.time() + 60
            while time.time() < deadline:
                status = await call(session_a, "get_ingest_status", job_id=job["job_id"])
                if status["status"] == "ready":
                    break
                await asyncio.sleep(0.5)
            assert status["status"] == "ready"

            # Tenant B: same server, different key — must see nothing,
            # including the other tenant's job ids.
            async with mcp_session(base, key_b) as session_b:
                sources = await call(session_b, "list_sources")
                assert sources == []
                hits = await call(session_b, "search_knowledge",
                                  query="rollout password hint", k=5)
                assert hits == []
                foreign = await call(session_b, "get_ingest_status",
                                     job_id=job["job_id"])
                assert foreign["status"] == "not_found"

    asyncio.run(scenario())
