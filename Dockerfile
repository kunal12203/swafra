# swafra cloud edge — Streamable HTTP MCP + Cognito JWT + Postgres
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SWAFRA_CLOUD_HOST=0.0.0.0 \
    SWAFRA_CLOUD_PORT=8788 \
    SWAFRA_CLOUD_DATA_DIR=/var/lib/swafra-cloud \
    HF_HOME=/var/cache/fastembed \
    FASTEMBED_CACHE_PATH=/var/cache/fastembed

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin swafra \
    && mkdir -p /var/lib/swafra-cloud /var/cache/fastembed \
    && chown -R swafra:swafra /var/lib/swafra-cloud /var/cache/fastembed

COPY engine/requirements.txt /tmp/engine-requirements.txt
COPY cloud/requirements.txt /tmp/cloud-requirements.txt
RUN pip install --upgrade pip \
 && pip install -r /tmp/engine-requirements.txt -r /tmp/cloud-requirements.txt \
 && python -c "from mcp.server.fastmcp import FastMCP; print('mcp_ok', FastMCP)"

COPY engine /app/engine
COPY cloud /app/cloud

# Bake the default embedding model so cold start does not need HuggingFace.
USER swafra
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

EXPOSE 8788
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8788/health || exit 1

CMD ["python", "-m", "cloud.server"]
