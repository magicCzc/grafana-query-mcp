<!-- Copyright (c) 2025, Project Grafana Query MCP. All rights reserved. -->

# 项目结构

```
app/
  auth.py            # 认证与限流
  config.py          # 环境变量配置加载
  main.py            # API 入口、SSE、指标与日志
  nlp/parser.py      # 中文解析引擎
  converter/grafana.py # 解析参数到 Grafana 查询参数转换
  clients/grafana.py # Grafana 调用封装、重试、缓存与分页
  utils/cache.py     # AES-GCM 加密 TTL 缓存
docs/
  api.md             # API 文档
  deploy.md          # 部署与运维
  architecture.md    # 系统架构设计
  data-model.md      # 数据模型（替代数据库文档）
  examples.md        # 使用示例
  structure.md       # 项目结构
tests/
  conftest.py
  test_parser.py
pyproject.toml       # 依赖与格式化工具配置
.env.example         # 环境变量示例
.env                 # 实际环境变量（不应提交）
```

## 版本变更记录
- v0.1.0：新增架构与数据模型文档；规范环境文件仅使用 `.env`
