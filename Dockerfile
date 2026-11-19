# 酒店房态与入住协同 MVP —— 容器化部署
# 构建：docker build -t hotel-room-checkin .
# 运行：docker run -d -p 8000:8000 -e JWT_SECRET_KEY=your-secret hotel-room-checkin
FROM python:3.11-slim

# 不写 pyc、日志实时输出
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先装依赖，利用 Docker 层缓存
COPY src/requirements.txt /app/src/requirements.txt
RUN pip install --no-cache-dir -r /app/src/requirements.txt

# 拷贝应用与知识库文档（RAG 依赖 docs/）
COPY src/ /app/src/
COPY docs/ /app/docs/

# 监听端口由 PORT 环境变量控制，默认 8000
ENV PORT=8000
EXPOSE 8000

# 以模块方式启动，入口固定 src.main:app
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT}"]
