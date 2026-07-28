"""swafra CLI — `swafra` in terminal shows knowledge graph stats."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path


DATA_DIR = Path(os.getenv("SCIMAP_DATA_DIR", os.path.expanduser("~/.scimap")))

CHUNKS_FILE = DATA_DIR / "chunks.json"
EDGES_FILE = DATA_DIR / "edges.json"
SOURCES_FILE = DATA_DIR / "sources.json"
FACTS_FILE = DATA_DIR / "facts.json"


def _load(path: Path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def _format_size(nbytes: int) -> str:
    if nbytes < 1024:
        return f"{nbytes} B"
    elif nbytes < 1024 * 1024:
        return f"{nbytes / 1024:.1f} KB"
    else:
        return f"{nbytes / (1024 * 1024):.1f} MB"


def _file_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def stats():
    """Print detailed knowledge graph statistics."""
    if not DATA_DIR.exists():
        print("  No data found. Start by adding knowledge through an MCP client.")
        print(f"  Data directory: {DATA_DIR}")
        return

    chunks = _load(CHUNKS_FILE)
    edges = _load(EDGES_FILE)
    sources = _load(SOURCES_FILE)
    facts = _load(FACTS_FILE)

    active_chunks = [c for c in chunks if not c.get("superseded_by")]
    superseded_chunks = [c for c in chunks if c.get("superseded_by")]

    # Edge type breakdown
    edge_types = Counter(e.get("type", "unknown") for e in edges)

    # Community stats
    communities = Counter(c.get("community_id") for c in active_chunks)

    # Entity stats
    all_entities = []
    for c in active_chunks:
        all_entities.extend(c.get("entities", []))
    entity_counts = Counter(all_entities)

    # Token stats
    total_tokens = sum(c.get("token_count", 0) for c in active_chunks)

    # Chunk type breakdown
    chunk_types = Counter(c.get("type", "unknown") for c in active_chunks)

    # Fact stats
    active_facts = [f for f in facts if f.get("valid_to") is None]
    superseded_facts = [f for f in facts if f.get("valid_to") is not None]

    # Cross-session edges
    cross_session = sum(1 for e in edges if e.get("type") == "cross_session")

    # Storage size
    total_size = sum(_file_size(p) for p in [CHUNKS_FILE, EDGES_FILE, SOURCES_FILE, FACTS_FILE])

    # Source breakdown
    source_chunks = defaultdict(int)
    for c in active_chunks:
        source_chunks[c.get("source_title", "unknown")] += 1

    # Print
    print()
    print("  \033[1;36m╭─────────────────────────────────────────╮\033[0m")
    print("  \033[1;36m│\033[0m       \033[1;37mswafra\033[0m knowledge graph stats       \033[1;36m│\033[0m")
    print("  \033[1;36m╰─────────────────────────────────────────╯\033[0m")
    print()

    # Overview
    print("  \033[1;33m⚡ Overview\033[0m")
    print(f"     Sources:          {len(sources)}")
    print(f"     Active chunks:    {len(active_chunks)}")
    print(f"     Superseded:       {len(superseded_chunks)}")
    print(f"     Edges:            {len(edges)}")
    print(f"     Total tokens:     {total_tokens:,}")
    print(f"     Storage:          {_format_size(total_size)}")
    print(f"     Data dir:         {DATA_DIR}")
    print()

    # Graph topology
    print("  \033[1;33m🔗 Graph Topology\033[0m")
    print(f"     Communities:      {len(communities)}")
    print(f"     Cross-session:    {cross_session} edges")
    if edge_types:
        print("     Edge types:")
        for etype, count in edge_types.most_common():
            print(f"       {etype:<15} {count}")
    print()

    # Facts
    print("  \033[1;33m📋 Facts\033[0m")
    print(f"     Active:           {len(active_facts)}")
    print(f"     Superseded:       {len(superseded_facts)}")
    print(f"     Total:            {len(facts)}")
    print()

    # Entities
    print("  \033[1;33m🏷️  Entities\033[0m")
    print(f"     Unique:           {len(entity_counts)}")
    if entity_counts:
        print("     Top entities:")
        for ent, count in entity_counts.most_common(10):
            print(f"       {ent:<20} ({count} chunks)")
    print()

    # Chunk types
    if chunk_types:
        print("  \033[1;33m📦 Chunk Types\033[0m")
        for ctype, count in chunk_types.most_common():
            print(f"     {ctype:<18} {count}")
        print()

    # Sources
    if sources:
        print("  \033[1;33m📚 Sources\033[0m")
        sorted_sources = sorted(source_chunks.items(), key=lambda x: x[1], reverse=True)
        for title, count in sorted_sources[:15]:
            display = title[:35] + "…" if len(title) > 35 else title
            print(f"     {display:<37} {count} chunks")
        if len(sorted_sources) > 15:
            print(f"     ... and {len(sorted_sources) - 15} more")
        print()


def setup():
    """Install swafra hooks into user's global Claude Code settings."""
    import json

    claude_dir = Path.home() / ".claude"
    settings_path = claude_dir / "settings.json"
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # Write hook scripts
    post_tool_hook = hooks_dir / "swafra-post-tool.sh"
    post_tool_hook.write_text('''#!/bin/bash
# Track when swafra tools are called this session
MARKER_DIR="${HOME}/.scimap/.session"
mkdir -p "$MARKER_DIR"
if [[ "$TOOL_NAME" == *"get_context"* ]]; then
    touch "$MARKER_DIR/retrieved"
fi
if [[ "$TOOL_NAME" == *"add_knowledge"* ]]; then
    touch "$MARKER_DIR/stored"
fi
exit 0
''')
    post_tool_hook.chmod(0o755)

    stop_hook = hooks_dir / "swafra-stop.sh"
    stop_hook.write_text('''#!/bin/bash
# Remind Claude to call get_context if it has stored memory
MARKER_DIR="${HOME}/.scimap/.session"

# Skip if no data exists
if [ ! -f "${HOME}/.scimap/chunks.json" ]; then
    rm -rf "$MARKER_DIR"
    exit 0
fi

# Skip if empty
CHUNKS_SIZE=$(stat -f%z "${HOME}/.scimap/chunks.json" 2>/dev/null || stat -c%s "${HOME}/.scimap/chunks.json" 2>/dev/null || echo "0")
if [ "$CHUNKS_SIZE" -lt 10 ]; then
    rm -rf "$MARKER_DIR"
    exit 0
fi

if [ ! -f "$MARKER_DIR/retrieved" ]; then
    echo "SWAFRA: You have persistent memory but did not call get_context. Call it now with the user topic as the query." >&2
    rm -rf "$MARKER_DIR"
    exit 2
fi

rm -rf "$MARKER_DIR"
exit 0
''')
    stop_hook.chmod(0o755)

    # Update settings.json
    settings = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())

    hooks = settings.setdefault("hooks", {})

    # Add PostToolUse hook
    post_hooks = hooks.setdefault("PostToolUse", [])
    # Remove existing swafra hook if present
    post_hooks = [h for h in post_hooks if "swafra" not in str(h.get("hooks", [{}])[0].get("command", ""))]
    post_hooks.append({
        "matcher": "mcp__swafra",
        "hooks": [{"type": "command", "command": f"bash {post_tool_hook}", "timeout": 5}]
    })
    hooks["PostToolUse"] = post_hooks

    # Add Stop hook
    stop_hooks = hooks.setdefault("Stop", [])
    stop_hooks = [h for h in stop_hooks if "swafra" not in str(h.get("hooks", [{}])[0].get("command", ""))]
    stop_hooks.append({
        "matcher": "",
        "hooks": [{"type": "command", "command": f"bash {stop_hook}", "timeout": 5}]
    })
    hooks["Stop"] = stop_hooks

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    # Write global CLAUDE.md instructions (append if exists)
    claude_md = claude_dir / "CLAUDE.md"
    swafra_block = """
# swafra Memory

You have persistent memory tools available via swafra MCP. Use them proactively.

## Rules
- ALWAYS call `get_context` at the start of every conversation with the user's topic
- ALWAYS call `add_knowledge` when the user shares preferences, decisions, context, or corrections
- Never say "I don't have context from previous sessions" without calling `get_context` first
- Use descriptive titles when storing: 'preference-editor', 'project-name', 'meeting-2026-07'
"""
    if claude_md.exists():
        content = claude_md.read_text()
        if "swafra" not in content.lower():
            claude_md.write_text(content.rstrip() + "\n" + swafra_block)
    else:
        claude_md.write_text(swafra_block)

    print()
    print("  \033[1;32m✓\033[0m swafra hooks installed!")
    print()
    print("  What was set up:")
    print(f"    → Stop hook:       {stop_hook}")
    print(f"    → PostToolUse hook: {post_tool_hook}")
    print(f"    → Settings:        {settings_path}")
    print(f"    → CLAUDE.md:       {claude_md}")
    print()
    print("  Claude will now:")
    print("    • Always call get_context at session start")
    print("    • Proactively store knowledge without being asked")
    print("    • Get reminded if it forgets")
    print()


def remove(global_remove: bool = False):
    """Remove swafra hooks and optionally all data."""
    import json

    claude_dir = Path.home() / ".claude"
    settings_path = claude_dir / "settings.json"
    hooks_dir = claude_dir / "hooks"

    # Remove hook scripts
    removed = []
    for hook_file in ["swafra-post-tool.sh", "swafra-stop.sh"]:
        p = hooks_dir / hook_file
        if p.exists():
            p.unlink()
            removed.append(str(p))

    # Remove from settings.json
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
        hooks = settings.get("hooks", {})
        for key in ["PostToolUse", "Stop"]:
            if key in hooks:
                hooks[key] = [h for h in hooks[key] if "swafra" not in json.dumps(h)]
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    # Remove swafra block from CLAUDE.md
    claude_md = claude_dir / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text()
        # Remove the swafra section
        import re
        content = re.sub(r'\n# swafra Memory\n.*?(?=\n# |\Z)', '', content, flags=re.DOTALL)
        claude_md.write_text(content.strip() + "\n")

    # Clean session markers
    session_dir = Path.home() / ".scimap" / ".session"
    if session_dir.exists():
        import shutil
        shutil.rmtree(session_dir)

    print()
    print("  \033[1;32m✓\033[0m swafra hooks removed!")
    print()
    if removed:
        for r in removed:
            print(f"    → Deleted: {r}")
    print(f"    → Cleaned: {settings_path}")
    print(f"    → Cleaned: {claude_md}")

    if global_remove:
        # Remove all swafra data
        data_dir = Path(os.getenv("SCIMAP_DATA_DIR", Path.home() / ".scimap"))
        if data_dir.exists():
            import shutil
            shutil.rmtree(data_dir)
            print(f"    → Deleted: {data_dir} (all knowledge data)")

        # Remove MCP registration
        print()
        print("  To also remove the MCP server:")
        print("    claude mcp remove swafra")

    print()


def install_skill():
    """Install swafra as a Claude Code skill (no MCP needed)."""
    import shutil

    skill_dir = Path.home() / ".claude" / "skills" / "swafra"
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Find the SKILL.md source
    candidates = [
        Path(__file__).parent.parent / "skill" / "SKILL.md",
        Path(__file__).parent / "skill" / "SKILL.md",
    ]

    skill_src = None
    for c in candidates:
        if c.exists():
            skill_src = c
            break

    if not skill_src:
        # Write it inline as fallback
        skill_content = _get_skill_content()
    else:
        skill_content = skill_src.read_text()

    (skill_dir / "SKILL.md").write_text(skill_content)

    print()
    print("  \033[1;32m✓\033[0m swafra skill installed!")
    print()
    print(f"    → {skill_dir / 'SKILL.md'}")
    print()
    print("  Claude Code will now use swafra memory via bash commands.")
    print("  No MCP server needed — works directly through the skill.")
    print()
    print("  Note: Requires `pip install swafra` for the engine.")
    print()


def _get_skill_content() -> str:
    return '''# swafra — Persistent Memory Skill

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

1. **ALWAYS call `get_context` before your first response** — use the user\'s question/topic as the query
2. **ALWAYS call `add` when the user shares ANY of:**
   - Their name, role, preferences, or personal context
   - Project decisions, architecture, or technical choices
   - Corrections to your behavior or rules to follow
   - Documents, meeting notes, or long-form content
3. **Never say "I don\'t have context from previous sessions"** without calling `get_context` first
4. **Use descriptive titles** when storing: `preference-editor`, `project-swafra`, `meeting-2026-07-28`
5. **Err on the side of storing too much** — if it has future value, store it

## How it works

- Data is stored locally in `~/.scimap/` as JSON (no cloud, no database)
- Text is chunked using Leiden community detection, embedded locally, and graph-linked
- Retrieval uses hybrid scoring: BM25 + vector cosine + entity overlap + n-gram matching
- Facts are tracked with lifecycle management (old facts get superseded, stale chunks penalized)

## If python3 doesn\'t work

Try `python3.12` or `python3.11` instead.
'''


def main():
    args = sys.argv[1:]

    if not args or args[0] == "stats":
        print()
        print("  \033[1;37mswafra\033[0m v" + _get_version())
        print("  Leiden-chunked, graph-linked semantic memory")
        stats()
    elif args[0] == "serve":
        from swafra.server import main as serve_main
        serve_main()
    elif args[0] == "setup":
        setup()
    elif args[0] == "skill":
        install_skill()
    elif args[0] == "remove":
        if len(args) > 1 and args[1] == "global":
            remove(global_remove=True)
        else:
            remove(global_remove=False)
    elif args[0] in ("-h", "--help", "help"):
        print()
        print("  \033[1;37mswafra\033[0m — semantic memory for AI")
        print()
        print("  Usage:")
        print("    swafra              Show knowledge graph stats")
        print("    swafra stats        Same as above")
        print("    swafra serve        Start the MCP server (for MCP clients)")
        print("    swafra setup        Install enforcement hooks for Claude Code")
        print("    swafra skill        Install as Claude Code skill (no MCP needed)")
        print("    swafra remove       Disable hooks (keeps data)")
        print("    swafra remove global  Remove hooks + delete all stored data")
        print("    swafra help         Show this help")
        print()
    else:
        print(f"  Unknown command: {args[0]}")
        print("  Run `swafra help` for usage.")
        sys.exit(1)


def _get_version():
    try:
        from swafra import __version__
        return __version__
    except Exception:
        return "0.2.1"


if __name__ == "__main__":
    main()
