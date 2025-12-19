# Copyright (c) 2025, Project Grafana Query MCP. All rights reserved.

import asyncio
import json
import os
import time
from typing import Any, Dict, List

import httpx

from .config import get_config
from .clients.grafana import (
    list_datasources,
    query_ds,
    get_datasource_detail,
    proxy_get,
)


_STATE: Dict[str, Any] = {
    "last_scan_ts": 0,
    "prometheus": {},
    "dashboards": [],
    "learned": {"prometheus": {}, "zabbix": {}},
    "zabbix": {"patterns": {"host": [], "netif": []}, "sources": {}},
    "datasources": {"items": [], "by_type": {}},
    "sql": {"sources": {}},
    "elasticsearch": {"sources": {}},
}


def _load_state() -> None:
    try:
        base = os.path.join(os.getcwd(), "cache")
        path = os.path.join(base, "discovery.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _STATE.update(data)
    except Exception:
        pass


def _write_state() -> None:
    try:
        base = os.path.join(os.getcwd(), "cache")
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "discovery.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_STATE, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


async def _scan_prometheus() -> None:
    ds = await list_datasources()
    items = ds.get("items", []) if isinstance(ds, dict) else []
    _STATE.setdefault("datasources", {})["items"] = items
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        t = (it.get("type") or "").lower()
        by_type.setdefault(t, []).append(it)
    _STATE["datasources"]["by_type"] = by_type
    prom_items = [i for i in items if (i.get("type") or "").lower() == "prometheus"]
    for it in prom_items:
        uid = it.get("uid")
        label_hosts: List[str] = []
        label_pods: List[str] = []
        label_pods2: List[str] = []
        label_deploys: List[str] = []
        # node_uname_info -> nodename
        q1 = {
            "queries": [
                {
                    "refId": "A",
                    "intervalMs": 60000,
                    "maxDataPoints": 43200,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": "node_uname_info",
                    "format": "time_series",
                }
            ],
            "from_str": "now-5m",
            "to_str": "now",
            "from_ts": int(time.time() * 1000) - 5 * 60 * 1000,
            "to_ts": int(time.time() * 1000),
        }
        r1 = await query_ds(q1)
        a1 = r1.get("results", {}).get("A", {})
        fields = []
        if a1.get("frames"):
            for fr in a1.get("frames", []):
                for fld in fr.get("schema", {}).get("fields", []):
                    if isinstance(fld, dict):
                        labels = fld.get("labels") or {}
                        if isinstance(labels, dict) and labels.get("nodename"):
                            label_hosts.append(str(labels.get("nodename")))
        elif a1.get("series"):
            for s in a1.get("series", []):
                labels = (s.get("fields", [{}])[1] or {}).get("labels") or {}
                if isinstance(labels, dict) and labels.get("nodename"):
                    label_hosts.append(str(labels.get("nodename")))

        # container_cpu_usage_seconds_total -> pod
        q2 = {
            "queries": [
                {
                    "refId": "A",
                    "intervalMs": 60000,
                    "maxDataPoints": 43200,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": f"sum by (pod)(rate(container_cpu_usage_seconds_total[5m]))",
                    "format": "time_series",
                }
            ],
            "from_str": "now-5m",
            "to_str": "now",
            "from_ts": int(time.time() * 1000) - 5 * 60 * 1000,
            "to_ts": int(time.time() * 1000),
        }
        r2 = await query_ds(q2)
        a2 = r2.get("results", {}).get("A", {})
        if a2.get("frames"):
            for fr in a2.get("frames", []):
                for fld in fr.get("schema", {}).get("fields", []):
                    if isinstance(fld, dict):
                        labels = fld.get("labels") or {}
                        if isinstance(labels, dict) and labels.get("pod"):
                            label_pods.append(str(labels.get("pod")))
        elif a2.get("series"):
            for s in a2.get("series", []):
                labels = (s.get("fields", [{}])[1] or {}).get("labels") or {}
                if isinstance(labels, dict) and labels.get("pod"):
                    label_pods.append(str(labels.get("pod")))

        # kube_pod_info -> pod
        q3 = {
            "queries": [
                {
                    "refId": "A",
                    "intervalMs": 60000,
                    "maxDataPoints": 43200,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": "sum by (pod)(kube_pod_info)",
                    "format": "time_series",
                }
            ],
            "from_str": "now-5m",
            "to_str": "now",
            "from_ts": int(time.time() * 1000) - 5 * 60 * 1000,
            "to_ts": int(time.time() * 1000),
        }
        r3 = await query_ds(q3)
        a3 = r3.get("results", {}).get("A", {})
        if a3.get("frames"):
            for fr in a3.get("frames", []):
                for fld in fr.get("schema", {}).get("fields", []):
                    if isinstance(fld, dict):
                        labels = fld.get("labels") or {}
                        if isinstance(labels, dict) and labels.get("pod"):
                            label_pods2.append(str(labels.get("pod")))
        elif a3.get("series"):
            for s in a3.get("series", []):
                labels = (s.get("fields", [{}])[1] or {}).get("labels") or {}
                if isinstance(labels, dict) and labels.get("pod"):
                    label_pods2.append(str(labels.get("pod")))

        # kube_deployment_labels -> deployment
        q4 = {
            "queries": [
                {
                    "refId": "A",
                    "intervalMs": 60000,
                    "maxDataPoints": 43200,
                    "datasource": {"uid": uid, "type": "prometheus"},
                    "expr": "sum by (deployment)(kube_deployment_labels)",
                    "format": "time_series",
                }
            ],
            "from_str": "now-5m",
            "to_str": "now",
            "from_ts": int(time.time() * 1000) - 5 * 60 * 1000,
            "to_ts": int(time.time() * 1000),
        }
        r4 = await query_ds(q4)
        a4 = r4.get("results", {}).get("A", {})
        if a4.get("frames"):
            for fr in a4.get("frames", []):
                for fld in fr.get("schema", {}).get("fields", []):
                    if isinstance(fld, dict):
                        labels = fld.get("labels") or {}
                        if isinstance(labels, dict) and labels.get("deployment"):
                            label_deploys.append(str(labels.get("deployment")))
        elif a4.get("series"):
            for s in a4.get("series", []):
                labels = (s.get("fields", [{}])[1] or {}).get("labels") or {}
                if isinstance(labels, dict) and labels.get("deployment"):
                    label_deploys.append(str(labels.get("deployment")))

        label_hosts = sorted(list({h for h in label_hosts if h}))
        label_pods = sorted(list({p for p in (label_pods + label_pods2) if p}))
        label_deploys = sorted(list({d for d in label_deploys if d}))
        _STATE.setdefault("prometheus", {})[uid] = {
            "hosts": label_hosts,
            "pods": label_pods,
            "deployments": label_deploys,
        }


async def _scan_dashboards() -> None:
    cfg = get_config().grafana
    dcfg = get_config().discovery
    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=cfg.timeout_ms / 1000.0) as client:
        base = cfg.base_url.rstrip("/")
        res = await client.get(
            base + "/api/search?query=&type=dash-db&limit=200", headers=headers
        )
        if res.status_code != 200:
            return
        items = res.json()
        dashboards: List[Dict[str, Any]] = []
        sem = asyncio.Semaphore(max(dcfg.max_concurrency_dashboards, 1))

        async def _fetch(uid: str):
            async with sem:
                return await client.get(
                    base + f"/api/dashboards/uid/{uid}", headers=headers
                )

        tasks = [_fetch(it.get("uid")) for it in items]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for dres in results:
            try:
                if (
                    isinstance(dres, Exception)
                    or getattr(dres, "status_code", 500) != 200
                ):
                    continue
                data = dres.json()
                templating = (data.get("dashboard") or {}).get("templating") or {}
                list_vars = templating.get("list") or []
                vars_slim: List[Dict[str, Any]] = []
                types_seen: List[str] = []
                for v in list_vars:
                    vars_slim.append(
                        {
                            "name": v.get("name"),
                            "label": v.get("label"),
                            "datasource": (v.get("datasource") or {}).get("type"),
                            "definition": v.get("definition") or v.get("query"),
                            "regex": v.get("regex"),
                        }
                    )
                    try:
                        ds_type = (
                            (v.get("datasource") or {}).get("type") or ""
                        ).lower()
                        if "zabbix" in ds_type:
                            nm = (v.get("name") or "").lower()
                            rgx = v.get("regex") or ""
                            dfn = v.get("definition") or v.get("query") or ""
                            if any(k in nm for k in ["host", "hostname"]):
                                pats = (
                                    _STATE.setdefault("zabbix", {})
                                    .setdefault("patterns", {})
                                    .setdefault("host", [])
                                )
                                if rgx and rgx not in pats:
                                    pats.append(rgx)
                            if any(
                                k in nm for k in ["netif", "iface", "interface", "nic"]
                            ):
                                pats = (
                                    _STATE.setdefault("zabbix", {})
                                    .setdefault("patterns", {})
                                    .setdefault("netif", [])
                                )
                                if rgx and rgx not in pats:
                                    pats.append(rgx)
                    except Exception:
                        pass
                panels = (data.get("dashboard") or {}).get("panels") or []
                for p in panels:
                    dst = ((p.get("datasource") or {}).get("type") or "").lower()
                    if dst:
                        types_seen.append(dst)
                dashboards.append(
                    {
                        "uid": (data.get("dashboard") or {}).get("uid"),
                        "title": (data.get("dashboard") or {}).get("title"),
                        "vars": vars_slim,
                        "types": sorted(list({t for t in types_seen if t})),
                    }
                )
            except Exception:
                continue
        _STATE["dashboards"] = dashboards


async def _scan_zabbix_values() -> None:
    ds = await list_datasources()
    items = ds.get("items", []) if isinstance(ds, dict) else []
    zbx_items = [i for i in items if "zabbix" in ((i.get("type") or "").lower())]
    for it in zbx_items:
        uid = it.get("uid")
        hosts: List[str] = []
        netifs: List[str] = []
        q_hosts = {
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
                    "item": {"filter": "Processor load (1 min average all core)"},
                    "options": {"showDisabledItems": False, "skipEmptyValues": False},
                    "table": {"skipEmptyValues": False},
                    "triggers": {"acknowledged": 2, "count": True, "minSeverity": 3},
                }
            ],
            "from_str": "now-5m",
            "to_str": "now",
            "from_ts": int(time.time() * 1000) - 5 * 60 * 1000,
            "to_ts": int(time.time() * 1000),
        }
        r_hosts = await query_ds(q_hosts)
        a_hosts = r_hosts.get("results", {}).get("A", {})
        if a_hosts.get("frames"):
            for fr in a_hosts.get("frames", []):
                for fld in fr.get("schema", {}).get("fields", []):
                    labels = (fld or {}).get("labels") or {}
                    hv = labels.get("host") or labels.get("hostname")
                    if hv:
                        hosts.append(str(hv))
        elif a_hosts.get("series"):
            for s in a_hosts.get("series", []):
                labels = (s.get("fields", [{}])[1] or {}).get("labels") or {}
                hv = labels.get("host") or labels.get("hostname")
                if hv:
                    hosts.append(str(hv))

        q_net = {
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
                    "application": {"filter": "Network interfaces"},
                    "item": {
                        "filter": r"/Incoming network traffic on (eth.*|ens.*|eno.*)/"
                    },
                    "options": {"showDisabledItems": False, "skipEmptyValues": False},
                    "table": {"skipEmptyValues": False},
                    "triggers": {"acknowledged": 2, "count": True, "minSeverity": 3},
                }
            ],
            "from_str": "now-5m",
            "to_str": "now",
            "from_ts": int(time.time() * 1000) - 5 * 60 * 1000,
            "to_ts": int(time.time() * 1000),
        }
        r_net = await query_ds(q_net)
        a_net = r_net.get("results", {}).get("A", {})

        def _extract_iface(txt: str) -> str:
            try:
                import re

                m = re.search(r"Incoming network traffic on (.+)", txt)
                return m.group(1) if m else txt
            except Exception:
                return txt

        if a_net.get("frames"):
            for fr in a_net.get("frames", []):
                nm = (fr.get("schema", {}) or {}).get("name") or ""
                if nm:
                    netifs.append(_extract_iface(str(nm)))
                for fld in fr.get("schema", {}).get("fields", []):
                    labels = (fld or {}).get("labels") or {}
                    iv = labels.get("item")
                    if iv:
                        netifs.append(_extract_iface(str(iv)))
        elif a_net.get("series"):
            for s in a_net.get("series", []):
                nm = s.get("name") or ""
                if nm:
                    netifs.append(_extract_iface(str(nm)))
                labels = (s.get("fields", [{}])[1] or {}).get("labels") or {}
                iv = labels.get("item")
                if iv:
                    netifs.append(_extract_iface(str(iv)))

        hosts = sorted(list({h for h in hosts if h}))
        netifs = sorted(list({n for n in netifs if n}))
        _STATE.setdefault("zabbix", {}).setdefault("sources", {})[uid] = {
            "hosts": hosts,
            "netifs": netifs,
        }


async def _scan_sql_values() -> None:
    ds = await list_datasources()
    items = ds.get("items", []) if isinstance(ds, dict) else []
    sql_types = {"mysql", "postgres", "mssql"}
    sql_items = [i for i in items if (i.get("type") or "").lower() in sql_types]
    for it in sql_items:
        uid = it.get("uid")
        typ = (it.get("type") or "").lower()
        raw = "SELECT table_schema, table_name FROM information_schema.tables ORDER BY table_schema, table_name LIMIT 200"
        q = {
            "queries": [
                {
                    "refId": "A",
                    "intervalMs": 60000,
                    "maxDataPoints": 43200,
                    "datasource": {"uid": uid, "type": typ},
                    "format": "table",
                    "rawSql": raw,
                }
            ],
            "from_str": "now-5m",
            "to_str": "now",
            "from_ts": int(time.time() * 1000) - 5 * 60 * 1000,
            "to_ts": int(time.time() * 1000),
        }
        try:
            res = await query_ds(q)
        except Exception:
            res = {}
        r = res.get("results", {}).get("A", {})
        schemas: List[str] = []
        tables_by_schema: Dict[str, List[str]] = {}

        def _ingest_rows(fr: Dict[str, Any]) -> None:
            data = (fr.get("data") or {}).get("values") or []
            fields = (fr.get("schema") or {}).get("fields") or []
            cols: Dict[str, List[Any]] = {}
            names: List[str] = []
            for i, f in enumerate(fields):
                nm = (f or {}).get("name") or f
                names.append(str(nm))
                vals = data[i] if i < len(data) else []
                cols[str(nm)] = vals
            if names and cols:
                nrows = max(len(v) for v in cols.values()) if cols else 0
                for idx in range(nrows):
                    sc = (
                        str(
                            (cols.get("table_schema") or cols.get("schema") or [None])[
                                idx
                            ]
                        )
                        if (cols.get("table_schema") or cols.get("schema"))
                        else None
                    )
                    tn = (
                        str((cols.get("table_name") or cols.get("name") or [None])[idx])
                        if (cols.get("table_name") or cols.get("name"))
                        else None
                    )
                    if sc:
                        schemas.append(sc)
                        if tn:
                            tables_by_schema.setdefault(sc, []).append(tn)

        if r.get("frames"):
            for fr in r.get("frames", []):
                try:
                    _ingest_rows(fr)
                except Exception:
                    continue
        elif r.get("series"):
            for s in r.get("series", []):
                try:
                    fr = {
                        "schema": {
                            "fields": [{"name": "table_schema"}, {"name": "table_name"}]
                        },
                        "data": {"values": s.get("values") or []},
                    }
                    _ingest_rows(fr)
                except Exception:
                    continue
        schemas = sorted(list({x for x in schemas if x}))
        for k in list(tables_by_schema.keys()):
            tables_by_schema[k] = sorted(
                list({x for x in tables_by_schema.get(k, []) if x})
            )
        _STATE.setdefault("sql", {}).setdefault("sources", {})[uid] = {
            "schemas": schemas,
            "tables": tables_by_schema,
        }


async def _scan_elasticsearch_values() -> None:
    ds = await list_datasources()
    items = ds.get("items", []) if isinstance(ds, dict) else []
    es_items = [i for i in items if (i.get("type") or "").lower() == "elasticsearch"]
    for it in es_items:
        uid = it.get("uid")
        ds_id = it.get("id")
        detail = await get_datasource_detail(uid)
        jd = (detail.get("jsonData") or {}) if isinstance(detail, dict) else {}
        tf = jd.get("timeField") or "@timestamp"
        q = {
            "queries": [
                {
                    "refId": "A",
                    "intervalMs": 60000,
                    "maxDataPoints": 43200,
                    "datasource": {"uid": uid, "type": "elasticsearch"},
                    "query": {
                        "query": "*",
                        "timeField": tf,
                        "bucketAggs": [
                            {
                                "type": "terms",
                                "id": "2",
                                "field": "_index",
                                "settings": {
                                    "size": 200,
                                    "order": "desc",
                                    "min_doc_count": 1,
                                },
                            }
                        ],
                        "metrics": [{"type": "count", "id": "1"}],
                    },
                }
            ],
            "from_str": "now-5m",
            "to_str": "now",
            "from_ts": int(time.time() * 1000) - 5 * 60 * 1000,
            "to_ts": int(time.time() * 1000),
        }
        try:
            res = await query_ds(q)
        except Exception:
            res = {}
        r = res.get("results", {}).get("A", {})
        indices: List[str] = []

        def _ingest_frame(fr: Dict[str, Any]) -> None:
            nm = (fr.get("schema") or {}).get("name") or ""
            if nm:
                indices.append(str(nm))
            for f in (fr.get("schema") or {}).get("fields") or []:
                labels = (f or {}).get("labels") or {}
                for k in ["term", "_index", "index"]:
                    v = labels.get(k)
                    if v:
                        indices.append(str(v))

        if r.get("frames"):
            for fr in r.get("frames", []):
                try:
                    _ingest_frame(fr)
                except Exception:
                    continue
        elif r.get("series"):
            for s in r.get("series", []):
                nm = s.get("name") or ""
                if nm:
                    indices.append(str(nm))
                labels = (s.get("fields", [{}])[1] or {}).get("labels") or {}
                for k in ["term", "_index", "index"]:
                    v = labels.get(k)
                    if v:
                        indices.append(str(v))
        indices = sorted(list({x for x in indices if x}))
        src = (
            _STATE.setdefault("elasticsearch", {})
            .setdefault("sources", {})
            .setdefault(uid, {})
        )
        src["indices"] = indices
        src["timeField"] = tf
        # field sampling per index (best-effort, limited to first 50 indices)
        fields_map: Dict[str, List[str]] = {}
        dcfg = get_config().discovery
        if isinstance(ds_id, int):
            sem = asyncio.Semaphore(max(dcfg.max_concurrency_elasticsearch, 1))

            async def _sample_index(ix: str) -> None:
                async with sem:
                    try:
                        caps = await proxy_get(ds_id, f"{ix}/_field_caps?fields=*")
                        names: List[str] = []
                        for k, v in (caps.get("fields") or {}).items():
                            names.append(str(k))
                        if names:
                            fields_map[ix] = sorted(list({n for n in names if n}))
                            return
                        mapping = await proxy_get(ds_id, f"{ix}/_mapping")
                        fnames: List[str] = []
                        for _k, v in (mapping or {}).items():
                            try:
                                props = ((v or {}).get("mappings") or {}).get(
                                    "properties"
                                ) or {}
                                for pk in props.keys():
                                    fnames.append(str(pk))
                            except Exception:
                                continue
                        if fnames:
                            fields_map[ix] = sorted(list({n for n in fnames if n}))
                    except Exception:
                        return

            tasks = [_sample_index(ix) for ix in indices[:50]]
            await asyncio.gather(*tasks)
        src["fields"] = fields_map


async def refresh_discovery() -> None:
    _load_state()
    await _scan_prometheus()
    await _scan_prom_metrics_names()
    await _scan_dashboards()
    await _scan_zabbix_values()
    await _scan_sql_values()
    await _scan_elasticsearch_values()
    _STATE["last_scan_ts"] = int(time.time())
    _write_state()


async def run_discovery_loop() -> None:
    dcfg = get_config().discovery
    # Initial delay to avoid impacting cold start
    try:
        await asyncio.sleep(max(dcfg.initial_delay_ms, 0) / 1000.0)
    except Exception:
        await asyncio.sleep(0.5)
    try:
        _load_state()
    except Exception:
        pass
    while True:
        try:
            await refresh_discovery()
        except Exception:
            pass
        # Refresh every 15 minutes
        try:
            interval = int(dcfg.refresh_interval_sec)
            if interval < 60:
                interval = 60
            await asyncio.sleep(interval)
        except Exception:
            await asyncio.sleep(15 * 60)


def get_discovery_state() -> Dict[str, Any]:
    return _STATE


def record_metric_hit(ds_uid: str, metric: str, dimension: str) -> None:
    try:
        prom = _STATE.setdefault("learned", {}).setdefault("prometheus", {})
        zbx = _STATE.setdefault("learned", {}).setdefault("zabbix", {})
        if dimension in ("pod", "host"):
            m = prom.setdefault(ds_uid, {}).setdefault(metric, {"pod": 0, "host": 0})
            m[dimension] = m.get(dimension, 0) + 1
        if dimension in ("netif", "host"):
            m2 = zbx.setdefault(ds_uid, {}).setdefault(metric, {"netif": 0, "host": 0})
            m2[dimension] = m2.get(dimension, 0) + 1
        _write_state()
    except Exception:
        pass


async def _scan_prom_metrics_names() -> None:
    try:
        ds = await list_datasources()
        items = ds.get("items", []) if isinstance(ds, dict) else []
        prom_items = [i for i in items if (i.get("type") or "").lower() == "prometheus"]
        dcfg = get_config().discovery
        sem = asyncio.Semaphore(max(dcfg.max_concurrency_prometheus, 1))

        async def _scan_one(it: Dict[str, Any]) -> None:
            uid = it.get("uid")
            ds_id = it.get("id")
            names: List[str] = []
            async with sem:
                try:
                    if isinstance(ds_id, int):
                        caps = await proxy_get(ds_id, "api/v1/label/__name__/values")
                        for n in (caps or {}).get("data", []):
                            names.append(str(n))
                except Exception:
                    names = names
            names = sorted(list({n for n in names if n}))
            node_like = [n for n in names if n.startswith("node_")]
            container_like = [n for n in names if n.startswith("container_")]
            _STATE.setdefault("prometheus", {}).setdefault(uid, {}).update(
                {
                    "metrics": names,
                    "has_node": bool(node_like),
                    "has_container": bool(container_like),
                }
            )

        await asyncio.gather(*[_scan_one(it) for it in prom_items])
    except Exception:
        pass
