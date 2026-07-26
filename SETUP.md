# scimap MCP Server Setup

## Prerequisites

1. **Node.js >= 18**
2. **Python >= 3.12** with:
   ```bash
   pip install fastembed igraph leidenalg numpy
   ```

## Install

```bash
cd packages/mcp
pnpm install
pnpm build
```

## Configure for Claude AI (Desktop)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "scimap": {
      "command": "node",
      "args": ["/absolute/path/to/scimap/packages/mcp/dist/index.js"]
    }
  }
}
```

## Configure for Claude Code

Add to `.claude/settings.json`:

```json
{
  "mcpServers": {
    "scimap": {
      "command": "node",
      "args": ["/absolute/path/to/scimap/packages/mcp/dist/index.js"]
    }
  }
}
```

## Configure for ChatGPT (via MCP bridge)

ChatGPT doesn't natively support MCP yet. Use an MCP-to-OpenAI bridge:

```bash
# Using mcp-openai-bridge or similar
npx mcp-to-openai --server "node /path/to/scimap/packages/mcp/dist/index.js"
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SCIMAP_DATA_DIR` | `~/.scimap` | Where knowledge is stored |
| `SCIMAP_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model (fastembed) |
| `SCIMAP_EMBED_BACKEND` | auto-detect | Force `fastembed` or `local` |
| `SCIMAP_PYTHON` | `python3` | Python binary path |

## Tools Exposed

| Tool | Description |
|------|-------------|
| `add_knowledge` | Ingest text → Leiden chunk → embed → build knowledge graph |
| `search_knowledge` | Semantic vector search over stored knowledge |
| `graph_walk` | Traverse chunk edges from a starting point |
| `get_context` | Combined search + graph walk (recommended for retrieval) |
| `list_sources` | List all ingested knowledge sources |
| `delete_source` | Remove a source and its graph |

## How It Works

1. **Leiden Chunking**: Text is split into sentences, entities are extracted, a hybrid graph is built (semantic similarity + entity co-occurrence + positional proximity), and the Leiden algorithm finds topically coherent communities. Each community = one chunk.

2. **Local Embeddings**: fastembed (ONNX, no cloud) embeds chunks with BAAI/bge-small-en-v1.5 (384-dim). Falls back to a deterministic hash-based embedder if fastembed isn't installed.

3. **Knowledge Graph**: Chunks are connected with sequential edges (next/prev), similarity edges (cosine > 0.7), and share community membership. Graph-walk retrieval traverses these edges for context.

4. **Storage**: Simple JSON files in `~/.scimap/`. No database required.
