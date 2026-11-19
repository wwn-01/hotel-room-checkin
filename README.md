# 酒店房态与入住协同 MVP
基于 FastAPI 开发的酒店房态管理后端接口项目

## 接口列表
1. GET /rooms — 获取全部房间信息
2. GET /room/{room_id} — 查询单个房间
3. PUT /room/{room_id}/status — 修改房间状态
4. POST /workorder — 创建保洁工单
5. GET /logs — 查看操作日志

## 本地运行方式
```bash
pip install -r src/requirements.txt
uvicorn src.main:app --reload
## MVP拓展规划
- 知识库：RAG、LLM、Wiki、知识图谱
- 权限：分层权限（管理员 / 用户，SaaS）
- CI/CD：Harness自动化部署
