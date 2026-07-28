#!/bin/bash
# PostToolUse hook: track when swafra tools are called this session
MARKER_DIR="${HOME}/.scimap/.session"
mkdir -p "$MARKER_DIR"

if [[ "$TOOL_NAME" == *"get_context"* ]]; then
    touch "$MARKER_DIR/retrieved"
fi
if [[ "$TOOL_NAME" == *"add_knowledge"* ]]; then
    touch "$MARKER_DIR/stored"
fi
exit 0
