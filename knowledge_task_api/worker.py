"""Small polling worker; crash recovery lives in the durable store."""

import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass

from .store import Store

logger = logging.getLogger("knowledge_task_api.worker")


class ProcessingInterrupted(RuntimeError):
    """A cooperative processor reached its deadline or lost permission to run."""


@dataclass(frozen=True)
class ProcessingContext:
    deadline: float
    clock: object
    shutdown: threading.Event
    interrupted: threading.Event

    @property
    def remaining_seconds(self):
        return max(0.0, self.deadline - self.clock())

    @property
    def stop_requested(self):
        return self.shutdown.is_set() or self.interrupted.is_set() or self.remaining_seconds == 0

    def checkpoint(self):
        if self.stop_requested:
            raise ProcessingInterrupted("processing permission ended")


class Worker:
    def __init__(self, store: Store, *, processor=None, processor_with_context=None,
                 heartbeat_interval=None, max_processing_seconds=300, processing_clock=time.monotonic):
        self.store = store
        if processor is not None and processor_with_context is not None:
            raise ValueError("choose processor or processor_with_context")
        self.processor = processor
        self.processor_with_context = processor_with_context
        if not isinstance(max_processing_seconds, (int, float)) or isinstance(max_processing_seconds, bool) or not 0 < max_processing_seconds <= threading.TIMEOUT_MAX or not math.isfinite(max_processing_seconds):
            raise ValueError("max_processing_seconds must be finite, positive and within threading.TIMEOUT_MAX")
        self.max_processing_seconds = max_processing_seconds
        self.processing_clock = processing_clock
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
        context = ProcessingContext(self.processing_clock() + self.max_processing_seconds,
                                    self.processing_clock, self.stopping, lost)

        def end_attempt():
            if context.remaining_seconds == 0:
                self.store.timeout(lease)
            else:
                self.store.fail(lease, retryable=True)

        def heartbeat():
            while not finished.wait(min(self.heartbeat_interval, context.remaining_seconds)):
                if context.remaining_seconds == 0:
                    lost.set()
                    try:
                        self.store.timeout(lease)
                    except Exception as error:
                        logger.error("processing timeout record failed: %s", type(error).__name__)
                    return
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
            context.checkpoint()
            if self.processor_with_context is not None:
                self.processor_with_context(lease, context)
            elif self.processor is not None:
                self.processor(lease)
            finished.set()
            monitor.join()
            if context.stop_requested:
                end_attempt()
                return True
            if not self.store.complete(lease, deadline=context.deadline, deadline_clock=self.processing_clock):
                if context.remaining_seconds == 0:
                    self.store.timeout(lease)
        except ProcessingInterrupted:
            end_attempt()
        except sqlite3.OperationalError:
            end_attempt()
        except Exception:
            logger.error("indexing failed for task %s", lease.id)
            if context.remaining_seconds == 0:
                self.store.timeout(lease)
            else:
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
