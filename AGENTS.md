# AGENTS.md — 酒店房态与入住协同 MVP

本文件是本仓库的长期协作规则，供 DeepSeek Harness / WorkBuddy / Trae 等编码智能体在会话开始时读取。规则是可审计的行为约束，不是教程。

## 项目概览
- 技术栈：FastAPI + Uvicorn + Pydantic，数据落在本地 SQLite（`hotel.db`）。
- 业务：酒店房态管理、保洁/质检工单、审计日志、房态协同问答。
- 单端口 HTTP 服务，部署沙箱只开放一个端口，不提供独立数据库/缓存。

## 硬规则（必须遵守）
- 不要引入 MySQL / PostgreSQL / Redis / MongoDB 等外部服务；持久化只用 SQLite 或项目内文件。
- 不要读取、打印或提交任何 `.env`、`*.db`、密钥文件。演示模式不依赖大模型密钥。
- 修改接口时必须保持既有路由契约：`/rooms`、`/room/{id}`、`PUT /room/{id}/status`、`/workorder`、`/logs`。
- 所有状态变更与敏感操作必须写入 `audit_log`。
- 智能体只生成建议/工单/提醒；不要直接开锁、不要强制核验客人；异常（冲突、维修、核查失败）必须转人工。
- 服务必须监听 `0.0.0.0` 并读取 `PORT` 环境变量（本地默认 8000）。

## 命令约定
- 安装依赖：`pip install -r src/requirements.txt`
- 本地启动：`uvicorn src.main:app --host 0.0.0.0 --port 8000`
- 接口文档：启动后访问 `/docs`
- 启动入口固定为 `src.main:app`，不要改为 `main:app`。

## 代码风格
- 新能力放在 `src/` 下的独立模块（如 `auth.py`、`rag.py`），`main.py` 只做注册与路由，保持简单。
- 依赖保持最小；新增第三方依赖必须同步写入 `src/requirements.txt`。
- 注释与文档用中文，提交信息格式：`类型: 说明`（如 `feat: 加入管理员与普通用户权限`）。

## 权限模型
- 角色：`admin`（管理员）、`user`（普通用户），通过 JWT Bearer Token 鉴权（`POST /login` 换取 token）。
- 演示账号：`admin/admin123`、`user/user123`；密钥由环境变量 `JWT_SECRET_KEY` 覆盖。
- 未携带或非法 token 返回 401，未知角色降级为 `user`，默认拒绝未授权的管理员操作。
- 读接口对两种角色开放；状态变更、工单创建、审计日志查看、知识库写入仅 `admin` 可用。

## 知识库 / RAG
- 知识来源统一放 `docs/knowledge_base/`，以 Markdown 为准。
- RAG 必须提供无密钥的本地降级实现（关键词检索），LLM 只作为可选增强，不可用时自动回退。
- 回答需携带来源文件名，无法确认时明确说明"知识库无相关内容，需转人工"。
