#!/bin/bash
# Stop hook: remind Claude if it forgot to retrieve memory at session start
# Only fires if the MCP server is actually running (swafra is connected)
MARKER_DIR="${HOME}/.scimap/.session"

# Skip if no data exists (fresh install, nothing to retrieve)
if [ ! -d "${HOME}/.scimap" ] || [ ! -f "${HOME}/.scimap/chunks.json" ]; then
    rm -rf "$MARKER_DIR"
    exit 0
fi

# Skip if chunks.json is empty/just "[]"
CHUNKS_SIZE=$(stat -f%z "${HOME}/.scimap/chunks.json" 2>/dev/null || echo "0")
if [ "$CHUNKS_SIZE" -lt 10 ]; then
    rm -rf "$MARKER_DIR"
    exit 0
fi

if [ ! -f "$MARKER_DIR/retrieved" ]; then
    echo "SWAFRA REMINDER: You have persistent memory but did not call get_context this session. Call it now with the user's topic/question as the query." >&2
    rm -rf "$MARKER_DIR"
    exit 2
fi

# Clean up markers for next session
rm -rf "$MARKER_DIR"
exit 0
