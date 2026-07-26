---
name: swafra
description: "Local-first knowledge graph memory for Claude. Store, search, and retrieve knowledge across sessions using swafra's Leiden-chunked graph. Trigger: /swafra"
---

# /swafra — Knowledge Graph Memory

swafra gives Claude persistent, searchable memory using a local knowledge graph. No cloud, no GPU, no API keys.

**GitHub:** https://github.com/kunal12203/swafra
**Benchmark:** 94.7% recall_all@10 on LongMemEval-S (beats Supermemory 95% end-to-end QA on retrieval)

---

## Setup (one time)

```bash
git clone https://github.com/kunal12203/swafra
cd swafra
pnpm install && pnpm build

pip install fastembed numpy  # optional — falls back to local embedder without it
```

Add to Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "swafra": {
      "command": "node",
      "args": ["/absolute/path/to/swafra/dist/index.js"]
    }
  }
}
```

Add to Claude Code (`.claude/settings.json`):

```json
{
  "mcpServers": {
    "swafra": {
      "command": "node",
      "args": ["/absolute/path/to/swafra/dist/index.js"]
    }
  }
}
```

Restart Claude after adding the config.

---

## Tools

| Tool | When to use |
|------|-------------|
| `add_knowledge` | Store a document, note, or any text into memory |
| `search_knowledge` | Find relevant chunks by natural language query |
| `get_context` | Best for retrieval — search + graph walk combined |
| `graph_walk` | Explore context around a specific chunk |
| `list_sources` | See what's been stored |
| `delete_source` | Remove a source from memory |

---

## What You Must Do When Invoked

When the user types `/swafra`, read their request and do ONE of:

### Store something
If the user wants to remember/store something:
```
add_knowledge(text="<the content>", title="<short label>")
```
Confirm: "Stored as '<title>' — searchable now."

### Retrieve something
If the user asks a question or wants to find something:
```
get_context(query="<their question>", k=10)
```
Use the returned chunks to answer. If nothing relevant comes back, say so clearly — don't hallucinate.

### List what's stored
```
list_sources()
```
Show the list cleanly.

### Delete something
```
delete_source(title="<title>")
```
Confirm deletion.

---

## Usage examples

```
/swafra store this meeting transcript: [paste text]
/swafra what did we decide about the API design?
/swafra what do you know about the auth system?
/swafra list everything stored
/swafra forget the meeting from last Tuesday
```

---

## How it works

1. **Leiden chunking** — splits text into semantically coherent chunks via community detection on a hybrid graph (semantic similarity + entity co-occurrence + position)
2. **Local embeddings** — fastembed (ONNX, CPU) or deterministic hash vectors — no API calls
3. **Fused retrieval** — BM25 + vectors + entity overlap + character n-grams, source-diverse ranking
4. **Knowledge graph** — chunks are connected with sequential, similarity, and community edges for graph-walk retrieval

Storage: `~/.swafra/` JSON files. No database required.
