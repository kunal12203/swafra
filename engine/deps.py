"""Auto-install missing Python dependencies."""
from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger("scimap.engine")

_REQUIRED_PACKAGES = {
    "fastembed": "fastembed>=0.4.0",
    "numpy": "numpy>=1.26.0",
}

_CLUSTERING_PACKAGES = {
    "igraph": "igraph>=0.11.0",
    "leidenalg": "leidenalg>=0.10.0",
}

_FALLBACK_PACKAGES = {
    "networkx": "networkx>=3.0",
}


def _in_virtualenv() -> bool:
    return (
        hasattr(sys, "real_prefix")
        or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
        or os.getenv("VIRTUAL_ENV") is not None
    )


def _pip_install(packages: list[str]) -> bool:
    """Try to pip install packages. Returns True on success."""
    import subprocess
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    if not _in_virtualenv():
        cmd.append("--user")
    cmd.extend(packages)
    try:
        subprocess.check_call(cmd, stdout=sys.stderr, stderr=sys.stderr)
        return True
    except subprocess.CalledProcessError:
        pass
    cmd_break = [sys.executable, "-m", "pip", "install", "--quiet",
                 "--disable-pip-version-check", "--break-system-packages"]
    if not _in_virtualenv():
        cmd_break.append("--user")
    cmd_break.extend(packages)
    try:
        subprocess.check_call(cmd_break, stdout=sys.stderr, stderr=sys.stderr)
        return True
    except subprocess.CalledProcessError:
        return False


def ensure_deps():
    # 1. Core deps (required)
    missing = []
    for import_name, pip_spec in _REQUIRED_PACKAGES.items():
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_spec)
    if missing:
        log.info("Installing core dependencies: %s", ", ".join(missing))
        if not _pip_install(missing):
            log.error("Failed to install dependencies: pip install %s", " ".join(missing))
            sys.exit(1)

    # 2. Clustering: try Leiden first, fall back to networkx
    leiden_ok = True
    for import_name in _CLUSTERING_PACKAGES:
        try:
            __import__(import_name)
        except ImportError:
            leiden_ok = False
            break

    if not leiden_ok:
        leiden_specs = list(_CLUSTERING_PACKAGES.values())
        log.info("Attempting Leiden install: %s", ", ".join(leiden_specs))
        if _pip_install(leiden_specs):
            log.info("Leiden clustering available (best quality)")
            return

        # Leiden failed — ensure networkx as fallback
        try:
            import networkx  # noqa: F401
            log.info("Using Louvain clustering via networkx (Leiden unavailable)")
        except ImportError:
            fallback_specs = list(_FALLBACK_PACKAGES.values())
            log.info("Installing networkx for Louvain fallback: %s", ", ".join(fallback_specs))
            if _pip_install(fallback_specs):
                log.info("Louvain clustering available via networkx")
            else:
                log.warning("No clustering library available — community detection disabled")
                log.warning("Install for best results: pip install igraph leidenalg")
