# Copyright (c) 2025, Project Grafana Query MCP. All rights reserved.

import asyncio
import datetime
import json
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_config
from ..utils.cache import TTLCache


_cache = TTLCache(default_ttl=30)


async def _retry_request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs
) -> httpx.Response:
    backoffs = [0.05, 0.1, 0.2]
    last_exc: Optional[Exception] = None
    for _i, b in enumerate(backoffs):
        try:
            resp = await client.request(method, url, **kwargs)
            if resp.status_code >= 500:
                raise httpx.HTTPError(f"server_error:{resp.status_code}")
            return resp
        except Exception as e:
            last_exc = e
            await asyncio.sleep(b)
    if last_exc:
        raise last_exc
    raise httpx.HTTPError("unknown_error")


async def query_ds(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = get_config().grafana
    if not cfg.base_url or not cfg.token:
        return {"error_code": "error.config.missing_grafana"}
    cache_key = f"q:{json.dumps(payload, sort_keys=True)}"
    cached = _cache.get(cache_key)
    if cached:
        return cached
    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=cfg.timeout_ms / 1000.0) as client:
        url = cfg.base_url.rstrip("/") + "/api/ds/query"
        qs = payload.get("queries", [])
        use_epoch = get_config().grafana.range_style != "relative"
        iso_from = (
            datetime.datetime.utcfromtimestamp(payload["from_ts"] / 1000.0).isoformat()
            + "Z"
        )
        iso_to = (
            datetime.datetime.utcfromtimestamp(payload["to_ts"] / 1000.0).isoformat()
            + "Z"
        )
        raw_range = None
        if payload.get("from_str") and payload.get("to_str"):
            raw_range = {"from": payload.get("from_str"), "to": payload.get("to_str")}
        if use_epoch:
            body = {"queries": qs, "from": payload["from_ts"], "to": payload["to_ts"]}
        else:
            body = {
                "queries": qs,
                "from": payload.get("from_str"),
                "to": payload.get("to_str"),
            }
        body["range"] = {"from": iso_from, "to": iso_to, "raw": raw_range or {}}
        resp = await _retry_request(client, "POST", url, json=body, headers=headers)
    if resp.status_code != 200:
        try:
            err_text = resp.text
        except Exception:
            err_text = ""
        return {
            "error_code": "error.grafana.bad_status",
            "status": resp.status_code,
            "body": err_text,
        }
    data = resp.json()
    _cache.set(cache_key, data, ttl=30)
    return data


def paginate_series(
    result: Dict[str, Any], page_size: int = 100
) -> List[Dict[str, Any]]:
    results_map = result.get("results", {})
    normalized: List[Any] = []
    for ref in sorted(results_map.keys()):
        entry = results_map.get(ref) or {}
        series = entry.get("series") or []
        frames = entry.get("frames") or []
        if series:
            normalized.extend(series)
        elif frames:
            for f in frames:
                name = f.get("schema", {}).get("name")
                fields = f.get("schema", {}).get("fields")
                values = f.get("data", {}).get("values")
                normalized.append({"name": name, "fields": fields, "values": values})
    pages: List[Dict[str, Any]] = []
    chunk: List[Any] = []
    for s in normalized:
        chunk.append(s)
        if len(chunk) >= page_size:
            pages.append({"series": chunk})
            chunk = []
    if chunk:
        pages.append({"series": chunk})
    if not pages:
        pages.append({"series": []})
    return pages


async def list_datasources() -> Dict[str, Any]:
    cfg = get_config().grafana
    if not cfg.base_url or not cfg.token:
        return {"error_code": "error.config.missing_grafana"}
    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=cfg.timeout_ms / 1000.0) as client:
        url = cfg.base_url.rstrip("/") + "/api/datasources"
        resp = await _retry_request(client, "GET", url, headers=headers)
    if resp.status_code != 200:
        return {"error_code": "error.grafana.bad_status", "status": resp.status_code}
    items = []
    for ds in resp.json():
        items.append(
            {
                "name": ds.get("name"),
                "type": ds.get("type"),
                "uid": ds.get("uid"),
                "id": ds.get("id"),
            }
        )
    return {"items": items}


async def get_health() -> Dict[str, Any]:
    cfg = get_config().grafana
    if not cfg.base_url or not cfg.token:
        return {"error_code": "error.config.missing_grafana"}
    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=cfg.timeout_ms / 1000.0) as client:
        url = cfg.base_url.rstrip("/") + "/api/health"
        resp = await _retry_request(client, "GET", url, headers=headers)
    if resp.status_code != 200:
        return {"error_code": "error.grafana.bad_status", "status": resp.status_code}
    return resp.json()


async def resolve_datasource_uid(hints: List[str] | None = None) -> Dict[str, Any]:
    data = await list_datasources()
    if "error_code" in data:
        return data
    items = data.get("items", [])
    prom = [i for i in items if (i.get("type") or "").lower() == "prometheus"]
    if not prom:
        return {"error_code": "error.datasource.not_found"}
    if hints:
        lower_hints = [h.lower() for h in hints if h]
        for ds in prom:
            name = (ds.get("name") or "").lower()
            if any(h in name for h in lower_hints):
                return {"uid": ds.get("uid")}
    return {"uid": prom[0].get("uid")}


async def get_datasource_detail(uid: str) -> Dict[str, Any]:
    cfg = get_config().grafana
    if not cfg.base_url or not cfg.token:
        return {"error_code": "error.config.missing_grafana"}
    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=cfg.timeout_ms / 1000.0) as client:
        url = cfg.base_url.rstrip("/") + f"/api/datasources/uid/{uid}"
        resp = await _retry_request(client, "GET", url, headers=headers)
    if resp.status_code != 200:
        return {"error_code": "error.grafana.bad_status", "status": resp.status_code}
    return resp.json()


async def proxy_get(ds_id: int, path: str) -> Dict[str, Any]:
    cfg = get_config().grafana
    if not cfg.base_url or not cfg.token:
        return {"error_code": "error.config.missing_grafana"}
    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=cfg.timeout_ms / 1000.0) as client:
        base = cfg.base_url.rstrip("/")
        url = f"{base}/api/datasources/proxy/{ds_id}/{path.lstrip('/')}"
        resp = await _retry_request(client, "GET", url, headers=headers)
    if resp.status_code != 200:
        try:
            body = resp.text
        except Exception:
            body = ""
        return {
            "error_code": "error.grafana.bad_status",
            "status": resp.status_code,
            "body": body,
        }
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}
