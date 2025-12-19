<!-- Copyright (c) 2025, Project Grafana Query MCP. All rights reserved. -->

# API 接口文档

本项目提供基于 FastAPI 的查询转换与分页传输服务，整合 Grafana 数据源并输出统一查询结构。

## 认证机制

### 认证方式
- **Header 认证**：使用 Bearer Token 进行认证
- **Header 格式**：`Authorization: Bearer <APP_TOKEN>`
- **APP_TOKEN**：在 `.env` 文件中配置的服务器 API 令牌

### 频率限制
- 每令牌每分钟请求次数限制：`APP_RATE_LIMIT_PER_MIN`（在 `.env` 文件中配置，默认 60）
- 超过限制将返回 429 状态码

## 统一响应格式

### 成功响应
```json
{
  "status": "status.ok",
  "pagination": { /* 分页信息，如存在 */ },
  "data": { /* 响应数据 */ }
}
```

### 错误响应
```json
{
  "status": "status.error",
  "error_code": "error.*" /* 错误代码 */
}
```

## API 端点列表

### 1. Discovery 相关接口

#### 获取发现状态
- **URL**: `GET /v1/discovery/state`
- **功能**: 返回当前发现状态，包括数据源、Dashboards、Prometheus/Elasticsearch 指标采样等信息
- **响应示例**:
  ```json
  {
    "status": "status.ok",
    "data": {
      "datasources": {
        "items": [
          {
            "uid": "prom-123",
            "type": "prometheus",
            "name": "Prometheus-1"
          },
          {
            "uid": "es-456",
            "type": "elasticsearch",
            "name": "Elasticsearch-1"
          }
        ]
      },
      "dashboards": {
        "count": 15
      },
      "last_scan_ts": 1730000000
    }
  }
  ```

### 2. Grafana 相关接口

#### Grafana 健康检查
- **URL**: `GET /v1/grafana/health`
- **功能**: 检查与 Grafana 实例的连接状态
- **响应**: 返回 Grafana `/api/health` 接口的原始响应

#### 获取远程数据源列表
- **URL**: `GET /v1/grafana/datasources_remote`
- **功能**: 获取 Grafana 实例中的数据源列表
- **响应示例**:
  ```json
  {
    "status": "status.ok",
    "data": {
      "items": [
        {
          "name": "Prometheus-1",
          "type": "prometheus",
          "uid": "prom-123",
          "id": 1
        },
        {
          "name": "Elasticsearch-1",
          "type": "elasticsearch",
          "uid": "es-456",
          "id": 2
        }
      ]
    }
  }
  ```

#### Grafana 原始查询代理
- **URL**: `POST /v1/grafana/raw`
- **功能**: 直接代理到 Grafana 的 `/api/ds/query` 接口，支持原始 Grafana 查询
- **请求参数**:
  | 参数名 | 类型 | 描述 |
  |--------|------|------|
  | queries | Array | Grafana 查询对象数组 |
  | from_str | String | 起始时间字符串（如 "now-5m"） |
  | to_str | String | 结束时间字符串（如 "now"） |
  | from_ts | Number | 起始时间戳（可选，与 from_str 二选一） |
  | to_ts | Number | 结束时间戳（可选，与 to_str 二选一） |

- **请求示例**:
  ```json
  {
    "queries": [
      {
        "refId": "A",
        "datasource": { "uid": "prom-123", "type": "prometheus" },
        "expr": "up"
      }
    ],
    "from_str": "now-5m",
    "to_str": "now"
  }
  ```

- **响应**: 返回 Grafana 风格的原始查询结果 JSON

### 3. 查询转换接口

#### 业务参数转 Grafana 查询
- **URL**: `POST /v1/grafana/convert`
- **功能**: 将业务参数转换为 Grafana 数据源可执行的查询语句
- **请求参数**:
  | 参数名 | 类型 | 描述 | 示例 |
  |--------|------|------|------|
  | parsed.provider | String | 云服务提供商 | "aws", "huawei" |
  | parsed.service | String | 服务类型 | "ec2", "microservice" |
  | parsed.names | Array | 实例名称列表 | ["instance-1", "instance-2"] |
  | parsed.range | String | 时间范围 | "5m", "1h", "24h" |
  | parsed.metric | String | 指标名称 | "cpu", "memory" |
  | parsed.agg | String | 聚合函数 | "avg", "max", "min" |
  | parsed.datasource_uid | String | 数据源 UID（可选） | "prom-123" |

- **请求示例**:
  ```json
  {
    "parsed": {
      "provider": "huawei",
      "service": "microservice",
      "names": ["center-sca-w101"],
      "range": "5m",
      "metric": "cpu",
      "agg": "avg"
    }
  }
  ```

- **响应示例**:
  ```json
  {
    "status": "status.ok",
    "data": {
      "queries": [
        {
          "refId": "A",
          "datasource": { "uid": "prom-123", "type": "prometheus" },
          "expr": "avg(rate(cpu_usage_seconds_total{instance='center-sca-w101'}[5m])) by (instance) * 100",
          "interval": "",
          "maxDataPoints": 100
        }
      ],
      "from": "1630000000000",
      "to": "1630000300000"
    }
  }
  ```

### 4. 查询执行接口

#### 开始查询（SSE 流式返回）
- **URL**: `POST /v1/query/start`
- **功能**: 执行查询并通过 Server-Sent Events (SSE) 流式返回结果
- **请求参数**:
  | 参数名 | 类型 | 描述 | 默认值 |
  |--------|------|------|--------|
  | parsed | Object | 业务参数对象（同查询转换接口） | - |
  | page_size | Number | 每页数据大小 | 100 |

- **请求示例**:
  ```json
  {
    "parsed": {
      "provider": "aws",
      "service": "ec2",
      "names": ["instance-1"],
      "range": "1h",
      "metric": "cpu_utilization",
      "agg": "avg"
    },
    "page_size": 100
  }
  ```

- **响应**: SSE 事件流，包含以下事件类型:
  - `event:meta`: 元数据，包含分页信息
    ```
    event:meta
data: {"pagination_id":"abc123","total_pages":5}
    ```
  - `event:page`: 分页数据
    ```
    event:page
data: {"index":0,"series_count":100,"data":[/* 数据内容 */]}
    ```
  - `event:done`: 查询完成
    ```
    event:done
data: {"status":"status.ok"}
    ```

#### 获取指定页查询结果
- **URL**: `GET /v1/query/page/{pagination_id}/{index}`
- **功能**: 获取指定分页 ID 和页码的查询结果
- **路径参数**:
  | 参数名 | 类型 | 描述 |
  |--------|------|------|
  | pagination_id | String | 分页会话 ID |
  | index | Number | 页码索引（从 0 开始） |

- **响应示例**:
  ```json
  {
    "status": "status.ok",
    "pagination": {
      "pagination_id": "abc123",
      "current_page": 0,
      "total_pages": 5,
      "page_size": 100
    },
    "data": [/* 查询结果数据 */]
  }
  ```

#### 同步执行查询（非 SSE）
- **URL**: `POST /v1/query/execute`
- **功能**: 执行查询并同步返回指定页的结果（适用于不支持 SSE 的客户端）
- **请求参数**:
  | 参数名 | 类型 | 描述 | 默认值 |
  |--------|------|------|--------|
  | parsed | Object | 业务参数对象（同查询转换接口） | - |
  | page_size | Number | 每页数据大小 | 100 |
  | page_index | Number | 页码索引（从 0 开始） | 0 |

- **请求示例**:
  ```json
  {
    "parsed": {
      "provider": "aws",
      "service": "ec2",
      "names": ["instance-1"],
      "range": "1h",
      "metric": "cpu_utilization",
      "agg": "avg"
    },
    "page_size": 100,
    "page_index": 0
  }
  ```

- **响应示例**:
  ```json
  {
    "status": "status.ok",
    "pagination": {
      "pagination_id": "abc123",
      "current_page": 0,
      "total_pages": 5,
      "page_size": 100
    },
    "data": [/* 查询结果数据 */]
  }
  ```

### 5. 异常检测接口

#### 检测异常实例
- **URL**: `POST /v1/anomaly/instances`
- **功能**: 自动检测所有资产的异常实例，基于历史分位数动态阈值判断
- **请求参数**:
  | 参数名 | 类型 | 描述 | 默认值 |
  |--------|------|------|--------|
  | range | String | 时间范围 | "1h" |
  | pctl | Number | 分位数阈值（90/95/99） | 95 |
  | include_normal | Boolean | 是否包含正常指标 | false |

- **请求示例**:
  ```json
  {
    "range": "5m",
    "pctl": 95,
    "include_normal": true
  }
  ```

- **响应示例**:
  ```json
  {
    "status": "status.ok",
    "data": {
      "items": [
        {
          "name": "192.168.1.100:9100",
          "datasource_uid": "prom-123",
          "metrics": {
            "cpu_pct": {
              "current": 86.4,
              "threshold": 83.1,
              "exceed": true
            },
            "disk_util_pct": {
              "current": 91.2,
              "threshold": 84.7,
              "exceed": true
            }
          }
        },
        {
          "name": "zabbix-host-01",
          "datasource_uid": "zbx-456",
          "metrics": {
            "memory_pct": {
              "current": 82.1,
              "threshold": 79.5,
              "exceed": true
            }
          }
        }
      ],
      "count": 2
    }
  }
  ```

- **异常指标说明**:
  - **CPU 相关**: `cpu_pct`, `iowait_pct`
  - **内存相关**: `memory_pct`
  - **磁盘相关**: `disk_used_pct`, `disk_util_pct`
  - **系统负载**: `load1`, `load5`, `load15`
  - **网络相关**: `net_errors_rx`, `net_errors_tx`
  - **进程相关**: `processes_running`, `processes_blocked`
  - **系统相关**: `context_switches`, `interrupts`
  - **Zabbix 特定**: `network_traffic_anomaly`

- **阈值机制**:
  - **动态阈值**: 按实例与指标的历史序列计算分位阈值
  - **静态回退阈值**: 当历史数据不足时，使用默认阈值（CPU>70%、内存>80%、磁盘>80%）

### 6. 监控指标接口

#### 获取 Prometheus 指标
- **URL**: `GET /metrics`
- **功能**: 暴露 Prometheus 监控指标
- **响应**: Prometheus 格式的指标数据
- **无需认证**

## 错误代码列表

| 错误代码 | 描述 | HTTP 状态码 |
|----------|------|-------------|
| error.invalid_input | 输入参数无效 | 400 |
| error.unauthorized | 认证失败 | 401 |
| error.forbidden | 权限不足 | 403 |
| error.rate_limit | 请求频率超过限制 | 429 |
| error.grafana_connection | Grafana 连接失败 | 500 |
| error.internal | 内部服务器错误 | 500 |

## 使用示例

### 1. 使用 curl 调用健康检查接口
```bash
curl -H "Authorization: Bearer your-app-token" http://127.0.0.1:8080/v1/grafana/health
```

### 2. 使用 Python 调用查询转换接口
```python
import requests
import json

url = "http://127.0.0.1:8080/v1/grafana/convert"
headers = {
    "Authorization": "Bearer your-app-token",
    "Content-Type": "application/json"
}
payload = {
    "parsed": {
        "provider": "aws",
        "service": "ec2",
        "names": ["instance-1"],
        "range": "1h",
        "metric": "cpu_utilization",
        "agg": "avg"
    }
}

response = requests.post(url, headers=headers, data=json.dumps(payload))
print(response.json())
```

### 3. 使用 JavaScript 接收 SSE 流式数据
```javascript
const source = new EventSource('http://127.0.0.1:8080/v1/query/start', {
  headers: {
    'Authorization': 'Bearer your-app-token',
    'Content-Type': 'application/json'
  },
  withCredentials: true
});

source.addEventListener('meta', (event) => {
  const meta = JSON.parse(event.data);
  console.log('分页信息:', meta);
});

source.addEventListener('page', (event) => {
  const page = JSON.parse(event.data);
  console.log('第', page.index, '页数据:', page.data);
});

source.addEventListener('done', (event) => {
  console.log('查询完成:', event.data);
  source.close();
});

source.addEventListener('error', (error) => {
  console.error('SSE 错误:', error);
  source.close();
});
```

## API 版本控制

当前 API 版本为 v1，版本号包含在 URL 路径中（如 `/v1/grafana/health`）。未来版本升级将使用新的版本号（如 v2），并保持向后兼容一段时间。
- `GET /v1/query/page/{pagination_id}/{index}` → 返回指定页内容
- `POST /v1/query/execute` → 同步返回指定页内容（非 SSE 客户端）
  - 多指标入参示例：
    ```json
    {
      "parsed": {
        "provider": "huawei",
        "service": "microservice",
        "names": ["center-sca-w101", "center-sca-w102"],
        "range": "15m",
        "metric": "cpu",
        "metrics": ["cpu", "memory", "disk_used_pct", "network_rx", "network_tx"],
        "agg": "avg"
      },
      "page_size": 300,
      "page_index": 0
    }
    ```

## 错误码
- `error.auth.missing_bearer`、`error.auth.invalid_token`
- `error.config.missing_grafana`、`error.grafana.bad_status`
- `error.datasource.no_data`
- `error.pagination.not_found`、`error.pagination.bad_index`

## 版本变更记录
- v0.1.0：整理标准接口清单、示例与错误码说明
