"""Small polling worker; crash recovery lives in the durable store."""

import logging
import math
import sqlite3
import threading

from .store import Store

logger = logging.getLogger("knowledge_task_api.worker")


class Worker:
    def __init__(self, store: Store, *, processor=None, heartbeat_interval=None):
        self.store = store
        self.processor = processor
        self.heartbeat_interval = store.lease_seconds / 3 if heartbeat_interval is None else heartbeat_interval
        if not isinstance(self.heartbeat_interval, (int, float)) or isinstance(self.heartbeat_interval, bool) or not math.isfinite(self.heartbeat_interval) or not 0 < self.heartbeat_interval < store.lease_seconds:
            raise ValueError("heartbeat_interval must be finite, positive and shorter than the lease")
        self.stopping = threading.Event()
        self.thread = None

    def run_once(self):
        lease = self.store.claim()
        if lease is None:
            return False
        finished, lost = threading.Event(), threading.Event()

        def heartbeat():
            while not finished.wait(self.heartbeat_interval):
                if self.stopping.is_set():
                    lost.set()
                    return
                try:
                    if not self.store.renew(lease):
                        lost.set()
                        return
                except Exception as error:
                    logger.error("lease renewal failed: %s", type(error).__name__)
                    lost.set()
                    return

        monitor = threading.Thread(target=heartbeat, name="knowledge-lease", daemon=True)
        monitor.start()
        try:
            if self.processor is not None:
                self.processor(lease)
            finished.set()
            monitor.join()
            if lost.is_set() or self.stopping.is_set():
                self.store.fail(lease, retryable=True)
                return True
            self.store.complete(lease)
        except sqlite3.OperationalError:
            self.store.fail(lease, retryable=True)
        except Exception:
            logger.error("indexing failed for task %s", lease.id)
            self.store.fail(lease, retryable=False)
        finally:
            finished.set()
            monitor.join()
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
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)
