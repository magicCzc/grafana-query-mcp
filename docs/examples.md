<!-- Copyright (c) 2025, Project Grafana Query MCP. All rights reserved. -->

# 使用示例

## 输入示例

```
"获取华为云微服务的center-sca-w101和center-sca-w102最近5分钟的cpu使用情况"
```

## 查询流程（由 Dify 提供 parsed 参数）

1. 上游 Dify 生成 `parsed`：`{ provider, service, names, range, metric, agg?, datasource_uid }`
2. `POST /v1/grafana/convert` 获取 Grafana 查询参数（可选调试）
3. `POST /v1/query/execute` 直接获取分页数据，或 `POST /v1/query/start` + `GET /v1/query/page/...` 走 SSE 分片
