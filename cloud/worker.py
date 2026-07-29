"""Standalone ingest worker — scale horizontally against the shared queue.

    SWAFRA_CLOUD_DATABASE_URL=postgres://... python -m cloud.worker

Any number of these processes drain the same jobs table; FOR UPDATE SKIP
LOCKED hands each a disjoint job and lease expiry recovers from crashes.
Without a DATABASE_URL the queue is a node-local SQLite file, so a separate
worker process only sees jobs submitted on this same node (dev only).
"""
from __future__ import annotations

import logging
import threading

from cloud.config import get_config
from cloud.jobs import JobQueue
from cloud.tenancy import StoreRegistry

log = logging.getLogger("swafra.cloud.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    config = get_config()
    if not config.database_url:
        log.warning("no SWAFRA_CLOUD_DATABASE_URL — draining the node-local "
                    "SQLite queue only (dev mode)")
    registry = StoreRegistry(config)
    queue = JobQueue(config, registry)
    count = max(1, config.ingest_workers)
    queue.start_workers(count)
    log.info("worker up: %d threads, backend=%s", count,
             "postgres" if registry.pool else "sqlite")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        log.info("shutting down")
        queue.stop_workers()


if __name__ == "__main__":
    main()
