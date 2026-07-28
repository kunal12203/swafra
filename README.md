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

## Python SDK

```python
import swafra

# Store knowledge
swafra.add("I prefer dark mode and use VS Code", title="prefs")

# Search
results = swafra.search("editor")
# [{"chunk_id": "...", "content": "...", "source_title": "prefs", "score": 0.9, ...}]

# Get context (search + graph walk — recommended)
ctx = swafra.context("what editor do I prefer?")

# List everything stored
srcs = swafra.sources()

# Delete a source
swafra.delete(srcs[0]["id"])
```

Class-style API (same behavior):

```python
from swafra import Memory

m = Memory()
m.add("React is my frontend framework", title="tech")
m.search("frontend")
m.context("what framework do I use?")
m.sources()
```

---

## JS/TS SDK

```bash
npm install swafra
```

```ts
import { Memory } from 'swafra'

const m = new Memory()

await m.add("I use TypeScript daily", { title: "stack" })
const results = await m.search("TypeScript")
const ctx = await m.context("what language do I use?")
const srcs = await m.sources()
await m.delete(srcs[0].id)

m.close() // shuts down the Python subprocess
```

Full TypeScript types are included — `AddResult`, `Chunk`, `Source`, `DeleteResult`.

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
| `swafra skill` | Install as Claude Code skill (no MCP needed) |
| `swafra config` | Configure LLM for enhanced extraction |
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

## LLM-enhanced extraction (optional)

By default, swafra uses regex for entity extraction. Configure an LLM for significantly better results:

```bash
# Anthropic (uses Haiku 4.5 — fast and cheap)
swafra config --provider anthropic --key sk-ant-...

# OpenAI (uses gpt-4o-mini)
swafra config --provider openai --key sk-...

# Any OpenAI-compatible endpoint (ollama, together, groq, etc.)
swafra config --provider openai-compatible --key KEY --url http://localhost:11434/v1
```

Or set environment variables (auto-detected, no config needed):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# or
export OPENAI_API_KEY=sk-...
# or
export SWAFRA_LLM_API_KEY=... SWAFRA_LLM_BASE_URL=http://localhost:11434/v1
```

**What LLM extraction adds:**

| Feature | Regex (default) | With LLM |
|---------|----------------|----------|
| Entity extraction | Title-cased words only | All entities including lowercase (python, react, kubernetes) |
| Semantic dedup | None — stores duplicates | Detects and skips duplicate knowledge at ingest |
| Preference detection | Pattern matching | Full semantic understanding |
| Topic extraction | None | Identifies themes and topics |

The LLM is called once per ingest (cheap — ~100 tokens per chunk). Search/retrieval never calls the LLM. Falls back to regex if the LLM call fails or times out.

To remove: `swafra config --clear`

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

**3. Hybrid retrieval + reranking**
4-signal fused scoring: BM25 + vector cosine + entity/date overlap + character n-gram. Returns the best chunk per source for diverse, non-redundant context. When an LLM key is configured, a single batched relevance-scoring call reorders the final results for higher precision.

**4. Knowledge graph**
Chunks are connected with sequential (next/prev), similarity, entity co-occurrence, and cross-session edges. Graph walk expands retrieval beyond what search alone finds.

**5. LLM extraction (optional)**
When an LLM key is configured, entities, preferences, and topics are extracted semantically — catching lowercase tech terms, tools, and concepts that regex misses. New content is checked for semantic duplication before storing. Falls back to regex when no LLM is available.

**6. Fact lifecycle**
Structured facts are extracted from chunks. When a new fact conflicts with an old one (e.g. "favorite editor" changes), the old fact is superseded — stale chunks get penalized in search.

**7. Storage**
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
| `ANTHROPIC_API_KEY` | — | Anthropic key (auto-enables LLM extraction) |
| `OPENAI_API_KEY` | — | OpenAI key (auto-enables LLM extraction) |
| `SWAFRA_LLM_API_KEY` | — | Custom LLM key (use with `SWAFRA_LLM_BASE_URL`) |
| `SWAFRA_LLM_BASE_URL` | — | OpenAI-compatible endpoint URL |

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

## Releases

### 0.3.2

- Fix: SQLite backend was bypassed — after auto-migration all reads/writes still hit JSON files, making the knowledge base appear empty. All operations now route through the adaptive storage layer correctly.
- Fix: `get_context(k=5)` could return 35+ results with many sources. `k` is now a hard ceiling.
- Fix: intra-source chunk supersession never fired on re-ingest (old chunks were checked after being stripped from the store).

### 0.3.1

- **Python SDK** — `import swafra; swafra.add("...")` works directly without MCP or CLI
- **JS/TS SDK** — `import { Memory } from 'swafra'` for Node.js with full TypeScript types
- **LLM reranking** — when an LLM key is configured, `get_context` and `search_knowledge(rerank=True)` run a single batched relevance-scoring call to reorder results; falls back silently when no LLM is available

### 0.3.0

- Adaptive storage: starts with JSON, auto-migrates to SQLite at 5k+ chunks
- SQLite: ACID transactions, WAL mode, indexes — handles 100k+ chunks
- No new dependencies (uses Python stdlib `sqlite3`)
- `swafra migrate` command to switch to SQLite early
- JSON files backed up as `.bak` after migration (safe rollback)
- Stats dashboard reads from whichever backend is active

### 0.2.9

- Security: API keys in `~/.scimap/config.json` are now stored with `600` permissions (owner-only)
- Added MIT LICENSE file
- `--model` flag: choose any model your provider offers
- Node CLI has full `swafra config` parity with Python CLI
- Cleaner npm package (excluded test files and bytecode)

### 0.2.8

- LLM-powered entity extraction — catches all entities including lowercase tech terms
- Semantic dedup at ingest — stops duplicate knowledge from being stored
- `swafra config` command for LLM provider setup
- Supports Anthropic, OpenAI, and any OpenAI-compatible endpoint (ollama, together, groq)
- Falls back to regex when no LLM configured — zero breakage

### 0.2.7

- `swafra skill` — install as Claude Code skill (no MCP server needed)
- Skill uses bash commands directly, works without running a server process

### 0.2.6

- `swafra setup` — enforcement hooks that guarantee Claude uses memory
- `swafra remove` / `swafra remove global` commands
- Stop hook wakes Claude back up if it skips `get_context`
- Stronger tool descriptions ("MANDATORY: call before first response")

### 0.2.5

- Native Node.js CLI — `npm install -g swafra` works without Python dependency for stats
- Fix: CLI hang on Python 3.14 (broken libexpat)

### 0.2.2

- `swafra` CLI stats dashboard — sources, chunks, edges, communities, entities
- Works from both pip and npm installs with no conflicts

### 0.1.5

- Proactive tool descriptions
- `CLAUDE.md` injection for automatic memory usage

### 0.1.2

- Initial release — MCP server with Leiden chunking, hybrid retrieval, fact lifecycle

---

## License

MIT — [github.com/kunal12203/swafra](https://github.com/kunal12203/swafra)
