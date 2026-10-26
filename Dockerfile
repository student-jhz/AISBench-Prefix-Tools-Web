FROM python:3.11-slim

WORKDIR /app

# 先安装依赖(利用 Docker 层缓存)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 后端应用(含内置测试代码 core_code)与前端静态资源
COPY backend /app/backend
COPY frontend /app/frontend

ENV PYTHONUNBUFFERED=1 \
    AISBENCH_DATA_DIR=/app/data

EXPOSE 8320

CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8320", "--app-dir", "/app/backend"]
