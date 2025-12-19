# Copyright (c) 2025, Project Grafana Query MCP. All rights reserved.

import os
from typing import Dict, Optional

from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel, Field


class SecurityConfig(BaseModel):
    app_token: str = Field(default="", description="API token for auth")
    encrypt_key: str = Field(default="", description="Key for sensitive encryption")
    rate_limit_per_minute: int = Field(default=60, description="Requests per minute")


class GrafanaConfig(BaseModel):
    base_url: str = Field(default="", description="Grafana base URL")
    token: str = Field(default="", description="Grafana API token")
    datasource_map: Dict[str, str] = Field(
        default_factory=dict,
        description="Mapping 'provider.service' -> datasource UID",
    )
    range_style: str = Field(default="epoch", description="'epoch' or 'relative'")
    timeout_ms: int = Field(default=30000, description="HTTP client timeout ms")


class DiscoveryConfig(BaseModel):
    enabled: bool = Field(
        default=True, description="Enable discovery background scanning"
    )
    refresh_interval_sec: int = Field(
        default=900, description="Discovery refresh interval seconds"
    )
    initial_delay_ms: int = Field(
        default=500, description="Initial delay before first discovery run (ms)"
    )
    max_concurrency_dashboards: int = Field(
        default=8, description="Max concurrent dashboard detail requests"
    )
    max_concurrency_elasticsearch: int = Field(
        default=4, description="Max concurrent Elasticsearch field sampling"
    )
    max_concurrency_prometheus: int = Field(
        default=4, description="Max concurrent Prometheus scans"
    )


class AppConfig(BaseModel):
    security: SecurityConfig
    grafana: GrafanaConfig
    discovery: DiscoveryConfig


def load_config() -> AppConfig:
    try:
        load_dotenv(find_dotenv(filename=".env"), override=False)
    except Exception:
        pass
    security = SecurityConfig(
        app_token=os.getenv("APP_TOKEN", ""),
        encrypt_key=os.getenv("APP_ENCRYPT_KEY", ""),
        rate_limit_per_minute=int(os.getenv("APP_RATE_LIMIT_PER_MIN", "60")),
    )
    ds_map_raw = os.getenv("DATASOURCE_MAP", "")
    ds_map: Dict[str, str] = {}
    if ds_map_raw:
        for item in ds_map_raw.split(","):
            k, v = item.split(":", 1)
            ds_map[k.strip()] = v.strip()
    token = os.getenv("GRAFANA_TOKEN") or os.getenv("GRAFANA_API_KEY") or ""
    grafana = GrafanaConfig(
        base_url=os.getenv("GRAFANA_URL", ""),
        token=token,
        datasource_map=ds_map,
        range_style=os.getenv("GRAFANA_RANGE_STYLE", "epoch"),
        timeout_ms=int(os.getenv("GRAFANA_TIMEOUT_MS", "30000")),
    )
    discovery = DiscoveryConfig(
        enabled=os.getenv("DISCOVERY_ENABLED", "true").lower()
        in {"1", "true", "yes", "on"},
        refresh_interval_sec=int(os.getenv("DISCOVERY_REFRESH_INTERVAL_SEC", "900")),
        initial_delay_ms=int(os.getenv("DISCOVERY_INITIAL_DELAY_MS", "500")),
        max_concurrency_dashboards=int(
            os.getenv("DISCOVERY_MAX_CONCURRENCY_DASHBOARDS", "8")
        ),
        max_concurrency_elasticsearch=int(
            os.getenv("DISCOVERY_MAX_CONCURRENCY_ELASTICSEARCH", "4")
        ),
        max_concurrency_prometheus=int(
            os.getenv("DISCOVERY_MAX_CONCURRENCY_PROMETHEUS", "4")
        ),
    )
    return AppConfig(security=security, grafana=grafana, discovery=discovery)


CONFIG: Optional[AppConfig] = None


def get_config() -> AppConfig:
    global CONFIG
    if CONFIG is None:
        CONFIG = load_config()
    return CONFIG
