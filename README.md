# 酒店房态与入住协同 MVP

基于 FastAPI 的酒店房态管理后端接口项目，包含分层权限、知识库 RAG 问答与 LLM Wiki。

## 本地运行

```bash
pip install -r src/requirements.txt
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

启动后访问 `http://localhost:8000/docs` 查看 Swagger 接口文档。

## 接口列表

| 方法 | 路径 | 说明 | 权限 |
| ---- | ---- | ---- | ---- |
| POST | `/login` | 登录换取 JWT | 公开 |
| GET | `/rooms` | 获取全部房间信息 | user / admin |
| GET | `/room/{room_id}` | 查询单个房间 | user / admin |
| PUT | `/room/{room_id}/status` | 修改房间状态 | 仅 admin |
| POST | `/workorder` | 创建保洁工单 | 仅 admin |
| GET | `/logs` | 查看审计日志 | 仅 admin |
| POST | `/rag/ask` | 知识库 RAG 问答（带来源） | user / admin |
| POST | `/rag/wiki` | LLM Wiki 结构化词条 | user / admin |
| GET | `/knowledge/stats` | 知识库索引统计 | user / admin |
| GET | `/knowledge/reload` | 重新加载知识库索引 | 仅 admin |

## 分层权限（JWT）

两种角色：`user`（普通用户）、`admin`（管理员）。通过 JWT Bearer Token 鉴权。

演示账号：`admin / admin123`（管理员）、`user / user123`（普通用户）。

- 未携带、伪造或过期 token 一律返回 401；未知角色降级为 `user`（默认拒绝提权）。
- 写操作（改房态、开工单）、审计日志、知识库重载仅管理员可用，通过 `src/auth.py` 依赖注入统一校验。
- 密钥由环境变量 `JWT_SECRET_KEY` 覆盖（默认演示密钥，上线务必替换）；真实接入只需替换 `authenticate_user` 的数据源。

登录并调用示例：

```bash
# 1) 登录拿 token
TOKEN=$(curl -s -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 2) 带 token 访问管理员接口
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/logs
```

## 知识库 / RAG / LLM Wiki

模块位于 `src/rag.py`，零第三方 NLP 依赖，纯标准库实现：

- **文档切片**：扫描 `docs/` 下所有 Markdown，按标题 / 空行 / 长度三级切片（单块上限 300 字、超长块保留重叠行），把所属标题拼进切片文本作为检索信号，过滤访谈时间戳等噪音；`knowledge_base/` 目录内容检索加权 1.5 倍。
- **检索**：中文 1-gram + 2-gram、英文整词分词，TF-IDF 风格打分（按正文长度归一避免长文档刷分），返回来源、标题、得分与查询词覆盖率。
- **相关性阈值**：`MIN_RELEVANCE = 2.5`，命中得分低于阈值视为无可靠依据，明确转人工。
- **无密钥降级**：默认抽取式作答，答案逐条标注来源文件；设置密钥后自动切换 LLM 作答，接口异常时静默回退本地模式。
- **LLM Wiki**：把命中片段整理为结构化词条（标题 / 摘要 / 要点 / 标签 / 来源 / 得分）。

可选 LLM 增强环境变量：

```bash
export LLM_API_KEY=your_key          # 非空即启用 LLM
export LLM_BASE_URL=https://api.deepseek.com/v1/chat/completions
export LLM_MODEL=deepseek-chat
```

RAG 问答示例（接口需 JWT，先登录拿 token）：

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d '{"username":"user","password":"user123"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# RAG 问答
curl -X POST http://localhost:8000/rag/ask \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"房间可以入住的前置条件是什么","top_k":3}'

# LLM Wiki
curl -X POST http://localhost:8000/rag/wiki \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"question":"角色与权限"}'

# 知识库统计 / 重载（重载仅管理员）
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/knowledge/stats
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/knowledge/reload
```

## 测试

测试脚本在 `tests/`，纯标准库实现，无需 pytest：

```bash
# 单元测试：分词、切片、检索打分、RAG 问答、Wiki、LLM 回退（14 项）
python -m unittest tests.test_rag -v

# 端到端冒烟：真实启动服务，验证 JWT + RAG 全链路（8 项）
python tests/test_api_http.py
```

两组测试全部通过：单元测试 14/14，端到端 8/8。

## 协作约定

编码智能体请读取根目录 `AGENTS.md`，其中记录了项目硬规则、命令约定与权限模型。

## MVP 拓展规划

- [x] 分层权限（管理员 / 用户）
- [x] 知识库：RAG、LLM、Wiki
- [ ] 知识图谱
- [ ] CI/CD：Harness 自动化部署
