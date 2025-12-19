<!-- Copyright (c) 2025, Project Grafana Query MCP. All rights reserved. -->

# 部署与运维指南

## 环境变量

- 统一仅使用 `.env` 文件或平台环境注入，示例字段：
  - `APP_TOKEN`：API 访问令牌
  - `APP_ENCRYPT_KEY`：缓存加密密钥（可选）
  - `APP_RATE_LIMIT_PER_MIN`：每分钟限流（默认 60）
  - `GRAFANA_URL`：Grafana 基地址，如 `https://grafana.example.com`
  - `GRAFANA_TOKEN` 或 `GRAFANA_API_KEY`：Grafana API 令牌（任选其一）
  - `DATASOURCE_MAP`：数据源映射，如 `huawei.microservice:DS_UID`
  - `GRAFANA_RANGE_STYLE`：`epoch | relative`
  - `GRAFANA_TIMEOUT_MS`：HTTP 超时毫秒
  - Discovery：`DISCOVERY_ENABLED`、`DISCOVERY_REFRESH_INTERVAL_SEC`、`DISCOVERY_INITIAL_DELAY_MS`
  - Discovery 并发：`DISCOVERY_MAX_CONCURRENCY_DASHBOARDS`、`DISCOVERY_MAX_CONCURRENCY_ELASTICSEARCH`、`DISCOVERY_MAX_CONCURRENCY_PROMETHEUS`

## 运行

- 使用 `uvicorn app.main:app --host 0.0.0.0 --port 8080`

## 监控与日志

- 访问 `/metrics` 获取 Prometheus 指标
- 标准输出记录 JSON 日志（可根据平台接入）

## 安全与性能

- 启用 HTTPS 与反向代理
- 设置合理的限流与令牌轮换
- 预热数据源映射以降低冷启动
- Discovery 节流：在大规模集群降低并发与提高刷新间隔，避免后端压力

## 示例 `.env`

```ini
APP_TOKEN=replace-with-your-api-token
GRAFANA_URL=https://grafana.example.com
GRAFANA_TOKEN=replace-with-grafana-api-token
DISCOVERY_ENABLED=true
DISCOVERY_REFRESH_INTERVAL_SEC=900
DISCOVERY_INITIAL_DELAY_MS=500
DISCOVERY_MAX_CONCURRENCY_DASHBOARDS=8
DISCOVERY_MAX_CONCURRENCY_ELASTICSEARCH=4
DISCOVERY_MAX_CONCURRENCY_PROMETHEUS=4
```

## 版本变更记录

- v0.1.0：统一环境加载为 `.env`，补充 Discovery 节流项
