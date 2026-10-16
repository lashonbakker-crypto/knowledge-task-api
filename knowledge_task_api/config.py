"""Explicit application credentials; no implicit production demo account."""

import hashlib
import hmac
import json
import math
import os
import re
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
        for duration in (self.lease_seconds, self.stream_seconds):
            if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0:
                raise ValueError("durations must be finite and positive")
        if type(self.max_request_bytes) is not int or self.max_request_bytes < 1:
            raise ValueError("max_request_bytes must be a positive integer")

    @classmethod
    def from_env(cls):
        source = os.environ.get("KTA_KEYS_JSON")
        if not source:
            raise ValueError("KTA_KEYS_JSON is required; use --demo only for local examples")
        try:
            keys = json.loads(source)
            if not isinstance(keys, dict):
                raise ValueError
            return cls(Path(os.environ.get("KTA_DATABASE", "data/knowledge.sqlite3")), keys)
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError("invalid KTA_KEYS_JSON configuration") from error

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
