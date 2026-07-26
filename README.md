# swafra

Leiden-chunked, graph-linked semantic memory as an MCP server — for Claude, Codex, and any MCP-compatible AI.

## Install

```bash
pip install swafra
```

## Attach to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "swafra": {
      "command": "swafra"
    }
  }
}
```

Restart Claude Desktop — the tools appear automatically.

## Attach to Claude Code

```bash
claude mcp add swafra swafra
```

## Attach to VS Code (Copilot/Codex)

Add to `.vscode/mcp.json` in your project:

```json
{
  "servers": {
    "swafra": {
      "command": "swafra"
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `add_knowledge` | Ingest text → Leiden chunk → embed → build knowledge graph |
| `search_knowledge` | Semantic vector search over stored knowledge |
| `graph_walk` | Traverse chunk edges from a starting point |
| `get_context` | Combined search + graph walk (recommended for retrieval) |
| `list_sources` | List all ingested knowledge sources |
| `delete_source` | Remove a source and its graph |

## How It Works

1. **Leiden Chunking** — Text is split into sentences, a hybrid graph is built (semantic similarity + entity co-occurrence + positional proximity), and the Leiden algorithm finds topically coherent communities. Each community becomes one chunk.

2. **Local Embeddings** — Uses [fastembed](https://github.com/qdrant/fastembed) (ONNX, no cloud) with `BAAI/bge-small-en-v1.5` (384-dim). Falls back to a deterministic hash-based embedder if fastembed is unavailable.

3. **Knowledge Graph** — Chunks are connected with sequential edges (next/prev), similarity edges (cosine > 0.7), and community membership. Graph-walk retrieval traverses these edges for broader context.

4. **Storage** — JSON files in `~/.scimap/`. No database required.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SCIMAP_DATA_DIR` | `~/.scimap` | Where knowledge is stored |
| `SCIMAP_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model (fastembed) |

## Requirements

- Python >= 3.10
- Dependencies installed automatically: `fastembed`, `igraph`, `leidenalg`, `numpy`, `mcp`

## License

MIT — [github.com/kunal12203/swafra](https://github.com/kunal12203/swafra)
