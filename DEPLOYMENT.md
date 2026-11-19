# 酒店房态与入住协同 MVP —— 公网部署方案

本项目的部署约束（来自 `AGENTS.md`）：**单端口 HTTP 服务**、**SQLite 本地持久化**、**演示模式不依赖大模型密钥**。
服务监听 `0.0.0.0`，端口由环境变量 `PORT` 控制（默认 `8000`），启动入口固定为 `src.main:app`。

---

## 0. 部署前准备（所有方案通用）

### 0.1 环境与前提

| 项 | 说明 |
|----|------|
| Python | 3.10+（RAG 模块用标准库，无需编译依赖） |
| 依赖 | `pip install -r src/requirements.txt` |
| 端口 | 单端口，默认 `8000`，可被 `PORT` 覆盖 |
| 数据 | SQLite 文件 `hotel.db`，首次启动自动建表并写入演示数据 |
| 知识库 | `docs/**/*.md`，RAG 启动时会扫描并建立索引 |
| 密钥 | JWT 密钥建议通过 `JWT_SECRET_KEY` 注入；LLM 完全可选 |

### 0.1.1 已实现模块清单

| 模块 | 代码位置 | 说明 |
|------|---------|------|
| 房态/工单/日志核心接口 | `src/main.py` | 保留原有 `/rooms`、`/room/{id}`、`/workorder`、`/logs`，新增 `/health`、`POST /room/{id}/checkin`（房态校验闭环） |
| 分层权限（RBAC） | `src/auth.py` | JWT + admin/user 角色 + 细粒度权限点（Permission）+ 多租户占位（Principal.tenant） |
| 知识库 / 向量检索 | `src/rag.py` | 文档上传、文本分片、哈希 TF-IDF 向量检索（余弦相似度）、RAG 问答、LLM Wiki |
| 扩展接口 | `src/rag.py` | `LLMProvider` / `KnowledgeGraph` 抽象类，可替换真实大模型与图数据库 |
| 测试调度器 | `tests/harness_runner.py` | 一键编排单元测试 + 接口测试，输出 JSON 报告 |
| 单元测试 | `tests/test_rag.py`、`tests/test_auth.py` | 覆盖 RAG/向量/上传/图谱、JWT/RBAC |
| 接口测试 | `tests/test_api_http.py` | 真实起服务，端到端验证全部接口 |

### 0.2 演示账号（用于验收）

- 管理员：`admin / admin123`
- 普通用户：`user / user123`

### 0.3 环境变量

复制 `.env.example` 为 `.env` 后按需修改（`.env` 已被 git 排除，不会进仓库）：

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `PORT` | 否 | `8000` | 服务监听端口；PaaS 平台通常自动注入 |
| `JWT_SECRET_KEY` | 建议 | 内置演示密钥 | 上线务必替换为随机长字符串 |
| `LLM_API_KEY` | 否 | 空 | 留空即纯本地 RAG，无需大模型 |
| `LLM_BASE_URL` | 否 | DeepSeek 接口 | 兼容 OpenAI 的 chat/completions 地址 |
| `LLM_MODEL` | 否 | `deepseek-chat` | 模型名 |

### 0.4 启动命令（本地 / 通用）

```bash
# 安装依赖
pip install -r src/requirements.txt

# 方式一：uvicorn 直接启动（推荐，显式绑定）
uvicorn src.main:app --host 0.0.0.0 --port 8000

# 方式二：python 模块启动（会读取 PORT 环境变量）
PORT=8000 python -m src.main

# Windows PowerShell 设置端口后启动
$env:PORT=8000; python -m src.main
```

> **重要**：部署平台的「启动命令」请用 `uvicorn src.main:app --host 0.0.0.0 --port 8000` 这种**显式端口号**的写法。
> 部分沙箱/PaaS 不会展开 `$PORT` 变量（会传空值导致 uvicorn 报错），若平台强制要求用变量，需确认平台确实会注入该变量。

### 0.5 部署后验证清单

```bash
# 1) 探活接口（无鉴权，期望 200 + {"status":"ok"}）
curl http://<公网地址>/health

# 2) 接口文档（期望 200，Swagger UI）
curl http://<公网地址>/docs

# 3) 登录拿 token
curl -X POST http://<公网地址>/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

# 4) 带 token 访问业务接口
curl -H "Authorization: Bearer <token>" http://<公网地址>/rooms

# 5) RAG 问答
curl -X POST http://<公网地址>/rag/ask \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"question":"房间可以入住的前置条件"}'

# 6) 向量检索
curl -X POST http://<公网地址>/rag/vector-search \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"question":"房间入住条件","top_k":3}'

# 7) 文档上传（仅管理员）
curl -X POST http://<公网地址>/knowledge/upload \
  -H "Authorization: Bearer <admin_token>" -H "Content-Type: application/json" \
  -d '{"text":"# 新规则\n宠物可入住需押金。","source":"pet.md"}'
```

### 0.6 Harness 测试调度（部署前/后自检）

```bash
# 一键运行全部测试（单元 + 接口），输出结构化 JSON 报告，退出码 0 表示全通过
python -m tests.harness_runner
python -m tests.harness_runner --json report.json     # 报告写入文件
python -m tests.harness_runner --suite unit           # 只跑单元测试
python -m tests.harness_runner --suite http           # 只跑接口测试

# 当前基线：49 项全部通过（单元 36 + 接口 13）
```

> Harness / CI 可解析 stdout 中以 `REPORT_JSON=` 开头的单行 JSON，其中 `all_ok` 字段为总闸门。

---

## 方案 A：WorkBuddy 应用发布（最快，已验证可用）

**适用场景**：课程演示、给老师快速看效果；无需自有服务器。
**需要工具**：WorkBuddy 内置「发布为应用」能力。
**步骤**：

1. 在 WorkBuddy 对话中说明「部署当前 FastAPI 项目到公网」。
2. 提供参数：
   - 项目目录：`hotel-room-checkin/`
   - 依赖安装：`pip install -r src/requirements.txt`
   - 启动命令：`uvicorn src.main:app --host 0.0.0.0 --port 8000`（**显式 8000，不用 $PORT**）
   - 端口：`8000`
3. 发布完成后得到形如 `https://<域名>/?sharecode=<凭证>` 的链接。
4. 在链接后加 `/docs` 打开 Swagger 文档：`https://<域名>/docs?sharecode=<凭证>`。

**注意**：
- `sharecode` 是访问凭证，分享链接时须完整保留。
- 每次代码更新后需重新发布，链接才会同步最新代码。
- 此方案的数据写在平台沙箱内，重启可能重置数据库，适合演示不适合长期存数据。

---

## 方案 B：Docker 容器部署（通用、可迁移）

**适用场景**：有自己的服务器或云主机，想要标准化部署。
**需要工具**：Docker 20+、一台有公网 IP 的主机。
**项目已提供**：`Dockerfile`、`.dockerignore`。

### 步骤

```bash
# 1. 进入项目目录
cd hotel-room-checkin

# 2. 构建镜像
docker build -t hotel-room-checkin .

# 3. 运行容器（映射 8000 端口，注入密钥）
docker run -d --name hotel-api \
  -p 8000:8000 \
  -e JWT_SECRET_KEY="your-random-secret" \
  hotel-room-checkin

# 4. 查看日志与状态
docker logs -f hotel-api
curl http://localhost:8000/health
```

### 数据持久化（避免容器重建丢数据）

SQLite 文件在容器内 `/app/hotel.db`。用数据卷挂载到宿主机持久化：

```bash
docker run -d --name hotel-api \
  -p 8000:8000 \
  -e JWT_SECRET_KEY="your-random-secret" \
  -v "$(pwd)/data:/app/data" \
  -e DB_FILE=/app/data/hotel.db \
  hotel-room-checkin
```

> 若要支持 `DB_FILE` 自定义路径，需将 `src/main.py` 中的 `DB_FILE = "hotel.db"` 改为
> `DB_FILE = os.getenv("DB_FILE", "hotel.db")`（一行改动，属可选增强）。

### 反向代理（可选，配域名 + HTTPS）

用 Nginx 把 80/443 转发到容器 8000 端口，并配置 Let's Encrypt 证书。核心配置：

```nginx
server {
    listen 80;
    server_name your-domain.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## 方案 C：云主机 + systemd（无 Docker）

**适用场景**：一台 Ubuntu/CentOS 云主机，想直接跑 Python 进程。
**需要工具**：云服务器（阿里云 / 腾讯云 / AWS 等）、Python 3.10+。

### 步骤

```bash
# 1. 上传项目到服务器（用 scp 或 git clone）
scp -r hotel-room-checkin/ user@your-server:/opt/

# 2. 登录服务器，创建虚拟环境并安装依赖
ssh user@your-server
cd /opt/hotel-room-checkin
python3 -m venv .venv
source .venv/bin/activate
pip install -r src/requirements.txt

# 3. 前台试跑（确认无误后 Ctrl+C 退出）
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

创建 systemd 服务 `/etc/systemd/system/hotel-api.service`：

```ini
[Unit]
Description=Hotel Room Checkin API
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/hotel-room-checkin
Environment="PORT=8000"
Environment="JWT_SECRET_KEY=your-random-secret"
ExecStart=/opt/hotel-room-checkin/.venv/bin/uvicorn src.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
# 4. 启动并设为开机自启
sudo systemctl daemon-reload
sudo systemctl enable --now hotel-api
sudo systemctl status hotel-api

# 5. 验证
curl http://localhost:8000/health
```

安全组/防火墙需放行 `8000` 端口（或经 Nginx 转发则放行 80/443）。

---

## 方案 D：PaaS 平台（Render / Railway / Fly.io）

**适用场景**：不想管服务器，平台按容器/进程托管，通常有免费层。
**需要工具**：平台账号 + 连接 GitHub 仓库（这也是「先推送代码到仓库」的意义）。

### 通用配置（各平台大同小异）

| 配置项 | 值 |
|--------|----|
| 构建命令 Build | `pip install -r src/requirements.txt` |
| 启动命令 Start | `uvicorn src.main:app --host 0.0.0.0 --port $PORT` |
| 端口 | 由平台注入 `PORT`（**这类平台会正确展开 $PORT**） |
| 环境变量 | `JWT_SECRET_KEY`（必填），`LLM_API_KEY`（可选） |

### Render 示例

1. 新建 Web Service，连接你的 GitHub 仓库。
2. Runtime 选 Python，Build Command 填 `pip install -r src/requirements.txt`。
3. Start Command 填 `uvicorn src.main:app --host 0.0.0.0 --port $PORT`。
4. Environment 添加 `JWT_SECRET_KEY`。
5. 部署完成后访问 `https://<服务名>.onrender.com/docs`。

### 注意

- PaaS 文件系统通常是临时的，`hotel.db` 重启会丢失；需要长期数据请用平台提供的持久卷或外部数据库（本项目按硬规则只用 SQLite，演示场景可接受）。
- 免费层实例休眠后首次访问会慢。

---

## 方案对比与推荐

| 方案 | 上手难度 | 是否需要服务器 | 数据持久 | 适合场景 |
|------|---------|--------------|---------|---------|
| A. WorkBuddy 发布 | ★☆☆☆☆ | 否 | 弱 | 课程演示、快速给老师看 |
| B. Docker | ★★★☆☆ | 是 | 强（挂载卷） | 标准化、可迁移 |
| C. 云主机 + systemd | ★★★☆☆ | 是 | 强 | 长期运行、完全可控 |
| D. PaaS | ★★☆☆☆ | 否 | 弱 | 免运维、有免费层 |

**推荐顺序**：演示优先用 **方案 A**；需要长期稳定、数据持久用 **方案 B 或 C**；已有 GitHub 仓库且想免运维用 **方案 D**。

---

## 常见问题

1. **启动报 `--port` 参数为空**：启动命令里的 `$PORT` 未被平台展开。改用显式 `--port 8000`（WorkBuddy 沙箱必须这样做）。
2. **根路径 `/` 返回 404**：项目未定义根路由，属正常现象；用 `/health`、`/docs` 验证即可。
3. **RAG 答非所问或转人工**：得分低于阈值 `MIN_RELEVANCE=2.5` 会明确转人工，这是设计行为；可往 `docs/knowledge_base/` 补充 Markdown 后调用 `GET /knowledge/reload`（管理员）重建索引。
4. **忘记 JWT 密钥**：演示密钥是内置的，不设也能跑；生产务必设 `JWT_SECRET_KEY`，否则任何人都能伪造 token。
5. **容器内找不到知识库**：确认 Dockerfile 已 `COPY docs/ /app/docs/`，且工作目录为 `/app`。
6. **上传的文档重启后丢失**：`POST /knowledge/upload` 是运行时内存上传，重启需重新上传；若要持久化，把文档写入 `docs/knowledge_base/*.md` 后调用 `GET /knowledge/reload`。
7. **向量检索与 RAG 问答的区别**：`/rag/vector-search` 走余弦相似度召回（`similarity` 0~1）；`/rag/ask` 走 TF-IDF 阈值判定并生成答案（带 `need_human`）。两者共用同一份切片索引。
8. **接入真实大模型 / 图数据库**：实现 `src/rag.py` 中的 `LLMProvider` 或 `KnowledgeGraph` 抽象类，构造 `KnowledgeBase(llm=..., kg=...)` 注入即可，无需改动业务代码。
