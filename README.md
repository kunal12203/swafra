# swafra

Leiden-chunked, graph-linked semantic memory as an MCP server — for Claude, Copilot, and any MCP-compatible AI.

**94.7% recall_all@10 on LongMemEval** — the standard benchmark for long-term memory in AI assistants. [See benchmark →](BENCHMARK.md)

---

## Install

**Python (recommended):**
```bash
pip install swafra
```

**Node.js:**
```bash
npm install -g swafra
```

---

## Attach to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

**Python:**
```json
{
  "mcpServers": {
    "swafra": {
      "command": "swafra"
    }
  }
}
```

**Node.js:**
```json
{
  "mcpServers": {
    "swafra": {
      "command": "node",
      "args": ["/usr/local/lib/node_modules/swafra/dist/index.js"]
    }
  }
}
```

Restart Claude Desktop — the tools appear automatically.

---

## Attach to Claude Code

```bash
claude mcp add swafra swafra
```

---

## Attach to VS Code (Copilot)

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "swafra": {
      "command": "swafra"
    }
  }
}
```

---

## Tools

| Tool | Description |
|------|-------------|
| `add_knowledge` | Ingest text → Leiden chunk → embed → build knowledge graph |
| `search_knowledge` | Hybrid search (BM25 + vector + entity + n-gram) |
| `graph_walk` | Traverse chunk edges from a starting point |
| `get_context` | Combined search + graph walk (recommended for retrieval) |
| `list_sources` | List all ingested knowledge sources |
| `delete_source` | Remove a source and its graph |

---

## Optional: Better embeddings + Leiden chunking

Base install uses deterministic hash vectors (no dependencies, works everywhere).
For full semantic embeddings and Leiden chunking:

```bash
pip install "swafra[full]"
```

This installs `fastembed`, `numpy`, `igraph`, `leidenalg`.

> Leiden chunking requires Python 3.10–3.12 (igraph not yet available on 3.13+).
> Without it, swafra uses conversation-aware chunking — still achieves 94.7% on LongMemEval.

---

## How It Works

1. **Leiden Chunking** — Text is split into sentences, a hybrid graph is built (semantic similarity + entity co-occurrence + positional proximity), and the Leiden algorithm finds topically coherent communities. Each community becomes one chunk.

2. **Local Embeddings** — Uses [fastembed](https://github.com/qdrant/fastembed) (ONNX, no cloud) with `BAAI/bge-small-en-v1.5` (384-dim). Falls back to a deterministic hash-based embedder if fastembed is unavailable.

3. **4-Signal Hybrid Retrieval** — BM25 (0.40) + vector cosine (0.15) + entity/date overlap (0.25) + character n-gram (0.20). Source-diverse: returns best chunk per session.

4. **Storage** — JSON files in `~/.scimap/`. No database required.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SCIMAP_DATA_DIR` | `~/.scimap` | Where knowledge is stored |
| `SCIMAP_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model (fastembed) |

---

## License

MIT — [github.com/kunal12203/swafra](https://github.com/kunal12203/swafra)
