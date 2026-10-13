"""Small polling worker; crash recovery lives in the durable store."""

import logging
import sqlite3
import threading

from .store import Store

logger = logging.getLogger("knowledge_task_api.worker")


class Worker:
    def __init__(self, store: Store):
        self.store = store
        self.stopping = threading.Event()
        self.thread = None

    def run_once(self):
        lease = self.store.claim()
        if lease is None:
            return False
        try:
            self.store.complete(lease)
        except sqlite3.OperationalError:
            self.store.fail(lease, retryable=True)
        except Exception:
            logger.error("indexing failed for task %s", lease.id)
            self.store.fail(lease, retryable=False)
        return True

    def run(self):
        while not self.stopping.is_set():
            try:
                worked = self.run_once()
            except Exception as error:
                # Do not log payloads, tokens, search text or exception messages.
                logger.error("worker iteration failed: %s", type(error).__name__)
                worked = False
            if not worked:
                self.stopping.wait(0.1)

    def start(self):
        self.thread = threading.Thread(target=self.run, name="knowledge-worker", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
