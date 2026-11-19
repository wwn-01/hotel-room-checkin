# 山海旅居 - 酒店房态与入住协同管理MVP
项目：FastAPI 后端，实现酒店房态管理、入住校验、工单、审计日志
仓库：后端源码（包含JWT鉴权模块）
线上演示说明：
线上部署版本仅开放业务接口：
GET /rooms
GET /room/{room_id}
PUT /room/{room_id}/status
POST /workorder
GET /logs
JWT登录模块在源码中存在，本次线上演示前端采用前端本地账号校验。

🔗 前端演示平台：https://hotel-ops-44085.app.workbuddy.host?sharecode=QUZAG42PCX
🔗 后端接口文档Swagger：https://hotel-checkin-api.app.workbuddy.host/docs?sharecode=YIIERFJOD5
演示账号：
管理员 admin / admin123
普通用户 user / user123
