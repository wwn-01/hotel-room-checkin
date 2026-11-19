# hotel-room-checkin
酒店客房入住登记系统，基于FastAPI开发，集成JWT权限认证 + RAG本地知识库问答
## 功能
- 用户登录，JWT分层权限：admin管理员 / user普通用户
- 客房基础信息管理
- 本地RAG知识库，支持文档检索问答，无大模型也可使用
## 环境安装
```bash
pip install -r src/requirements.txt
uvicorn src.main:app --reload
```
接口文档访问地址：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## 项目结构
src/
├── main.py      # 主程序入口
├── auth.py      # JWT鉴权模块
├── rag.py       # RAG知识库模块
└── requirements.txt
