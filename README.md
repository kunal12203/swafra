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

Both work independently — install one or both, no conflicts.

---

## Upgrade

```bash
pip install --upgrade swafra
```

Or:

```bash
npm update -g swafra
```

---

## Quick start

```bash
# 1. Install
pip install swafra

# 2. Connect to Claude Code
claude mcp add swafra -- swafra serve

# 3. Install enforcement hooks (ensures Claude always uses memory)
swafra setup

# Done — Claude will now remember across sessions
```

---

## CLI

```bash
swafra
```

Shows your full knowledge graph dashboard — sources, chunks, edges, communities, entities, facts, storage size.

| Command | What it does |
|---------|-------------|
| `swafra` | Show knowledge graph stats dashboard |
| `swafra stats` | Same as above |
| `swafra serve` | Start the MCP server |
| `swafra setup` | Install enforcement hooks for Claude Code |
| `swafra remove` | Disable hooks (keeps all data) |
| `swafra remove global` | Remove hooks + delete all stored knowledge |
| `swafra help` | Show usage |

---

## Connect to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "swafra": {
      "command": "swafra",
      "args": ["serve"]
    }
  }
}
```

Restart Claude Desktop — the tools appear automatically.

---

## Connect to Claude Code

```bash
claude mcp add swafra -- swafra serve
```

Then install enforcement hooks so Claude always retrieves memory:

```bash
swafra setup
```

---

## Connect to VS Code (Copilot)

Add to `.vscode/mcp.json` in your project:

```json
{
  "servers": {
    "swafra": {
      "command": "swafra",
      "args": ["serve"]
    }
  }
}
```

---

## Enforcement hooks

Running `swafra setup` installs Claude Code hooks that ensure Claude:

- **Always calls `get_context`** at the start of every session (Stop hook wakes Claude back up if it forgets)
- **Proactively stores knowledge** without waiting to be asked
- **Never says "I don't have context"** without checking memory first

Three layers of enforcement:

| Layer | Mechanism | Reliability |
|-------|-----------|-------------|
| Tool descriptions | "MANDATORY: call before first response" | High — visible every turn |
| CLAUDE.md | Rules injected into `~/.claude/CLAUDE.md` | Medium — system prompt |
| Stop hook | Wakes Claude back up if it skips memory | Guaranteed |

To disable: `swafra remove`

---

## What you can do

Once connected, Claude remembers and retrieves automatically:

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
Text is split into semantically coherent chunks using Leiden community detection — a graph algorithm that groups sentences by topic. Falls back to conversation-aware chunking if Leiden deps aren't available.

**2. Local embeddings**
Uses [fastembed](https://github.com/qdrant/fastembed) (ONNX, CPU-only, no API key) with `BAAI/bge-small-en-v1.5`. Falls back to deterministic hash vectors if fastembed isn't installed.

**3. Hybrid retrieval**
4-signal fused scoring: BM25 + vector cosine + entity/date overlap + character n-gram. Returns the best chunk per source for diverse, non-redundant context.

**4. Knowledge graph**
Chunks are connected with sequential (next/prev), similarity, entity co-occurrence, and cross-session edges. Graph walk expands retrieval beyond what search alone finds.

**5. Fact lifecycle**
Structured facts are extracted from chunks. When a new fact conflicts with an old one (e.g. "favorite editor" changes), the old fact is superseded — stale chunks get penalized in search.

**6. Storage**
JSON files in `~/.scimap/`. No database, no server, no cloud. Everything runs locally.

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

## Platform support

| Feature | macOS | Linux | Windows |
|---------|-------|-------|---------|
| Stats dashboard | ✓ | ✓ | ✓ |
| MCP server | ✓ | ✓ | ✓ |
| Enforcement hooks | ✓ | ✓ | WSL / Git Bash |

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SCIMAP_DATA_DIR` | `~/.scimap` | Where knowledge is stored |
| `SCIMAP_EMBED_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model |

---

## Uninstall

```bash
# Remove hooks only (keep data)
swafra remove

# Remove everything (hooks + data)
swafra remove global

# Remove MCP registration
claude mcp remove swafra

# Uninstall package
pip uninstall swafra
# or
npm uninstall -g swafra
```

---

## License

MIT — [github.com/kunal12203/swafra](https://github.com/kunal12203/swafra)
