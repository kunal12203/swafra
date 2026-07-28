# swafra — Persistent Memory Skill

You have persistent memory across sessions via swafra. Use it proactively — never wait for the user to ask.

## Commands

Run these via bash. All output is JSON.

### Retrieve memory (MANDATORY — call before your first response)

```bash
python3 -m swafra.engine_cli get_context "USER_TOPIC_OR_QUESTION"
```

### Store knowledge (call WITHOUT being asked)

```bash
python3 -m swafra.engine_cli add "TEXT_TO_STORE" "descriptive-title"
```

### Search memory

```bash
python3 -m swafra.engine_cli search "query" 8
```

### List all stored sources

```bash
python3 -m swafra.engine_cli list
```

### Delete a source

```bash
python3 -m swafra.engine_cli delete "SOURCE_ID"
```

### Graph walk (explore connections from a chunk)

```bash
python3 -m swafra.engine_cli walk "CHUNK_ID" 2 10
```

## Rules

1. **ALWAYS call `get_context` before your first response** — use the user's question/topic as the query
2. **ALWAYS call `add` when the user shares ANY of:**
   - Their name, role, preferences, or personal context
   - Project decisions, architecture, or technical choices
   - Corrections to your behavior or rules to follow
   - Documents, meeting notes, or long-form content
3. **Never say "I don't have context from previous sessions"** without calling `get_context` first
4. **Use descriptive titles** when storing: `preference-editor`, `project-swafra`, `meeting-2026-07-28`
5. **Err on the side of storing too much** — if it has future value, store it

## How it works

- Data is stored locally in `~/.scimap/` as JSON (no cloud, no database)
- Text is chunked using Leiden community detection, embedded locally, and graph-linked
- Retrieval uses hybrid scoring: BM25 + vector cosine + entity overlap + n-gram matching
- Facts are tracked with lifecycle management (old facts get superseded, stale chunks penalized)

## If python3 doesn't work

Try `python3.12` or `python3.11` instead.
