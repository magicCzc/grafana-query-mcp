# Copyright (c) 2025, Project Grafana Query MCP. All rights reserved.

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from pydantic import BaseModel, Field, field_validator

from .auth import require_token
from .clients.grafana import get_health, list_datasources, paginate_series, query_ds
from .config import get_config
from .converter.grafana import convert_to_grafana
from .discovery import get_discovery_state, record_metric_hit, run_discovery_loop
from .utils.cache import TTLCache

app = FastAPI()

_logger = logging.getLogger("app")
_handler = logging.StreamHandler()
try:
    from pythonjsonlogger import jsonlogger

    _handler.setFormatter(
        jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(message)s")
    )
except Exception:
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
_logger.addHandler(_handler)
_logger.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO"), logging.INFO))

metrics_requests = Counter("app_requests_total", "Total requests", ["path"])
metrics_latency = Gauge("app_latency_ms", "Latency per endpoint", ["path"])

_pagination_cache = TTLCache(default_ttl=300)


def success(data: Any, pagination: Optional[Dict[str, Any]] = None) -> JSONResponse:
    body = {"status": "status.ok", "pagination": pagination or {}, "data": data}
    return JSONResponse(content=body)


def failure(code: str, http_status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=http_status, content={"status": "status.error", "error_code": code}
    )


@app.get("/metrics")
def metrics() -> Any:
    return StreamingResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/discovery/state")
async def discovery_state(token: str = Depends(require_token)) -> JSONResponse:
    metrics_requests.labels(path="/v1/discovery/state").inc()
    return success(get_discovery_state())


@app.middleware("http")
async def measure_latency(request: Request, call_next):
    path = request.url.path
    t0 = asyncio.get_event_loop().time()
    resp = await call_next(request)
    t1 = asyncio.get_event_loop().time()
    ms = (t1 - t0) * 1000.0
    metrics_latency.labels(path=path).set(ms)
    _logger.info(json.dumps({"path": path, "latency_ms": round(ms, 2)}))
    return resp


@app.on_event("startup")
async def _startup_scan() -> None:
    try:
        cfg = get_config().discovery
        if cfg.enabled:
            asyncio.create_task(run_discovery_loop())
    except Exception:
        pass


class ParsedInput(BaseModel):
    provider: str
    service: str
    names: List[str]
    range: str
    metric: str
    metrics: Optional[List[str]] = None
    agg: Optional[str] = None
    datasource_uid: Optional[str] = None

    @field_validator("range")
    @classmethod
    def validate_range(cls, v: str):
        import re

        if not re.match(r"^\d+(m|h|d|s)$", v):
            raise ValueError("error.payload.bad_range")
        return v

    @field_validator("agg")
    @classmethod
    def validate_agg(cls, v: Optional[str]):
        if v is None:
            return v
        if v not in {"avg", "max", "min", "sum"}:
            raise ValueError("error.payload.bad_agg")
        return v


class ConvertBody(BaseModel):
    parsed: ParsedInput


@app.post("/v1/grafana/convert")
async def grafana_convert(
    payload: ConvertBody, token: str = Depends(require_token)
) -> JSONResponse:
    metrics_requests.labels(path="/v1/grafana/convert").inc()
    p = payload.parsed
    if not p.datasource_uid:
        r = await _autoselect_with_data(p)
        if not r:
            return failure("error.datasource.no_data", 404)
        p.datasource_uid = r["uid"]
    ds_info = await _get_ds_info_by_uid(p.datasource_uid)
    ds_type = ds_info.get("type") if ds_info else None
    ds_id = ds_info.get("id") if ds_info else None
    q = convert_to_grafana(
        p.provider,
        p.service,
        p.names,
        p.range,
        p.metric,
        p.metrics,
        p.agg,
        p.datasource_uid,
        None,
        ds_type,
        ds_id,
    ).model_dump()
    return success(q)


class QueryStartBody(BaseModel):
    parsed: ParsedInput
    page_size: int = Field(default=100, ge=1, le=1000)


@app.post("/v1/query/start")
async def query_start(
    payload: QueryStartBody, token: str = Depends(require_token)
) -> StreamingResponse:
    metrics_requests.labels(path="/v1/query/start").inc()
    p = payload.parsed
    if not p.datasource_uid:
        r = await _autoselect_with_data(p)
        if not r:
            ev = b'event:error\ndata:{"error_code":"error.datasource.no_data"}\n\n'
            return StreamingResponse(iter([ev]), media_type="text/event-stream")
        p.datasource_uid = r["uid"]
    ds_info = await _get_ds_info_by_uid(p.datasource_uid)
    ds_type = ds_info.get("type") if ds_info else None
    ds_id = ds_info.get("id") if ds_info else None
    q = convert_to_grafana(
        p.provider,
        p.service,
        p.names,
        p.range,
        p.metric,
        p.metrics,
        p.agg,
        p.datasource_uid,
        None,
        ds_type,
        ds_id,
    ).model_dump()
    result = await query_ds(q)
    if "error_code" in result:
        ev = f"event:error\ndata:{json.dumps(result)}\n\n".encode("utf-8")
        return StreamingResponse(iter([ev]), media_type="text/event-stream")
    default_size = int(os.getenv("PAGE_DEFAULT_SIZE", "100"))
    max_size = int(os.getenv("PAGE_MAX_SIZE", "1000"))
    size = payload.page_size or default_size
    if size > max_size:
        size = max_size
    pages = paginate_series(result, page_size=size)
    try:
        if pages and pages[0].get("series"):
            record_metric_hit(
                p.datasource_uid, p.metric, (q.get("x_dimension") or "auto")
            )
    except Exception:
        pass
    pid = uuid.uuid4().hex
    _pagination_cache.set(pid, pages, ttl=300)

    async def event_stream():
        meta = {"pagination_id": pid, "total_pages": len(pages)}
        yield f"event:meta\ndata:{json.dumps(meta)}\n\n".encode("utf-8")
        for i, page in enumerate(pages):
            ev = {"index": i, "series_count": len(page.get("series", []))}
            yield f"event:page\ndata:{json.dumps(ev)}\n\n".encode("utf-8")
            await asyncio.sleep(0)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


class ExecuteBody(BaseModel):
    parsed: ParsedInput
    page_size: int = Field(default=100, ge=1, le=1000)
    page_index: int = Field(default=0, ge=0)


@app.post("/v1/query/execute")
async def query_execute(
    payload: ExecuteBody, token: str = Depends(require_token)
) -> JSONResponse:
    metrics_requests.labels(path="/v1/query/execute").inc()
    p = payload.parsed
    if not p.datasource_uid:
        r = await _autoselect_with_data(p)
        if not r:
            return failure("error.datasource.no_data", 404)
        p.datasource_uid = r["uid"]
    ds_info = await _get_ds_info_by_uid(p.datasource_uid)
    ds_type = ds_info.get("type") if ds_info else None
    ds_id = ds_info.get("id") if ds_info else None
    q = convert_to_grafana(
        p.provider,
        p.service,
        p.names,
        p.range,
        p.metric,
        p.metrics,
        p.agg,
        p.datasource_uid,
        None,
        ds_type,
        ds_id,
    ).model_dump()
    result = await query_ds(q)
    if "error_code" in result:
        return failure(result["error_code"], 502)
    default_size = int(os.getenv("PAGE_DEFAULT_SIZE", "100"))
    max_size = int(os.getenv("PAGE_MAX_SIZE", "1000"))
    size = payload.page_size or default_size
    if size > max_size:
        size = max_size
    pages = paginate_series(result, page_size=size)
    try:
        if pages and pages[0].get("series"):
            record_metric_hit(
                p.datasource_uid, p.metric, (q.get("x_dimension") or "auto")
            )
    except Exception:
        pass
    idx = min(max(payload.page_index, 0), max(len(pages) - 1, 0))
    return success(
        pages[idx],
        pagination={
            "total_pages": len(pages),
            "index": idx,
            "page_size": size,
            "query": q,
        },
    )


class AnomalyBody(BaseModel):
    range: str
    pctl: int = Field(default=95)
    include_normal: bool = Field(default=False)

    @field_validator("range")
    @classmethod
    def validate_range(cls, v: str):
        import re

        if not re.match(r"^\d+(m|h|d|s)$", v):
            raise ValueError("error.payload.bad_range")
        return v

    @field_validator("pctl")
    @classmethod
    def validate_pctl(cls, v: int):
        if v not in {90, 95, 99}:
            raise ValueError("error.payload.bad_pctl")
        return v


@app.post("/v1/anomaly/instances")
async def anomaly_instances(
    payload: AnomalyBody, token: str = Depends(require_token)
) -> StreamingResponse:
    metrics_requests.labels(path="/v1/anomaly/instances").inc()
    r = payload.range
    pval = (payload.pctl or 95) / 100.0
    ds = await list_datasources()
    if "error_code" in ds:
        async def error_stream():
            yield f"event:error\ndata:{json.dumps({'error_code': ds['error_code']})}\n\n".encode("utf-8")
        return StreamingResponse(error_stream(), media_type="text/event-stream")
    items = ds.get("items", [])
    now_ms = int(time.time() * 1000)
    unit = r[-1]
    num = int(r[:-1])
    if unit == "m":
        dur_ms = num * 60 * 1000
    elif unit == "h":
        dur_ms = num * 60 * 60 * 1000
    elif unit == "d":
        dur_ms = num * 24 * 60 * 60 * 1000
    else:
        dur_ms = num * 1000

    def _calc_step_and_points(dur: int) -> tuple[int, int]:
        # Optimize for performance: use fewer points for anomaly detection
        # We don't need high resolution for "is this instance anomalous?"
        target_points = 20
        step = max(60 * 1000, dur // target_points)
        return step, target_points

    interval_ms, max_points = _calc_step_and_points(dur_ms)
    cpu_thr = 70.0
    mem_thr = 80.0
    fs_thr = 80.0
    out: Dict[str, Dict[str, Any]] = {}

    def _last(vs: List[float]) -> Optional[float]:
        return vs[-1] if vs else None

    def _pctl(vs: List[float], p: float) -> Optional[float]:
        if not vs:
            return None
        arr = sorted(vs)
        idx = int(max(min(len(arr) - 1, round(p * (len(arr) - 1))), 0))
        return arr[idx]

    # 创建一个队列来存储处理结果
    result_queue = asyncio.Queue()

    async def process_item(it: Dict[str, Any]):
        t = (it.get("type") or "").lower()
        uid = it.get("uid")
        _logger.info(f"Processing datasource: {it.get('name')} (type: {t})")
        if "prometheus" in t:
            qs_all = [
                {
                    "refId": "A",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": '100 * (1 - (sum by (instance)(rate(node_cpu_seconds_total{{mode="idle"}}[5m])) / sum by (instance)(rate(node_cpu_seconds_total[5m]))) ) and on (instance) node_uname_info',
                    "format": "time_series",
                },
                {
                    "refId": "B",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": "sum by (instance)(100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))) and on (instance) node_uname_info",
                    "format": "time_series",
                },
                {
                    "refId": "C",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": 'max by (instance)(100 * (1 - (node_filesystem_avail_bytes{{fstype!~"(tmpfs|devtmpfs)"}} / node_filesystem_size_bytes{{fstype!~"(tmpfs|devtmpfs)"}}))) and on (instance) node_uname_info',
                    "format": "time_series",
                },
                {
                    "refId": "D",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": "node_load1 and on (instance) node_uname_info",
                    "format": "time_series",
                },
                {
                    "refId": "E",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": '100 * (sum by (instance)(rate(node_cpu_seconds_total{{mode="iowait"}}[5m])) / sum by (instance)(rate(node_cpu_seconds_total[5m]))) and on (instance) node_uname_info',
                    "format": "time_series",
                },
                {
                    "refId": "F",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": 'sum by (instance)(rate(node_network_receive_errs_total{{device!~"^(lo|docker.*|veth.*|br.*|flannel.*)$"}}[5m])) and on (instance) node_uname_info',
                    "format": "time_series",
                },
                {
                    "refId": "G",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": 'sum by (instance)(rate(node_network_transmit_errs_total{{device!~"^(lo|docker.*|veth.*|br.*|flannel.*)$"}}[5m])) and on (instance) node_uname_info',
                    "format": "time_series",
                },
                {
                    "refId": "H",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": 'sum by (instance)(rate(node_disk_io_time_seconds_total{{device!~"^(ram.*|loop.*|dm-.*)$"}}[5m])) * 100 and on (instance) node_uname_info',
                    "format": "time_series",
                },
                {
                    "refId": "I",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": "node_load5 and on (instance) node_uname_info",
                    "format": "time_series",
                },
                {
                    "refId": "J",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": "node_load15 and on (instance) node_uname_info",
                    "format": "time_series",
                },
                {
                    "refId": "K",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": "node_processes_running and on (instance) node_uname_info",
                    "format": "time_series",
                },
                {
                    "refId": "L",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": "node_processes_blocked and on (instance) node_uname_info",
                    "format": "time_series",
                },
                {
                    "refId": "M",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": "sum by (instance)(rate(node_context_switches_total[5m])) and on (instance) node_uname_info",
                    "format": "time_series",
                },
                {
                    "refId": "N",
                    "intervalMs": interval_ms,
                    "maxDataPoints": max_points,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": "sum by (instance)(rate(node_intr_total[5m])) and on (instance) node_uname_info",
                    "format": "time_series",
                },
            ]
            res_all: Dict[str, Any] = {}
            # Execute all queries in parallel (one per request) to maximize concurrency
            async def safe_query(q_payload):
                try:
                    return await query_ds(q_payload)
                except Exception:
                    return {}

            tasks = []
            for q_def in qs_all:
                q = {
                    "queries": [q_def],
                    "from_str": f"now-{r}",
                    "to_str": "now",
                    "from_ts": now_ms - dur_ms,
                    "to_ts": now_ms,
                }
                tasks.append(asyncio.create_task(safe_query(q)))

            # Wait for Prometheus tasks
            done, pending = await asyncio.wait(tasks)
                
            results = []
            for t in done:
                try:
                    results.append(t.result())
                except Exception:
                    pass
            
            for part in results:
                rs = (part.get("results") or {}) if isinstance(part, dict) else {}
                for k, v in rs.items():
                    res_all[k] = v
            res = {"results": res_all}
            
            # Pre-calculation optimization
            
            def _series(ref: str, _res=res) -> Dict[str, List[float]]:
                d: Dict[str, List[float]] = {}
                a = (_res.get("results", {}) or {}).get(ref, {})
                frs = a.get("frames") or []
                if frs:
                    for fr in frs:
                        fields = (fr.get("schema", {}) or {}).get("fields") or []
                        labels = fields[1] or {}
                        lab = labels.get("labels") or {}
                        name = lab.get("nodename") or lab.get("instance") or ""
                        vals = (fr.get("data", {}) or {}).get("values") or []
                        arr: List[float] = []
                        if len(vals) > 1 and isinstance(vals[1], list) and vals[1]:
                            for vv in vals[1]:
                                try:
                                    arr.append(float(vv))
                                except Exception:
                                    continue
                        if name and arr:
                            d[str(name)] = arr
                else:
                    sers = a.get("series") or []
                    for s in sers:
                        labels = s.get("fields", [{}])[1] or {}
                        lab = labels.get("labels") or {}
                        name = lab.get("nodename") or lab.get("instance") or ""
                        values = s.get("values") or []
                        arr: List[float] = []
                        if values and isinstance(values, list):
                            try:
                                for vv in values:
                                    arr.append(float(vv[-1]))
                            except Exception:
                                arr = []
                        if name and arr:
                            d[str(name)] = arr
                return d

            cpu_series = _series("A")
            mem_series = _series("B")
            fs_series = _series("C")
            load_series = _series("D")
            iowait_series = _series("E")
            nerr_rx_series = _series("F")
            nerr_tx_series = _series("G")
            ioutil_series = _series("H")
            load5_series = _series("I")
            load15_series = _series("J")
            proc_run_series = _series("K")
            proc_blk_series = _series("L")
            ctxsw_series = _series("M")
            intr_series = _series("N")
            
            names = set().union(
                cpu_series.keys(),
                mem_series.keys(),
                fs_series.keys(),
                load_series.keys(),
                iowait_series.keys(),
                nerr_rx_series.keys(),
                nerr_tx_series.keys(),
                ioutil_series.keys(),
                load5_series.keys(),
                load15_series.keys(),
                proc_run_series.keys(),
                proc_blk_series.keys(),
                ctxsw_series.keys(),
                intr_series.keys(),
            )
            
            for nm in names:
                mvals: Dict[str, Any] = {}
                cv = _last(cpu_series.get(nm, []))
                mv = _last(mem_series.get(nm, []))
                fv = _last(fs_series.get(nm, []))
                lv = _last(load_series.get(nm, []))
                iw = _last(iowait_series.get(nm, []))
                nerx = _last(nerr_rx_series.get(nm, []))
                netx = _last(nerr_tx_series.get(nm, []))
                iout = _last(ioutil_series.get(nm, []))
                l5 = _last(load5_series.get(nm, []))
                l15 = _last(load15_series.get(nm, []))
                pr = _last(proc_run_series.get(nm, []))
                pb = _last(proc_blk_series.get(nm, []))
                cs = _last(ctxsw_series.get(nm, []))
                it_val = _last(intr_series.get(nm, []))
                if cv is not None:
                    thr = _pctl(cpu_series.get(nm, []), pval) or cpu_thr
                    ex = cv > thr
                    if ex or payload.include_normal:
                        mvals["cpu_pct"] = {
                            "current": cv,
                            "threshold": thr,
                            "exceed": ex,
                        }
                if mv is not None:
                    thr = _pctl(mem_series.get(nm, []), pval) or mem_thr
                    ex = mv > thr
                    if ex or payload.include_normal:
                        mvals["memory_pct"] = {
                            "current": mv,
                            "threshold": thr,
                            "exceed": ex,
                        }
                if fv is not None:
                    thr = _pctl(fs_series.get(nm, []), pval) or fs_thr
                    ex = fv > thr
                    if ex or payload.include_normal:
                        mvals["disk_used_pct"] = {
                            "current": fv,
                            "threshold": thr,
                            "exceed": ex,
                        }
                if lv is not None:
                    thr = _pctl(load_series.get(nm, []), pval)
                    if thr is not None:
                        ex = lv > thr
                        if ex or payload.include_normal:
                            mvals["load1"] = {
                                "current": lv,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if iw is not None:
                    thr = _pctl(iowait_series.get(nm, []), pval)
                    if thr is not None:
                        ex = iw > thr
                        if ex or payload.include_normal:
                            mvals["iowait_pct"] = {
                                "current": iw,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if nerx is not None:
                    thr = _pctl(nerr_rx_series.get(nm, []), pval)
                    if thr is not None:
                        ex = nerx > thr
                        if ex or payload.include_normal:
                            mvals["net_errors_rx"] = {
                                "current": nerx,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if netx is not None:
                    thr = _pctl(nerr_tx_series.get(nm, []), pval)
                    if thr is not None:
                        ex = netx > thr
                        if ex or payload.include_normal:
                            mvals["net_errors_tx"] = {
                                "current": netx,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if iout is not None:
                    thr = _pctl(ioutil_series.get(nm, []), pval)
                    if thr is not None:
                        ex = iout > thr
                        if ex or payload.include_normal:
                            mvals["disk_util_pct"] = {
                                "current": iout,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if l5 is not None:
                    thr = _pctl(load5_series.get(nm, []), pval)
                    if thr is not None:
                        ex = l5 > thr
                        if ex or payload.include_normal:
                            mvals["load5"] = {
                                "current": l5,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if l15 is not None:
                    thr = _pctl(load15_series.get(nm, []), pval)
                    if thr is not None:
                        ex = l15 > thr
                        if ex or payload.include_normal:
                            mvals["load15"] = {
                                "current": l15,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if pr is not None:
                    thr = _pctl(proc_run_series.get(nm, []), pval)
                    if thr is not None:
                        ex = pr > thr
                        if ex or payload.include_normal:
                            mvals["processes_running"] = {
                                "current": pr,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if pb is not None:
                    thr = _pctl(proc_blk_series.get(nm, []), pval)
                    if thr is not None:
                        ex = pb > thr
                        if ex or payload.include_normal:
                            mvals["processes_blocked"] = {
                                "current": pb,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if cs is not None:
                    thr = _pctl(ctxsw_series.get(nm, []), pval)
                    if thr is not None:
                        ex = cs > thr
                        if ex or payload.include_normal:
                            mvals["context_switches"] = {
                                "current": cs,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if it_val is not None:
                    thr = _pctl(intr_series.get(nm, []), pval)
                    if thr is not None:
                        ex = it_val > thr
                        if ex or payload.include_normal:
                            mvals["interrupts"] = {
                                "current": it_val,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if mvals:
                    instance_data = {"name": nm, "datasource_uid": uid, "metrics": mvals}
                    out[nm] = instance_data
                    await result_queue.put(("instance", instance_data))
        elif "alexanderzobnin-zabbix-datasource" in t or "zabbix" in t:
            q = {
                "queries": [
                    {
                        "refId": "A",
                        "intervalMs": 10000,
                        "maxDataPoints": 100,
                        "intervalFactor": 1,
                        "datasource": {
                            "uid": uid,
                            "type": "alexanderzobnin-zabbix-datasource",
                        },
                        "format": "time_series",
                        "resultFormat": "time_series",
                        "expr": "",
                        "queryType": "0",
                        "group": {"filter": r"/.*/"},
                        "host": {"filter": r"/.*/"},
                        "application": {"filter": "CPU"},
                        "item": {"filter": r"/(cpu used percent|CPU used percent)/"},
                        "options": {
                            "showDisabledItems": False,
                            "skipEmptyValues": False,
                        },
                        "table": {"skipEmptyValues": False},
                        "triggers": {
                            "acknowledged": 2,
                            "count": True,
                            "minSeverity": 3,
                        },
                    },
                    {
                        "refId": "B",
                        "intervalMs": 10000,
                        "maxDataPoints": 100,
                        "intervalFactor": 1,
                        "datasource": {
                            "uid": uid,
                            "type": "alexanderzobnin-zabbix-datasource",
                        },
                        "format": "time_series",
                        "resultFormat": "time_series",
                        "expr": "",
                        "queryType": "0",
                        "group": {"filter": r"/.*/"},
                        "host": {"filter": r"/.*/"},
                        "application": {"filter": "Memory"},
                        "item": {
                            "filter": r"/(Used memory percent|Memory utilization|Used memory)/"
                        },
                        "options": {
                            "showDisabledItems": False,
                            "skipEmptyValues": False,
                        },
                        "table": {"skipEmptyValues": False},
                        "triggers": {
                            "acknowledged": 2,
                            "count": True,
                            "minSeverity": 3,
                        },
                    },
                    {
                        "refId": "C",
                        "intervalMs": 10000,
                        "maxDataPoints": 100,
                        "intervalFactor": 1,
                        "datasource": {
                            "uid": uid,
                            "type": "alexanderzobnin-zabbix-datasource",
                        },
                        "format": "time_series",
                        "resultFormat": "time_series",
                        "expr": "",
                        "queryType": "0",
                        "group": {"filter": r"/.*/"},
                        "host": {"filter": r"/.*/"},
                        "application": {"filter": "Filesystem"},
                        "item": {
                            "filter": r"/(Used disk space on / \(percentage\)|Used disk space on /data \(percentage\)|Used disk space on /u01 \(percentage\))/"
                        },
                        "options": {
                            "showDisabledItems": False,
                            "skipEmptyValues": False,
                        },
                        "table": {"skipEmptyValues": False},
                        "triggers": {
                            "acknowledged": 2,
                            "count": True,
                            "minSeverity": 3,
                        },
                    },
                    {
                        "refId": "D",
                        "intervalMs": 10000,
                        "maxDataPoints": 100,
                        "intervalFactor": 1,
                        "datasource": {
                            "uid": uid,
                            "type": "alexanderzobnin-zabbix-datasource",
                        },
                        "format": "time_series",
                        "resultFormat": "time_series",
                        "expr": "",
                        "queryType": "0",
                        "group": {"filter": r"/.*/"},
                        "host": {"filter": r"/.*/"},
                        "application": {"filter": "Network interfaces"},
                        "item": {"filter": r"/(Incoming network traffic|Outgoing network traffic)/"},
                        "options": {
                            "showDisabledItems": False,
                            "skipEmptyValues": False,
                        },
                        "table": {"skipEmptyValues": False},
                        "triggers": {
                            "acknowledged": 2,
                            "count": True,
                            "minSeverity": 3,
                        },
                    },
                    {
                        "refId": "E",
                        "intervalMs": 10000,
                        "maxDataPoints": 100,
                        "intervalFactor": 1,
                        "datasource": {
                            "uid": uid,
                            "type": "alexanderzobnin-zabbix-datasource",
                        },
                        "format": "time_series",
                        "resultFormat": "time_series",
                        "expr": "",
                        "queryType": "0",
                        "group": {"filter": r"/.*/"},
                        "host": {"filter": r"/.*/"},
                        "application": {"filter": "System"},
                        "item": {
                            "filter": r"/(Load average \(5 min\)|Load avg \(5m\))/"
                        },
                        "options": {
                            "showDisabledItems": False,
                            "skipEmptyValues": False,
                        },
                        "table": {"skipEmptyValues": False},
                        "triggers": {
                            "acknowledged": 2,
                            "count": True,
                            "minSeverity": 3,
                        },
                    },
                    {
                        "refId": "F",
                        "intervalMs": 10000,
                        "maxDataPoints": 100,
                        "intervalFactor": 1,
                        "datasource": {
                            "uid": uid,
                            "type": "alexanderzobnin-zabbix-datasource",
                        },
                        "format": "time_series",
                        "resultFormat": "time_series",
                        "expr": "",
                        "queryType": "0",
                        "group": {"filter": r"/.*/"},
                        "host": {"filter": r"/.*/"},
                        "application": {"filter": "System"},
                        "item": {
                            "filter": r"/(Load average \(15 min\)|Load avg \(15m\))/"
                        },
                        "options": {
                            "showDisabledItems": False,
                            "skipEmptyValues": False,
                        },
                        "table": {"skipEmptyValues": False},
                        "triggers": {
                            "acknowledged": 2,
                            "count": True,
                            "minSeverity": 3,
                        },
                    },
                    {
                        "refId": "G",
                        "intervalMs": 10000,
                        "maxDataPoints": 100,
                        "intervalFactor": 1,
                        "datasource": {
                            "uid": uid,
                            "type": "alexanderzobnin-zabbix-datasource",
                        },
                        "format": "time_series",
                        "resultFormat": "time_series",
                        "expr": "",
                        "queryType": "0",
                        "group": {"filter": r"/.*/"},
                        "host": {"filter": r"/.*/"},
                        "application": {"filter": "Processes"},
                        "item": {"filter": r"/(Processes running|Running processes)/"},
                        "options": {
                            "showDisabledItems": False,
                            "skipEmptyValues": False,
                        },
                        "table": {"skipEmptyValues": False},
                        "triggers": {
                            "acknowledged": 2,
                            "count": True,
                            "minSeverity": 3,
                        },
                    },
                    {
                        "refId": "H",
                        "intervalMs": 10000,
                        "maxDataPoints": 100,
                        "intervalFactor": 1,
                        "datasource": {
                            "uid": uid,
                            "type": "alexanderzobnin-zabbix-datasource",
                        },
                        "format": "time_series",
                        "resultFormat": "time_series",
                        "expr": "",
                        "queryType": "0",
                        "group": {"filter": r"/.*/"},
                        "host": {"filter": r"/.*/"},
                        "application": {"filter": "Processes"},
                        "item": {"filter": r"/(Processes blocked|Blocked processes)/"},
                        "options": {
                            "showDisabledItems": False,
                            "skipEmptyValues": False,
                        },
                        "table": {"skipEmptyValues": False},
                        "triggers": {
                            "acknowledged": 2,
                            "count": True,
                            "minSeverity": 3,
                        },
                    },
                    {
                        "refId": "I",
                        "intervalMs": 10000,
                        "maxDataPoints": 100,
                        "intervalFactor": 1,
                        "datasource": {
                            "uid": uid,
                            "type": "alexanderzobnin-zabbix-datasource",
                        },
                        "format": "time_series",
                        "resultFormat": "time_series",
                        "expr": "",
                        "queryType": "0",
                        "group": {"filter": r"/.*/"},
                        "host": {"filter": r"/.*/"},
                        "application": {"filter": "System"},
                        "item": {"filter": r"/(Context switches)/"},
                        "options": {
                            "showDisabledItems": False,
                            "skipEmptyValues": False,
                        },
                        "table": {"skipEmptyValues": False},
                        "triggers": {
                            "acknowledged": 2,
                            "count": True,
                            "minSeverity": 3,
                        },
                    },
                    {
                        "refId": "J",
                        "intervalMs": 10000,
                        "maxDataPoints": 100,
                        "intervalFactor": 1,
                        "datasource": {
                            "uid": uid,
                            "type": "alexanderzobnin-zabbix-datasource",
                        },
                        "format": "time_series",
                        "resultFormat": "time_series",
                        "expr": "",
                        "queryType": "0",
                        "group": {"filter": r"/.*/"},
                        "host": {"filter": r"/.*/"},
                        "application": {"filter": "System"},
                        "item": {"filter": r"/(Interrupts)/"},
                        "options": {
                            "showDisabledItems": False,
                            "skipEmptyValues": False,
                        },
                        "table": {"skipEmptyValues": False},
                        "triggers": {
                            "acknowledged": 2,
                            "count": True,
                            "minSeverity": 3,
                        },
                    },
                ],
                "from_str": f"now-{r}",
                "to_str": "now",
                "from_ts": now_ms - dur_ms,
                "to_ts": now_ms,
            }
            try:
                res = await query_ds(q)
            except Exception:
                res = {}

            def _series_z(ref: str, _res=res) -> Dict[str, List[float]]:
                d: Dict[str, List[float]] = {}
                a = (_res.get("results", {}) or {}).get(ref, {})
                frs = a.get("frames") or []
                if frs:
                    for fr in frs:
                        fields = (fr.get("schema", {}) or {}).get("fields") or []
                        labels = fields[1] or {}
                        lab = labels.get("labels") or {}
                        name = lab.get("host") or lab.get("hostname") or ""
                        vals = (fr.get("data", {}) or {}).get("values") or []
                        arr: List[float] = []
                        if len(vals) > 1 and isinstance(vals[1], list) and vals[1]:
                            for vv in vals[1]:
                                try:
                                    arr.append(float(vv))
                                except Exception:
                                    continue
                        if name and arr:
                            d[str(name)] = arr
                else:
                    sers = a.get("series") or []
                    for s in sers:
                        labels = s.get("fields", [{}])[1] or {}
                        lab = labels.get("labels") or {}
                        name = lab.get("host") or lab.get("hostname") or ""
                        values = s.get("values") or []
                        arr: List[float] = []
                        if values and isinstance(values, list):
                            try:
                                for vv in values:
                                    arr.append(float(vv[-1]))
                            except Exception:
                                arr = []
                        if name and arr:
                            d[str(name)] = arr
                return d

            cpu_series = _series_z("A")
            mem_series = _series_z("B")
            fs_series = _series_z("C")
            net_series = _series_z("D")
            load5_series = _series_z("E")
            load15_series = _series_z("F")
            proc_run_series = _series_z("G")
            proc_blk_series = _series_z("H")
            ctxsw_series = _series_z("I")
            intr_series = _series_z("J")
            names = set().union(
                cpu_series.keys(),
                mem_series.keys(),
                fs_series.keys(),
                net_series.keys(),
                load5_series.keys(),
                load15_series.keys(),
                proc_run_series.keys(),
                proc_blk_series.keys(),
                ctxsw_series.keys(),
                intr_series.keys(),
            )
            for nm in names:
                mvals: Dict[str, Any] = {}
                cv = _last(cpu_series.get(nm, []))
                mv = _last(mem_series.get(nm, []))
                fv = _last(fs_series.get(nm, []))
                nv = _last(net_series.get(nm, []))
                l5 = _last(load5_series.get(nm, []))
                l15 = _last(load15_series.get(nm, []))
                pr = _last(proc_run_series.get(nm, []))
                pb = _last(proc_blk_series.get(nm, []))
                cs = _last(ctxsw_series.get(nm, []))
                it_val = _last(intr_series.get(nm, []))
                if cv is not None:
                    thr = _pctl(cpu_series.get(nm, []), pval) or cpu_thr
                    ex = cv > thr
                    if ex or payload.include_normal:
                        mvals["cpu_pct"] = {
                            "current": cv,
                            "threshold": thr,
                            "exceed": ex,
                        }
                if mv is not None:
                    thr = _pctl(mem_series.get(nm, []), pval) or mem_thr
                    ex = mv > thr
                    if ex or payload.include_normal:
                        mvals["memory_pct"] = {
                            "current": mv,
                            "threshold": thr,
                            "exceed": ex,
                        }
                if fv is not None:
                    thr = _pctl(fs_series.get(nm, []), pval) or fs_thr
                    ex = fv > thr
                    if ex or payload.include_normal:
                        mvals["disk_used_pct"] = {
                            "current": fv,
                            "threshold": thr,
                            "exceed": ex,
                        }
                if nv is not None:
                    thr = _pctl(net_series.get(nm, []), pval)
                    if thr is not None:
                        ex = nv > thr
                        if ex or payload.include_normal:
                            mvals["network_traffic_anomaly"] = {
                                "current": nv,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if l5 is not None:
                    thr = _pctl(load5_series.get(nm, []), pval)
                    if thr is not None:
                        ex = l5 > thr
                        if ex or payload.include_normal:
                            mvals["load5"] = {
                                "current": l5,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if l15 is not None:
                    thr = _pctl(load15_series.get(nm, []), pval)
                    if thr is not None:
                        ex = l15 > thr
                        if ex or payload.include_normal:
                            mvals["load15"] = {
                                "current": l15,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if pr is not None:
                    thr = _pctl(proc_run_series.get(nm, []), pval)
                    if thr is not None:
                        ex = pr > thr
                        if ex or payload.include_normal:
                            mvals["processes_running"] = {
                                "current": pr,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if pb is not None:
                    thr = _pctl(proc_blk_series.get(nm, []), pval)
                    if thr is not None:
                        ex = pb > thr
                        if ex or payload.include_normal:
                            mvals["processes_blocked"] = {
                                "current": pb,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if cs is not None:
                    thr = _pctl(ctxsw_series.get(nm, []), pval)
                    if thr is not None:
                        ex = cs > thr
                        if ex or payload.include_normal:
                            mvals["context_switches"] = {
                                "current": cs,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if it_val is not None:
                    thr = _pctl(intr_series.get(nm, []), pval)
                    if thr is not None:
                        ex = it_val > thr
                        if ex or payload.include_normal:
                            mvals["interrupts"] = {
                                "current": it_val,
                                "threshold": thr,
                                "exceed": ex,
                            }
                if mvals:
                    instance_data = {"name": nm, "datasource_uid": uid, "metrics": mvals}
                    out[nm] = instance_data
                    await result_queue.put(("instance", instance_data))

    # 启动数据处理任务
    tasks = [asyncio.create_task(process_item(it)) for it in items]
    
    async def event_stream():
        # 异步生成器，用于流式输出结果
        total = len(items)
        
        # 等待所有处理任务完成
        done, pending = await asyncio.wait(tasks)
        
        # 发送所有实例数据
        while not result_queue.empty():
            event_type, data = await result_queue.get()
            yield f"event:{event_type}\ndata:{json.dumps(data)}\n\n".encode("utf-8")
        
        # 发送汇总信息
        summary = {"count": len(out), "items_processed": total}
        yield f"event:meta\ndata:{json.dumps(summary)}\n\n".encode("utf-8")
    
    return StreamingResponse(event_stream(), media_type="text/event-stream")


async def _autoselect_with_data(p: ParsedInput) -> Optional[Dict[str, str]]:
    ds = await list_datasources()
    if "error_code" in ds:
        return None
    items = ds.get("items", [])
    for cand in items:
        uid = cand.get("uid")
        typ = (cand.get("type") or "").lower()
        if "prometheus" in typ:
            exprs = _candidate_exprs(p)
            for ex in exprs:
                q = convert_to_grafana(
                    p.provider,
                    p.service,
                    p.names,
                    p.range,
                    p.metric,
                    p.metrics,
                    p.agg,
                    uid,
                    ex,
                    typ,
                ).model_dump()
                result = await query_ds(q)
                ares = result.get("results", {}).get("A", {})
                series = ares.get("series", [])
                frames = ares.get("frames", [])
                if series or frames:
                    return {"uid": uid}
        elif "zabbix" in typ:
            ds_id = cand.get("id")
            q = convert_to_grafana(
                p.provider,
                p.service,
                p.names,
                p.range,
                p.metric,
                p.metrics,
                p.agg,
                uid,
                None,
                typ,
                ds_id,
            ).model_dump()
            result = await query_ds(q)
            frames = result.get("results", {}).get("A", {}).get("frames", [])
            if frames:
                return {"uid": uid}
    return None


def _candidate_exprs(p: ParsedInput) -> List[str]:
    pat = "|".join(p.names)
    r = p.range
    exprs: List[str] = []
    try:
        state = get_discovery_state()
        prom = state.get("prometheus", {})
        metrics_union: set[str] = set()
        for v in prom.values():
            for m in v.get("metrics") or []:
                metrics_union.add(str(m))
        has_node = any(m.startswith("node_") for m in metrics_union)
        has_container = any(m.startswith("container_") for m in metrics_union)
    except Exception:
        has_node = True
        has_container = True
    if (p.metric or "") == "memory":
        join_mem = (
            f"sum by (instance)((node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes)) "
            f'and on (instance) node_uname_info{{nodename=~"{pat}"}}'
        )
        if has_node:
            exprs.append(join_mem)
        if has_container:
            exprs.append(
                f'sum by (pod)(container_memory_working_set_bytes{{pod=~"{pat}"}})'
            )
    else:
        join_node = (
            f'sum by (instance)(rate(node_cpu_seconds_total{{mode!="idle"}}[{r}])) '
            f'and on (instance) node_uname_info{{nodename=~"{pat}"}}'
        )
        if has_node:
            exprs.append(join_node)
            exprs.append(
                f'sum by (instance)(rate(node_cpu_seconds_total{{mode!="idle",instance=~"{pat}"}}[{r}]))'
            )
        if has_container:
            exprs.append(
                f'sum by (pod)(rate(container_cpu_usage_seconds_total{{pod=~"{pat}"}}[{r}]))'
            )
        exprs.append(f'rate(process_cpu_seconds_total{{instance=~"{pat}"}}[{r}])')
    return exprs


async def _get_ds_info_by_uid(uid: str) -> Optional[Dict[str, Any]]:
    ds = await list_datasources()
    if "error_code" in ds:
        return None
    for i in ds.get("items", []):
        if i.get("uid") == uid:
            return i
    return None


@app.get("/v1/grafana/datasources_remote")
async def datasources_remote(token: str = Depends(require_token)) -> JSONResponse:
    metrics_requests.labels(path="/v1/grafana/datasources_remote").inc()
    data = await list_datasources()
    if "error_code" in data:
        return failure(data["error_code"], 502)
    return success(data)


@app.get("/v1/grafana/health")
async def grafana_health(token: str = Depends(require_token)) -> JSONResponse:
    metrics_requests.labels(path="/v1/grafana/health").inc()
    data = await get_health()
    if "error_code" in data:
        return failure(data["error_code"], 502)
    return success(data)


class RawGrafanaBody(BaseModel):
    queries: List[Dict[str, Any]]
    from_ts: Optional[int] = None
    to_ts: Optional[int] = None
    from_str: Optional[str] = None
    to_str: Optional[str] = None


@app.post("/v1/grafana/raw")
async def grafana_raw(
    payload: RawGrafanaBody, token: str = Depends(require_token)
) -> JSONResponse:
    metrics_requests.labels(path="/v1/grafana/raw").inc()
    body = {
        "queries": payload.queries,
        "from_ts": payload.from_ts or int(time.time() * 1000) - 5 * 60 * 1000,
        "to_ts": payload.to_ts or int(time.time() * 1000),
        "from": payload.from_str or "now-5m",
        "to": payload.to_str or "now",
    }
    result = await query_ds(body)
    if "error_code" in result:
        return failure(result["error_code"], 502)
    return success(result)
