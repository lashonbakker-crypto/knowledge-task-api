"""Explicit application credentials; no implicit production demo account."""

import hashlib
import hmac
import json
import math
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Principal:
    tenant: str
    role: str


@dataclass(frozen=True)
class Settings:
    database: Path
    keys: dict[str, dict[str, str]]
    worker_enabled: bool = True
    lease_seconds: float = 30
    max_request_bytes: int = 256 * 1024
    stream_seconds: float = 30
    processing_seconds: float = 300
    max_pending_per_tenant: int = 100
    max_pending_total: int = 1000

    def __post_init__(self):
        if not self.keys:
            raise ValueError("at least one access token must be configured")
        fingerprints = set()
        for tenant, record in self.keys.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant):
                raise ValueError("invalid tenant identifier")
            if set(record) != {"key", "role"} or record["role"] not in {"user", "admin"}:
                raise ValueError("each key requires key and role=user|admin")
            key = record["key"]
            if not isinstance(key, str) or len(key) < 16:
                raise ValueError("access tokens must contain at least 16 characters")
            fingerprint = hashlib.sha256(key.encode()).digest()
            if fingerprint in fingerprints:
                raise ValueError("access tokens must be distinct")
            fingerprints.add(fingerprint)
        if isinstance(self.processing_seconds, (int, float)) and self.processing_seconds > threading.TIMEOUT_MAX:
            raise ValueError("processing_seconds exceeds threading.TIMEOUT_MAX")
        for duration in (self.lease_seconds, self.stream_seconds, self.processing_seconds):
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0:
                raise ValueError("durations must be finite and positive")
        if type(self.max_request_bytes) is not int or self.max_request_bytes < 1:
            raise ValueError("max_request_bytes must be a positive integer")
        for limit in (self.max_pending_per_tenant, self.max_pending_total):
            if type(limit) is not int or limit < 1:
                raise ValueError("pending task limits must be positive integers")

    @classmethod
    def from_env(cls):
        source = os.environ.get("KTA_KEYS_JSON")
        if not source:
            raise ValueError("KTA_KEYS_JSON is required; use --demo only for local examples")
        try:
            keys = json.loads(source)
            if not isinstance(keys, dict):
                raise ValueError
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError("invalid KTA_KEYS_JSON configuration") from error
        return cls(Path(os.environ.get("KTA_DATABASE", "data/knowledge.sqlite3")), keys,
                   processing_seconds=processing_seconds_from_env(),
                   max_pending_per_tenant=pending_limit_from_env("KTA_MAX_PENDING_PER_TENANT", 100),
                   max_pending_total=pending_limit_from_env("KTA_MAX_PENDING_TOTAL", 1000))

    @classmethod
    def demo(cls, database: Path, *, worker_enabled=True):
        return cls(database, {
            "alice": {"key": "demo-alice-local-only", "role": "user"},
            "bob": {"key": "demo-bob-local-only-1", "role": "user"},
            "admin": {"key": "demo-admin-local-only", "role": "admin"},
        }, worker_enabled=worker_enabled)

    def authenticate(self, token: str) -> Principal | None:
        digest = hashlib.sha256(token.encode()).digest()
        principal = None
        for tenant, record in self.keys.items():
            expected = hashlib.sha256(record["key"].encode()).digest()
            if hmac.compare_digest(digest, expected):
                principal = Principal(tenant, record["role"])
        return principal


def processing_seconds_from_env():
    try:
        seconds = float(os.environ.get("KTA_PROCESSING_SECONDS", "300"))
        if not math.isfinite(seconds) or not 0 < seconds <= threading.TIMEOUT_MAX:
            raise ValueError
        return seconds
    except ValueError as error:
        raise ValueError("KTA_PROCESSING_SECONDS must be finite, positive and within threading.TIMEOUT_MAX") from error


def pending_limit_from_env(name, default):
    try:
        value = int(os.environ.get(name, str(default)))
        if value < 1:
            raise ValueError
        return value
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer") from error
