<!-- Copyright (c) 2025, Project Grafana Query MCP. All rights reserved. -->

# 数据模型设计文档（替代数据库设计）

本项目不引入内部关系型数据库，使用外部数据源（Grafana/Prometheus/Zabbix/SQL/Elasticsearch）。内部仅持久化 `cache/discovery.json` 做状态缓存。

## 数据模型概览
- `datasources.items`: 远端数据源列表
- `prometheus[uid]`: 主机与 Pod 集合、指标名采样、特征标识
- `dashboards[]`: Dashboard 元数据（变量、数据源类型）
- `zabbix.sources[uid]`: 主机与网卡采样
- `sql.sources[uid]`: `schemas` 与 `tables`
- `elasticsearch.sources[uid]`: `indices`、`timeField`、`fields`
- `learned.prometheus[ds_uid][metric]`: 命中统计（pod/host）

## 数据模型图（Mermaid ER）
```mermaid
erDiagram
  DATASOURCE ||--o{ PROM_SOURCES : has
  DATASOURCE ||--o{ ZBX_SOURCES : has
  DATASOURCE ||--o{ SQL_SOURCES : has
  DATASOURCE ||--o{ ES_SOURCES  : has
  DASHBOARD  }o--o{ DATASOURCE  : uses

  DATASOURCE {
    string uid
    string type
    string name
    number id
  }
  PROM_SOURCES {
    string uid
    string[] hosts
    string[] pods
    string[] deployments
    string[] metrics
    bool has_node
    bool has_container
  }
  ZBX_SOURCES {
    string uid
    string[] hosts
    string[] netifs
  }
  SQL_SOURCES {
    string uid
    string[] schemas
    map<string, string[]> tables
  }
  ES_SOURCES {
    string uid
    string timeField
    string[] indices
    map<string, string[]> fields
  }
  DASHBOARD {
    string uid
    string title
    map vars
    string[] types
  }
```

## 示例片段（`discovery.json`）
```json
{
  "datasources":{"items":[{"uid":"abc","type":"prometheus","name":"Prom","id":1}]},
  "prometheus":{"abc":{"hosts":["node-1"],"metrics":["node_cpu_seconds_total"],"has_node":true}},
  "dashboards":[{"uid":"UOJjh1SMz","title":"SLS JVM监控大盘","types":["prometheus"]}],
  "learned":{"prometheus":{"abc":{"cpu":{"pod":3,"host":1}}}}
}
```

## 版本变更记录
- v0.1.0：明确不使用内部数据库；提供 ER 风格数据模型与 JSON 示例
