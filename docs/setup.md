# 安装与适配（国内镜像 + Python 3.8）

## Python 3.8 兼容

- 代码与依赖已适配 Python `>=3.8`。
- 推荐使用 `python3.8` 虚拟环境运行，示例：
  - Windows（PowerShell）：
    - `py -3.8 -m venv .venv`
    - `.\.venv\Scripts\Activate.ps1`
  - Linux/macOS：
    - `python3.8 -m venv .venv`
    - `source .venv/bin/activate`

## 使用国内镜像安装依赖

- 方案一：临时镜像参数（推荐简单）
  - `pip install -r requirements-3.8.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn`
  - 如需阿里云：`-i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com`
- 方案二：全局镜像配置（Windows 示例）
  - 复制仓库中的 `pip.ini.example` 到 `%APPDATA%\pip\pip.ini`
  - 内容：
    ```ini
    [global]
    index-url = https://pypi.tuna.tsinghua.edu.cn/simple
    trusted-host = pypi.tuna.tsinghua.edu.cn
    [install]
    use-pep517 = true
    ```
- 方案三：项目级镜像（一次性）
  - `pip install -r requirements-3.8.txt --config-settings index-url=https://pypi.tuna.tsinghua.edu.cn/simple`

## 启动与验证

- 启动服务（优先使用 `python -m uvicorn` 以避免 PATH 问题）：
  - `python -m uvicorn app.main:app --host 0.0.0.0 --port 8080`
- 验证连通：
  - `GET /v1/grafana/health` → 期望返回 `version: 8.5.6`
  - `GET /v1/grafana/datasources_remote` → 列出远端数据源（含 `prometheus`）
- 查询示例（不提供 `datasource_uid`，由服务自动选择）：
  ```json
  POST /v1/query/execute
  {
    "parsed": {
      "provider": "huawei",
      "service": "microservice",
      "names": ["center-sca-w101","center-sca-w102"],
      "range": "5m",
      "metric": "cpu",
      "agg": "avg"
    },
    "page_size": 100,
    "page_index": 0
  }
  ```

## 常见问题

- `uvicorn.exe` 不在 PATH：使用 `python -m uvicorn ...` 方式启动。
- 依赖下载慢或超时：确认镜像地址与 `--trusted-host`；或切换到阿里云/华为云镜像。
- Python 版本不兼容：确保虚拟环境为 3.8，`pip --version` 显示路径指向 `.venv`。
