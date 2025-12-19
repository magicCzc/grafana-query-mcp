<!-- Copyright (c) 2025, Project Grafana Query MCP. All rights reserved. -->

# 开发环境配置文档

本文档详细说明如何配置 Grafana Query MCP 项目的开发环境，包括依赖安装、环境变量设置和开发工具配置。

## 系统要求

### 硬件要求
- CPU: 2 核或以上
- 内存: 4GB 或以上
- 磁盘空间: 10GB 或以上

### 软件要求
- **操作系统**: Linux, macOS, Windows
- **Python**: 3.8 或以上版本
- **Git**: 2.20 或以上版本
- **Grafana**: 8.0 或以上版本（用于测试）

## 安装步骤

### 1. 克隆项目仓库

```bash
git clone https://github.com/magicCzc/grafana-query-mcp.git
cd grafana-query-mcp
```

### 2. 创建虚拟环境（可选但推荐）

#### Linux/macOS
```bash
python3 -m venv venv
source venv/bin/activate
```

#### Windows
```bash
python -m venv venv
venv\Scripts\activate
```

### 3. 安装依赖

```bash
pip install -r requirements-3.8.txt
```

## 环境变量配置

### 1. 创建环境变量文件

复制 `.env.example` 为 `.env` 文件：

```bash
cp .env.example .env
```

### 2. 配置环境变量

编辑 `.env` 文件，根据开发环境设置以下变量：

#### 服务器配置
```ini
# Server
APP_TOKEN=your-secure-api-token       # 用于 API 认证的令牌
APP_ENCRYPT_KEY=optional-16+chars     # 加密密钥（可选，至少 16 个字符）
APP_RATE_LIMIT_PER_MIN=60             # 每分钟请求速率限制
```

#### Grafana 配置
```ini
# Grafana
GRAFANA_URL=http://localhost:3000     # Grafana 实例的 URL
# 可使用其中之一（API Token 或 API Key）：
GRAFANA_TOKEN=your-grafana-api-token  # Grafana API Token
GRAFANA_API_KEY=your-grafana-api-key  # Grafana API Key
DATASOURCE_MAP=huawei.microservice:DS_UID  # 数据源映射（可选）
GRAFANA_RANGE_STYLE=epoch             # 时间范围格式（epoch 或 relative）
GRAFANA_TIMEOUT_MS=10000              # Grafana API 超时时间（毫秒）
```

#### 分页配置
```ini
# Pagination
PAGE_DEFAULT_SIZE=100                 # 默认每页大小
PAGE_MAX_SIZE=1000                    # 最大每页大小
```

#### 日志配置
```ini
# Logging
LOG_LEVEL=INFO                        # 日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
```

#### Discovery 配置
```ini
# Discovery
DISCOVERY_ENABLED=true                # 是否启用 Discovery 扫描
DISCOVERY_REFRESH_INTERVAL_SEC=900    # 扫描刷新间隔（秒）
DISCOVERY_INITIAL_DELAY_MS=500        # 初始扫描延迟（毫秒）
DISCOVERY_MAX_CONCURRENCY_DASHBOARDS=8  # Dashboards 扫描最大并发数
DISCOVERY_MAX_CONCURRENCY_ELASTICSEARCH=4  # Elasticsearch 扫描最大并发数
DISCOVERY_MAX_CONCURRENCY_PROMETHEUS=4     # Prometheus 扫描最大并发数
```

## 开发工具配置

### 1. IDE 推荐

- **PyCharm**: 专业的 Python IDE，提供代码补全、调试等功能
- **VS Code**: 轻量级编辑器，配合 Python 扩展使用
- **Sublime Text**: 简洁的文本编辑器，配合插件使用

### 2. 代码格式化工具

项目使用 `black` 进行代码格式化：

```bash
python -m black app
```

### 3. 代码质量检查工具

项目使用 `ruff` 进行代码质量检查：

```bash
python -m ruff app
```

### 4. 测试工具

项目使用 `pytest` 进行单元测试：

```bash
pytest -q
```

## 启动开发服务器

### 1. 基本启动

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

### 2. 带自动重载的开发模式

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

### 3. 验证服务

启动服务后，可以通过以下命令验证服务是否正常运行：

```bash
curl -H "Authorization: Bearer your-app-token" http://127.0.0.1:8080/v1/grafana/health
```

## 调试技巧

### 1. 启用调试日志

在 `.env` 文件中设置 `LOG_LEVEL=DEBUG`，可以查看详细的调试日志。

### 2. 使用 FastAPI 自动生成的文档

访问 `http://127.0.0.1:8080/docs` 可以查看 FastAPI 自动生成的交互式 API 文档。

### 3. 使用 Pydantic 调试模式

在开发环境中，可以启用 Pydantic 的调试模式，获取更详细的错误信息：

```python
# 在 app/main.py 中添加
from pydantic import BaseSettings
BaseSettings.Config.validate_assignment = True
```

## 常见问题解决

### 1. 依赖安装失败

**问题**：安装依赖时出现错误
**解决方法**：
- 确保 Python 版本正确（3.8+）
- 更新 pip 到最新版本：`pip install --upgrade pip`
- 尝试单独安装失败的包：`pip install <package-name>`

### 2. Grafana 连接失败

**问题**：服务无法连接到 Grafana
**解决方法**：
- 检查 `GRAFANA_URL` 是否正确
- 验证 `GRAFANA_TOKEN` 或 `GRAFANA_API_KEY` 是否有效
- 确保 Grafana 实例正在运行且网络可访问

### 3. 端口被占用

**问题**：启动服务时提示端口已被占用
**解决方法**：
- 尝试使用不同的端口：`--port 8081`
- 查找并关闭占用该端口的进程

## 开发工作流

1. 创建特性分支：`git checkout -b feature/YourFeature`
2. 编写代码并添加测试
3. 运行代码格式化：`python -m black app`
4. 运行代码质量检查：`python -m ruff app`
5. 运行测试：`pytest -q`
6. 提交代码：`git commit -m 'feat: Add some feature'`
7. 推送到远程分支：`git push origin feature/YourFeature`
8. 创建 Pull Request

## 相关文档

- [项目结构文档](structure.md)
- [API 接口文档](api.md)
- [架构设计文档](architecture.md)
- [数据模型文档](data-model.md)
