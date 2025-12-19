<!-- Copyright (c) 2025, Project Grafana Query MCP. All rights reserved. -->

# Grafana Query MCP

## 项目概述
Grafana Query MCP 是一个基于 FastAPI 的查询转换与分页服务，面向中文自然语言场景，将上游解析后的业务参数转换为 Grafana 数据源可执行的查询，并通过 SSE/分页方式高效返回结果。系统内置 Discovery 后台扫描，自动采集数据源、Dashboards、Prometheus 指标名与 Elasticsearch 字段等信息，用于增强自动选择数据源与表达式生成的命中率，同时提供统一认证、速率限制与观测性（Prometheus 指标、结构化日志）。该服务适用于需要在现有 Grafana 生态中以编程方式构建与执行查询的应用和 Agent 框架。

## 功能特性

### 核心功能
- **查询转换**：将 `{provider, service, names, range, metric, agg}` 等业务参数转换为 Grafana 数据源可执行的查询语句
- **异常检测**：基于动态分位阈值与静态回退策略，自动巡检 CPU/内存/磁盘及系统负载等指标
- **SSE 分页**：海量结果以事件流（Server-Sent Events）分片传输，降低客户端压力
- **自动选择数据源**：在未指定 `datasource_uid` 时根据数据回包智能选择合适的数据源

### 辅助功能
- **Discovery 扫描**：自动采集数据源、Dashboards、Prometheus 指标名、Elasticsearch 字段等信息
- **观测与日志**：提供 Prometheus 指标与 JSON 结构化日志，便于监控和调试
- **认证与限流**：基于 Bearer Token 的统一认证机制和每分钟速率限制

## 技术栈

### 后端框架
- **FastAPI**：高性能的现代 Python Web 框架，用于构建 API
- **Uvicorn**：ASGI 服务器，用于运行 FastAPI 应用

### 核心依赖
- **Pydantic**：数据验证和设置管理
- **httpx**：异步 HTTP 客户端，用于与 Grafana API 交互
- **python-json-logger**：结构化 JSON 日志记录
- **prometheus-client**：Prometheus 指标暴露
- **cryptography**：加密相关功能
- **python-dotenv**：环境变量管理

### 开发工具
- **pytest**：单元测试框架
- **black**：代码格式化工具
- **ruff**：代码质量检查工具

## 快速开始

### 环境要求
- Python `>=3.8`
- 已可用的 Grafana 实例与 API Token/API Key

### 安装

1. 克隆项目仓库
   ```bash
   git clone https://github.com/magicCzc/grafana-query-mcp.git
   cd grafana-query-mcp
   ```

2. 安装依赖
   ```bash
   pip install -r requirements-3.8.txt
   ```

### 配置

1. 复制 `.env.example` 为 `.env`
   ```bash
   cp .env.example .env
   ```

2. 编辑 `.env` 文件，填入实际值
   ```ini
   # Server
   APP_TOKEN=your-secure-api-token
   APP_ENCRYPT_KEY=optional-16+ chars
   APP_RATE_LIMIT_PER_MIN=60
   
   # Grafana
   GRAFANA_URL=https://grafana.example.com
   # 可使用其中之一：
   GRAFANA_TOKEN=your-grafana-api-token
   GRAFANA_API_KEY=your-grafana-api-key
   DATASOURCE_MAP=huawei.microservice:DS_UID
   GRAFANA_RANGE_STYLE=epoch
   GRAFANA_TIMEOUT_MS=10000
   
   # Pagination
   PAGE_DEFAULT_SIZE=100
   PAGE_MAX_SIZE=1000
   
   # Logging
   LOG_LEVEL=INFO
   
   # Discovery
   DISCOVERY_ENABLED=true
   DISCOVERY_REFRESH_INTERVAL_SEC=900
   DISCOVERY_INITIAL_DELAY_MS=500
   DISCOVERY_MAX_CONCURRENCY_DASHBOARDS=8
   DISCOVERY_MAX_CONCURRENCY_ELASTICSEARCH=4
   DISCOVERY_MAX_CONCURRENCY_PROMETHEUS=4
   ```

### 启动服务

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### 验证服务

```bash
curl -H "Authorization: Bearer <APP_TOKEN>" http://127.0.0.1:8080/v1/grafana/health
```

## 使用方法

### 基本查询流程

1. **查询转换**：将业务参数转换为 Grafana 查询
2. **执行查询**：通过 API 执行查询并获取结果
3. **异常检测**：（可选）对查询结果进行异常检测

### API 示例

#### 1. 查询转换
```bash
curl -X POST -H "Authorization: Bearer <APP_TOKEN>" -H "Content-Type: application/json" \
  -d '{"parsed":{"provider":"aws","service":"ec2","names":["instance-1"],"range":"1h","metric":"cpu_utilization","agg":"avg"}}' \
  http://127.0.0.1:8080/v1/grafana/convert
```

#### 2. 执行查询
```bash
curl -X POST -H "Authorization: Bearer <APP_TOKEN>" -H "Content-Type: application/json" \
  -d '{"parsed":{"provider":"aws","service":"ec2","names":["instance-1"],"range":"1h","metric":"cpu_utilization","agg":"avg"},"page_size":100}' \
  http://127.0.0.1:8080/v1/query/start
```

#### 3. 异常检测
```bash
curl -X POST -H "Authorization: Bearer <APP_TOKEN>" -H "Content-Type: application/json" \
  -d '{"range":"1h","pctl":95,"include_normal":false}' \
  http://127.0.0.1:8080/v1/anomaly/instances
```

## 开发指南

### 代码结构

```
app/
  auth.py            # 认证与限流
  config.py          # 环境变量配置加载
  main.py            # API 入口、SSE、指标与日志
  nlp/parser.py      # 中文解析引擎
  converter/grafana.py # 解析参数到 Grafana 查询参数转换
  clients/grafana.py # Grafana 调用封装、重试、缓存与分页
  utils/cache.py     # AES-GCM 加密 TTL 缓存
  discovery.py       # Discovery 扫描逻辑

docs/
  api.md             # API 文档
  deploy.md          # 部署与运维
  architecture.md    # 系统架构设计
  data-model.md      # 数据模型（替代数据库文档）
  examples.md        # 使用示例
  structure.md       # 项目结构

tests/
  conftest.py        # 测试配置
  test_parser.py     # 解析器测试
  test_converter.py  # 转换器测试

pyproject.toml       # 依赖与格式化工具配置
.env.example         # 环境变量示例
.env                 # 实际环境变量（不应提交）
requirements-3.8.txt # 依赖列表
```

### 开发环境设置

1. 安装开发依赖
   ```bash
   pip install -r requirements-3.8.txt
   ```

2. 配置开发环境变量
   ```bash
   cp .env.example .env
   # 编辑 .env 文件设置开发环境参数
   ```

3. 运行开发服务器
   ```bash
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
   ```

### 代码质量

1. 格式化代码
   ```bash
   python -m black app
   ```

2. 代码质量检查
   ```bash
   python -m ruff app
   ```

3. 运行测试
   ```bash
   pytest -q
   ```

## 贡献指南

### 如何贡献

1. Fork 项目仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'feat: Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

### 代码规范

- 代码必须通过 `black` 格式化和 `ruff` 检查
- 函数和类必须有完整的文档字符串
- 复杂逻辑必须有清晰的注释
- 遵循项目的命名约定（snake_case、PascalCase）

### PR 要求

- PR 标题清晰描述变更内容
- PR 描述包含：
  - 变更摘要
  - 影响范围
  - 风险点
  - 测试情况
- 确保所有测试通过
- 避免提交 `.env` 等包含密钥的文件

## 许可证

- Copyright (c) 2025
- License: MIT

## 联系方式

如有问题或建议，请通过以下方式联系：

- 维护者：Chenzc

