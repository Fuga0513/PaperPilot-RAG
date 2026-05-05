# PaperPilot-RAG 本地开发环境指南

这份指南面向第一次运行本项目的同学。当前项目使用 FastAPI 后端、Vue 3 CDN 前端、PostgreSQL、Redis、Milvus、LangChain 和本地 embedding 模型。

## 1. 需要安装的软件

请先安装：

- Python 3.12+
- Docker Desktop
- Git
- uv，推荐的 Python 包管理工具

可选工具：

- VS Code 或 PyCharm
- DBeaver，用于查看 PostgreSQL
- Redis Insight，用于查看 Redis
- Milvus Attu，项目的 `docker-compose.yml` 已包含，启动后可访问 `http://127.0.0.1:8080`

检查命令：

```powershell
python --version
docker --version
docker compose version
git --version
uv --version
```

如果还没有安装 uv，可以用：

```powershell
pip install uv
```

## 2. 检查 Docker Compose

项目根目录已经包含 `docker-compose.yml`，里面定义了：

- PostgreSQL: `localhost:5432`
- Redis: `localhost:6379`
- Milvus standalone: `localhost:19530`
- Milvus health endpoint: `localhost:9091`
- Attu 管理界面: `http://127.0.0.1:8080`
- Milvus 依赖服务：etcd、MinIO

本阶段没有覆盖或重写 `docker-compose.yml`。

## 3. 创建 Python 虚拟环境

在项目根目录执行：

```powershell
uv venv
```

激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 阻止脚本运行，可以临时执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 4. 安装 requirements / 项目依赖

当前项目没有单独的 `requirements.txt`，依赖写在 `pyproject.toml` 和 `uv.lock` 中。推荐使用：

```powershell
uv sync
```

如果不用 uv，也可以使用 pip：

```powershell
python -m pip install -U pip
pip install -e .
```

第一次安装 `BAAI/bge-m3` embedding 模型时，运行上传或检索可能会下载 HuggingFace 模型，请保持网络可用。

## 5. 配置 .env

复制 `.env.example` 为 `.env`：

```powershell
Copy-Item .env.example .env
```

然后编辑 `.env`。至少需要确认这些值：

```env
ARK_API_KEY=your_api_key_here
BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MODEL=qwen-plus
GRADE_MODEL=qwen-plus

EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DEVICE=cpu
DENSE_EMBEDDING_DIM=1024

MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530
MILVUS_COLLECTION=embeddings_collection

DATABASE_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/langchain_app
REDIS_URL=redis://127.0.0.1:6379/0

BM25_STATE_PATH=data/bm25_state.json
AUTO_MERGE_ENABLED=true
```

Rerank 是可选能力。如果你没有 rerank 服务，可以先保持为空：

```env
RERANK_MODEL=
RERANK_BINDING_HOST=
RERANK_API_KEY=
```

注意：`.env` 已在 `.gitignore` 中忽略，不要提交真实 API Key。

## 6. 启动 PostgreSQL / Redis / Milvus

在项目根目录执行：

```powershell
docker compose up -d
```

查看容器状态：

```powershell
docker compose ps
```

等待 `postgres`、`redis`、`standalone` 等服务变为 healthy 或 running。Milvus 第一次启动会慢一些。

查看日志：

```powershell
docker compose logs -f postgres
docker compose logs -f redis
docker compose logs -f standalone
```

停止服务：

```powershell
docker compose down
```

如果要删除本地数据库和向量库数据，需要额外删除 `volumes/` 目录；这会清空本地数据，谨慎操作。

## 7. 启动 FastAPI 后端

推荐启动命令：

```powershell
uv run uvicorn app:app --app-dir backend --host 0.0.0.0 --port 8000 --reload
```

或者：

```powershell
uv run python backend/app.py
```

启动成功后访问：

- 前端页面：`http://127.0.0.1:8000/`
- API 文档：`http://127.0.0.1:8000/docs`

## 8. Vue 3 CDN 前端访问方式

当前前端不是 Vite / Webpack 项目，不需要单独运行 `npm install` 或 `npm run dev`。

FastAPI 会把 `frontend/` 目录挂载到根路径：

- `frontend/index.html`
- `frontend/script.js`
- `frontend/style.css`

浏览器直接打开：

```text
http://127.0.0.1:8000/
```

## 9. 验证 PostgreSQL 连接成功

方式一：看 FastAPI 是否正常启动。`backend/app.py` 启动时会调用 `init_db()`，如果 PostgreSQL 连接失败，后端通常会报错。

方式二：进入 PostgreSQL 容器检查表：

```powershell
docker exec -it supermew-postgres psql -U postgres -d langchain_app
```

在 psql 中执行：

```sql
\dt
```

正常情况下，注册或启动后会看到这些表：

- `users`
- `chat_sessions`
- `chat_messages`
- `parent_chunks`

退出 psql：

```sql
\q
```

## 10. 验证 Redis 连接成功

执行：

```powershell
docker exec -it supermew-redis redis-cli ping
```

如果返回：

```text
PONG
```

说明 Redis 正常。

也可以在登录、聊天或上传后查看 key：

```powershell
docker exec -it supermew-redis redis-cli keys "*"
```

## 11. 验证 Milvus 连接成功

方式一：检查健康接口：

```powershell
curl http://127.0.0.1:9091/healthz
```

方式二：打开 Attu：

```text
http://127.0.0.1:8080
```

连接地址使用：

```text
standalone:19530
```

如果从宿主机上的工具连接，通常使用：

```text
127.0.0.1:19530
```

方式三：后端上传文档后，Milvus 中应出现 `.env` 配置的 collection，默认：

```text
embeddings_collection
```

## 12. 上传文档并测试 RAG 问答

1. 启动 Docker Compose：

```powershell
docker compose up -d
```

2. 启动 FastAPI：

```powershell
uv run uvicorn app:app --app-dir backend --host 0.0.0.0 --port 8000 --reload
```

3. 打开前端：

```text
http://127.0.0.1:8000/
```

4. 注册管理员账号：

- 切换到注册。
- 角色选择 admin。
- 管理员邀请码填写 `.env` 中的 `ADMIN_INVITE_CODE`，默认示例为 `paperpilot-admin-local`。

5. 进入文档管理区域。

6. 上传一个 PDF、Word 或 Excel 文件。

支持格式：

- `.pdf`
- `.doc`
- `.docx`
- `.xls`
- `.xlsx`

7. 等待上传进度完成。

上传完成后，系统会执行：

- 保存原文件到 `data/documents/`
- 解析文档
- 三层分块
- L1/L2 父级块写入 PostgreSQL
- L3 叶子块生成 dense embedding 和 BM25 sparse vector
- L3 叶子块写入 Milvus

8. 回到聊天区域，提问与文档内容相关的问题。

示例：

```text
请总结这篇论文的主要贡献。
```

或：

```text
这个项目文档中提到的系统架构是什么？
```

9. 检查回答下方的 RAG Trace。

应能看到：

- 初次检索结果
- 是否触发查询重写
- Rerank 状态
- Auto-merging 状态
- 检索到的 chunk 来源

## 13. 常见问题

### 后端启动时报 PostgreSQL 连接失败

先确认 Docker 容器启动：

```powershell
docker compose ps
```

再确认 `.env`：

```env
DATABASE_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/langchain_app
```

### Redis 返回连接失败

确认容器名和端口：

```powershell
docker exec -it supermew-redis redis-cli ping
```

`.env` 应为：

```env
REDIS_URL=redis://127.0.0.1:6379/0
```

### Milvus 上传或检索失败

先确认 Milvus health：

```powershell
curl http://127.0.0.1:9091/healthz
```

`.env` 应为：

```env
MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530
MILVUS_COLLECTION=embeddings_collection
```

### 第一次上传文档很慢

首次运行可能会下载 `BAAI/bge-m3` embedding 模型。下载完成后会快很多。

### 普通用户看不到文档上传入口

文档上传、删除、文档列表接口需要 admin 权限。请使用管理员账号登录。
