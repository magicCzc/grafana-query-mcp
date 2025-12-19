# Copyright (c) 2025, Project Grafana Query MCP. All rights reserved.

import time
from typing import Dict, List, Optional

from pydantic import BaseModel

from ..config import get_config
from ..discovery import get_discovery_state


class GrafanaQuery(BaseModel):
    queries: List[Dict]
    from_ts: int
    to_ts: int
    from_str: Optional[str] = None
    to_str: Optional[str] = None
    x_dimension: Optional[str] = None


def convert_to_grafana(
    provider: str,
    service: str,
    names: List[str],
    timerange: str,
    metric: str,
    metrics: Optional[List[str]] = None,
    agg: Optional[str] = None,
    datasource_uid: Optional[str] = None,
    expr_override: Optional[str] = None,
    datasource_type: Optional[str] = None,
    datasource_id: Optional[int] = None,
) -> GrafanaQuery:
    cfg = get_config().grafana
    key = f"{provider}.{service}"
    ds_uid = datasource_uid or cfg.datasource_map.get(key, "")
    now = int(time.time() * 1000)
    mins = int(timerange.rstrip("m"))
    frm = now - mins * 60 * 1000
    pattern = "|".join(names)
    t = (datasource_type or "prometheus").lower()

    def _target_dimension() -> str:
        try:
            state = get_discovery_state()
            prom = state.get("prometheus", {})
            learned = (state.get("learned", {}).get("prometheus", {}) or {}).get(
                ds_uid or "", {}
            )
            if isinstance(learned, dict):
                pod_cnt = int((learned.get(metric) or {}).get("pod", 0))
                host_cnt = int((learned.get(metric) or {}).get("host", 0))
                if pod_cnt > host_cnt:
                    return "pod"
                if host_cnt > pod_cnt:
                    return "host"
            hosts = set()
            pods = set()
            for v in prom.values():
                for h in v.get("hosts", []):
                    hosts.add(str(h))
                for p in v.get("pods", []):
                    pods.add(str(p))
            if any(n in pods for n in names):
                return "pod"
            if any(n in hosts for n in names):
                return "host"
        except Exception:
            pass
        return "auto"

    target = _target_dimension()
    if "zabbix" in t:
        group_filter = r"/.*/"
        host_filter = (
            f"/^({'|'.join(names)})$/" if len(names) > 1 else f"/^{names[0]}$/"
        )
        state = get_discovery_state()
        zbx = state.get("zabbix", {})
        pats = zbx.get("patterns") or {}
        host_pats = pats.get("host") or []
        netif_pats = pats.get("netif") or []

        def _is_net(m: str) -> bool:
            return m in (
                "network_rx",
                "net_rx",
                "network_in",
                "network_tx",
                "net_tx",
                "network_out",
            )

        learned_z = (state.get("learned", {}).get("zabbix", {}) or {}).get(
            ds_uid or "", {}
        )
        net_hits = int((learned_z.get(metric) or {}).get("netif", 0))
        host_hits = int((learned_z.get(metric) or {}).get("host", 0))
        if net_hits > host_hits:
            z_dim = "netif"
        elif host_hits > net_hits:
            z_dim = "host"
        else:
            z_dim = (
                "netif"
                if (metrics and any(_is_net(mm) for mm in metrics)) or _is_net(metric)
                else "host"
            )

        def _build_query_for_metric(m: str, ref: str) -> Dict:
            if m == "cpu":
                item_filter = "Processor load (1 min average all core)"
                application_filter = "CPU"
            elif m in ("cpu_pct", "cpu_usage_percent", "cpu_used_percent"):
                item_filter = r"/(cpu used percent|CPU used percent)/"
                application_filter = "CPU"
            elif m == "memory":
                item_filter = r"/(Used memory|Memory used|Memory utilization)/"
                application_filter = "Memory"
            elif m in (
                "memory_pct",
                "mem_pct",
                "memory_usage_percent",
                "memory_used_percent",
            ):
                item_filter = r"/(Used memory percent|Memory utilization|Used memory)/"
                application_filter = "Memory"
            elif m in ("disk_used_pct", "filesystem_used_pct"):
                # Match common mounts; Zabbix supports regex filters
                item_filter = r"/(Used disk space on / \(percentage\)|Used disk space on /data \(percentage\)|Used disk space on /u01 \(percentage\))/"
                application_filter = "Filesystem"
            elif m in ("network_rx", "net_rx", "network_in"):
                rx_pat = None
                for p in netif_pats:
                    if "Incoming" in str(p):
                        rx_pat = p
                        break
                item_filter = (
                    rx_pat or r"/Incoming network traffic on (eth.*|ens.*|eno.*)/"
                )
                application_filter = "Network interfaces"
            elif m in ("network_tx", "net_tx", "network_out"):
                tx_pat = None
                for p in netif_pats:
                    if "Outgoing" in str(p):
                        tx_pat = p
                        break
                item_filter = (
                    tx_pat or r"/Outgoing network traffic on (eth.*|ens.*|eno.*)/"
                )
                application_filter = "Network interfaces"
            else:
                item_filter = m
                application_filter = ""
            q = {
                "refId": ref,
                "intervalMs": 10000,
                "maxDataPoints": 100,
                "intervalFactor": 1,
                "datasource": {
                    "uid": ds_uid,
                    "type": "alexanderzobnin-zabbix-datasource",
                },
                "format": "time_series",
                "resultFormat": "time_series",
                "expr": "",
                "queryType": "0",
                "group": {"filter": group_filter},
                "host": {"filter": host_filter},
                "application": {"filter": application_filter},
                "item": {"filter": item_filter},
                "options": {"showDisabledItems": False, "skipEmptyValues": False},
                "table": {"skipEmptyValues": False},
                "triggers": {"acknowledged": 2, "count": True, "minSeverity": 3},
            }
            if datasource_id is not None:
                q["datasourceId"] = datasource_id
            if m == "cpu":
                q["itemTag"] = {"filter": "Application: CPU"}
            elif m in (
                "memory",
                "memory_pct",
                "mem_pct",
                "memory_usage_percent",
                "memory_used_percent",
            ):
                pass
            elif m in (
                "network_rx",
                "net_rx",
                "network_in",
                "network_tx",
                "net_tx",
                "network_out",
            ):
                q["itemTag"] = {"filter": "Application: Network interfaces"}
            return q

        ref_ids = [chr(ord("A") + i) for i in range(26)]
        qs: List[Dict] = []
        if metrics:
            for idx, m in enumerate(metrics):
                ref = ref_ids[idx % len(ref_ids)]
                qs.append(_build_query_for_metric(m, ref))
        else:
            qs.append(_build_query_for_metric(metric, "A"))
        target = z_dim
    else:
        def _generate_expr_for_metric(m: str, override_expr: Optional[str] = None) -> tuple[str, Optional[str]]:
            base_metric: Optional[str] = None
            if override_expr:
                expr = override_expr
            elif m == "cpu":
                expr = (
                    f'sum by (instance)(rate(node_cpu_seconds_total{{mode!="idle"}}[{mins}m])) '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m == "memory":
                expr = (
                    f"sum by (instance)((node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes)) "
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("cpu_pct", "cpu_usage_percent", "cpu_used_percent"):
                expr = (
                    f"sum by (instance)(100 * sum by (instance)(rate(node_cpu_seconds_total{{mode!='idle'}}[{mins}m])) / sum by (instance)(rate(node_cpu_seconds_total[{mins}m]))) "
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("memory_pct", "mem_pct", "memory_usage_percent"):
                expr = (
                    f"sum by (instance)(100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))) "
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("network_rx", "net_rx", "network_in"):
                expr = (
                    f'sum by (instance)(rate(node_network_receive_bytes_total{{device!~"^(lo|docker.*|veth.*|br.*|flannel.*)$"}}[{mins}m])) '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("network_tx", "net_tx", "network_out"):
                expr = (
                    f'sum by (instance)(rate(node_network_transmit_bytes_total{{device!~"^(lo|docker.*|veth.*|br.*|flannel.*)$"}}[{mins}m])) '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("net_packets_rx", "network_packets_rx"):
                expr = (
                    f'sum by (instance)(rate(node_network_receive_packets_total{{device!~"^(lo|docker.*|veth.*|br.*|flannel.*)$"}}[{mins}m])) '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("net_packets_tx", "network_packets_tx"):
                expr = (
                    f'sum by (instance)(rate(node_network_transmit_packets_total{{device!~"^(lo|docker.*|veth.*|br.*|flannel.*)$"}}[{mins}m])) '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("net_errors_rx", "network_errors_rx"):
                expr = (
                    f'sum by (instance)(rate(node_network_receive_errs_total{{device!~"^(lo|docker.*|veth.*|br.*|flannel.*)$"}}[{mins}m])) '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("net_errors_tx", "network_errors_tx"):
                expr = (
                    f'sum by (instance)(rate(node_network_transmit_errs_total{{device!~"^(lo|docker.*|veth.*|br.*|flannel.*)$"}}[{mins}m])) '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("disk_read", "io_read"):
                expr = (
                    f'sum by (instance)(rate(node_disk_read_bytes_total{{device!~"^(ram.*|loop.*|dm-.*)$"}}[{mins}m])) '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("disk_write", "io_write"):
                expr = (
                    f'sum by (instance)(rate(node_disk_written_bytes_total{{device!~"^(ram.*|loop.*|dm-.*)$"}}[{mins}m])) '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("disk_iops_read", "iops_read"):
                expr = (
                    f'sum by (instance)(rate(node_disk_reads_completed_total{{device!~"^(ram.*|loop.*|dm-.*)$"}}[{mins}m])) '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("disk_iops_write", "iops_write"):
                expr = (
                    f'sum by (instance)(rate(node_disk_writes_completed_total{{device!~"^(ram.*|loop.*|dm-.*)$"}}[{mins}m])) '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("disk_util", "io_util"):
                expr = (
                    f'sum by (instance)(rate(node_disk_io_time_seconds_total{{device!~"^(ram.*|loop.*|dm-.*)$"}}[{mins}m])) * 100 '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("fs_used", "filesystem_used"):
                expr = (
                    f'sum by (instance)((node_filesystem_size_bytes{{fstype!~"(tmpfs|devtmpfs)"}} - node_filesystem_avail_bytes{{fstype!~"(tmpfs|devtmpfs)"}})) '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("fs_used_pct", "filesystem_used_pct", "disk_used_pct"):
                expr = (
                    f'sum by (instance)(100 * (1 - (node_filesystem_avail_bytes{{fstype!~"(tmpfs|devtmpfs)"}} / node_filesystem_size_bytes{{fstype!~"(tmpfs|devtmpfs)"}}))) '
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("load1", "load_1m"):
                expr = f'node_load1 and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
            elif m in ("load5", "load_5m"):
                expr = f'node_load5 and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
            elif m in ("load15", "load_15m"):
                expr = f'node_load15 and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
            elif m in ("uptime", "uptime_seconds"):
                expr = f'(time() - node_boot_time_seconds) and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
            elif m in ("processes_running",):
                expr = f'node_processes_running and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
            elif m in ("processes_blocked",):
                expr = f'node_processes_blocked and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
            elif m in ("context_switches",):
                expr = f'sum by (instance)(rate(node_context_switches_total[{mins}m])) and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
            elif m in ("interrupts",):
                expr = f'sum by (instance)(rate(node_intr_total[{mins}m])) and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
            elif m in ("container_cpu",):
                expr = f'sum by (pod)(rate(container_cpu_usage_seconds_total{{pod=~"{pattern}"}}[{mins}m]))'
            elif m in ("container_memory",):
                expr = f'sum by (pod)(container_memory_working_set_bytes{{pod=~"{pattern}"}})'
            elif m in ("container_network_rx",):
                expr = f'sum by (pod)(rate(container_network_receive_bytes_total{{pod=~"{pattern}"}}[{mins}m]))'
            elif m in ("container_network_tx",):
                expr = f'sum by (pod)(rate(container_network_transmit_bytes_total{{pod=~"{pattern}"}}[{mins}m]))'
            elif m in ("container_restarts", "pod_restarts"):
                expr = f'sum by (pod)(increase(kube_pod_container_status_restarts_total{{pod=~"{pattern}"}}[{mins}m]))'
            elif m in ("mysql_qps",):
                if target == "pod":
                    expr = f"sum by (pod)(rate(mysql_global_status_queries[{mins}m]))"
                else:
                    expr = (
                        f"sum by (instance)(rate(mysql_global_status_queries[{mins}m])) "
                        f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                    )
            elif m in ("mysql_connections",):
                if target == "pod":
                    expr = f"sum by (pod)(mysql_global_status_threads_connected)"
                else:
                    expr = (
                        f"sum by (instance)(mysql_global_status_threads_connected) "
                        f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                    )
            elif m in ("mysql_innodb_buffer_bytes", "mysql_buffer_bytes"):
                if target == "pod":
                    expr = f"sum by (pod)(mysql_global_status_innodb_buffer_pool_bytes_data)"
                else:
                    expr = (
                        f"sum by (instance)(mysql_global_status_innodb_buffer_pool_bytes_data) "
                        f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                    )
            elif m in ("es_http_requests", "elasticsearch_http_requests"):
                if target == "pod":
                    expr = f"sum by (pod)(rate(elasticsearch_http_total[{mins}m]))"
                else:
                    expr = (
                        f"sum by (instance)(rate(elasticsearch_http_total[{mins}m])) "
                        f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                    )
            elif m in ("es_heap_used", "elasticsearch_heap_used"):
                if target == "pod":
                    expr = f'sum by (pod)(elasticsearch_jvm_memory_bytes_used{{area="heap"}})'
                else:
                    expr = (
                        f'sum by (instance)(elasticsearch_jvm_memory_bytes_used{{area="heap"}}) '
                        f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                    )
            elif m in ("nginx_requests",):
                expr = (
                    f"sum by (instance)(rate(nginx_http_requests_total[{mins}m])) "
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("nginx_active_connections", "nginx_conn_active"):
                expr = (
                    f"sum by (instance)(nginx_connections_active) "
                    f'and on (instance) node_uname_info{{nodename=~"{pattern}"}}'
                )
            elif m in ("jvm_heap_used",):
                expr = f'sum by (pod)(jvm_memory_used_bytes{{area="heap",pod=~"{pattern}"}})'
            elif m in ("jvm_gc_pause",):
                expr = f'sum by (pod)(rate(jvm_gc_pause_seconds_sum{{pod=~"{pattern}"}}[{mins}m]))'
            elif m in ("jvm_threads", "jvm_threads_current"):
                expr = f'sum by (pod)(jvm_threads_current{{pod=~"{pattern}"}})'
            elif m in ("rocketmq_put", "rocketmq_broker_put_total"):
                expr = f'sum by (pod)(rate(rocketmq_broker_put_message_total{{pod=~"{pattern}"}}[{mins}m]))'
            elif m in ("rocketmq_consume_lag", "rocketmq_lag"):
                expr = f'sum by (pod)(rocketmq_consumer_lag{{pod=~"{pattern}"}})'
            else:
                base = f'metric_value{{name=~"{pattern}"}}'
                base_metric = base
                if agg in {"avg", "max", "min", "sum"}:
                    expr = f"{agg}({base})"
                else:
                    expr = base
            return expr, base_metric

        ref_ids = [chr(ord("A") + i) for i in range(26)]
        if metrics:
            qs = []
            for idx, m in enumerate(metrics):
                expr, base_metric = _generate_expr_for_metric(m, expr_override)
                ref = ref_ids[idx % len(ref_ids)]
                qs.append({
                    "refId": ref,
                    "intervalMs": 60000,
                    "maxDataPoints": 43200,
                    "datasource": {"uid": ds_uid, "type": "prometheus"},
                    "expr": expr,
                    "format": "time_series",
                })
        else:
            expr, base_metric = _generate_expr_for_metric(metric, expr_override)
            if base_metric and not agg:
                aggs = ["avg", "max", "min", "sum"]
                qs = [
                    {
                        "refId": ref_ids[i],
                        "intervalMs": 60000,
                        "maxDataPoints": 43200,
                        "datasource": {"uid": ds_uid, "type": "prometheus"},
                        "expr": f"{a}({base_metric})",
                        "format": "time_series",
                    }
                    for i, a in enumerate(aggs)
                ]
            else:
                qs = [
                    {
                        "refId": "A",
                        "intervalMs": 60000,
                        "maxDataPoints": 43200,
                        "datasource": {"uid": ds_uid, "type": "prometheus"},
                        "expr": expr,
                        "format": "time_series",
                    }
                ]
    from_str = f"now-{mins}m"
    to_str = "now"
    return GrafanaQuery(
        queries=qs,
        from_ts=frm,
        to_ts=now,
        from_str=from_str,
        to_str=to_str,
        x_dimension=target,
    )
