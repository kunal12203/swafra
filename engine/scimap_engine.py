#!/usr/bin/env python3
"""Entry point for the scimap engine subprocess.

This file is kept for backward compatibility — the TS bridge spawns it directly.
All logic lives in the engine package modules.
"""
import sys
from pathlib import Path

# Ensure the project root is on sys.path so `engine.*` imports resolve
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from engine.rpc import main  # noqa: E402

if __name__ == "__main__":
    main()
