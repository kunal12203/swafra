# swafra

Semantic memory for AI — ingest anything, retrieve what matters.

**94.7% recall_all@10 on [LongMemEval](BENCHMARK.md)** — the standard benchmark for long-term memory in AI assistants.

Works as an MCP server with Claude Desktop, Claude Code, VS Code Copilot, and any MCP-compatible AI.

---

## Install

```bash
pip install swafra
```

Or with Node.js:

```bash
npm install -g swafra
```

---

## Connect to Claude Desktop

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

---

## Connect to Claude Code

```bash
claude mcp add swafra swafra
```

---

## Connect to VS Code (Copilot)

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

---

## What you can do

Once connected, Claude can use swafra to remember and retrieve anything:

```
"Remember this meeting transcript: ..."
"What did we decide about the API design?"
"What are my editor preferences?"
"Forget everything from the project X sessions"
```

---

## Tools

| Tool | What it does |
|------|-------------|
| `add_knowledge` | Store text — chunked, embedded, and graph-linked |
| `search_knowledge` | Find relevant chunks by natural language query |
| `get_context` | Search + graph walk combined (recommended) |
| `graph_walk` | Explore connected chunks from a starting point |
| `list_sources` | See everything stored |
| `delete_source` | Remove a source and all its data |

---

## How it works

**1. Chunking**
Text is split into semantically coherent chunks using Leiden community detection — a graph algorithm that groups sentences by topic. Falls back to conversation-aware chunking if Leiden deps aren't available (Python 3.13+).

**2. Local embeddings**
Uses [fastembed](https://github.com/qdrant/fastembed) (ONNX, CPU-only, no API key) with `BAAI/bge-small-en-v1.5`. Falls back to deterministic hash vectors if fastembed isn't installed.

**3. Hybrid retrieval**
4-signal fused scoring: BM25 + vector cosine + entity/date overlap + character n-gram. Returns the best chunk per source so you get diverse, non-redundant context.

**4. Knowledge graph**
Chunks are connected with sequential (next/prev), similarity, and entity co-occurrence edges. Graph walk expands retrieval beyond what search alone finds.

**5. Storage**
JSON files in `~/.scimap/`. No database, no server, no cloud.

---

## Benchmark

94.7% recall_all@10 on LongMemEval-S — 500 questions across 6 categories, 53 sessions each.

| Category | recall_all@10 |
|----------|--------------|
| knowledge-update | 100.0% |
| single-session-user | 100.0% |
| single-session-preference | 100.0% |
| single-session-assistant | 100.0% |
| temporal-reasoning | 99.2% |
| multi-session | 93.3% |

[Full benchmark details and reproduction steps →](BENCHMARK.md)

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SCIMAP_DATA_DIR` | `~/.scimap` | Where knowledge is stored |
| `SCIMAP_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model |

---

## License

MIT — [github.com/kunal12203/swafra](https://github.com/kunal12203/swafra)
