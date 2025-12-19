# Copyright (c) 2025, Project Grafana Query MCP. All rights reserved.

import time
from typing import Dict, List

from fastapi import Header, HTTPException

from .config import get_config


_rate_buckets: Dict[str, List[float]] = {}


def require_token(authorization: str = Header(default="")) -> str:
    cfg = get_config().security
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error_code": "error.auth.missing_bearer"})
    token = authorization.split(" ", 1)[1]
    if not cfg.app_token or token != cfg.app_token:
        raise HTTPException(status_code=401, detail={"error_code": "error.auth.invalid_token"})
    _rate_limit(token)
    return token


def _rate_limit(token: str) -> None:
    now = time.time()
    window = 60.0
    limit = get_config().security.rate_limit_per_minute
    bucket = _rate_buckets.setdefault(token, [])
    bucket.append(now)
    while bucket and now - bucket[0] > window:
        bucket.pop(0)
    if len(bucket) > limit:
        raise HTTPException(status_code=429, detail={"error_code": "error.rate.limit_exceeded"})
