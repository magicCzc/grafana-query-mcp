# Copyright (c) 2025, Project Grafana Query MCP. All rights reserved.

import base64
import os
import time
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class TTLCache:
    def __init__(self, default_ttl: int = 60):
        self.default_ttl = default_ttl
        self._store: Dict[str, Tuple[float, bytes]] = {}
        key = os.getenv("APP_ENCRYPT_KEY", "")
        if key:
            self._key = _derive_key(key)
        else:
            self._key = AESGCM.generate_key(bit_length=128)
        self._aes = AESGCM(self._key)

    def set(self, k: str, v: Any, ttl: Optional[int] = None) -> None:
        exp = time.time() + (ttl or self.default_ttl)
        data = _bjson(v)
        nonce = os.urandom(12)
        ct = nonce + self._aes.encrypt(nonce, data, None)
        self._store[k] = (exp, ct)

    def get(self, k: str) -> Optional[Any]:
        item = self._store.get(k)
        if not item:
            return None
        exp, ct = item
        if time.time() > exp:
            self._store.pop(k, None)
            return None
        nonce, payload = ct[:12], ct[12:]
        try:
            data = self._aes.decrypt(nonce, payload, None)
        except Exception:
            return None
        return _bjload(data)

    def delete(self, k: str) -> None:
        self._store.pop(k, None)


def _derive_key(s: str) -> bytes:
    raw = base64.urlsafe_b64encode(s.encode("utf-8"))[:16]
    pad = raw.ljust(16, b"\0")
    return pad


def _bjson(v: Any) -> bytes:
    import json

    return json.dumps(v, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _bjload(b: bytes) -> Any:
    import json

    return json.loads(b.decode("utf-8"))
