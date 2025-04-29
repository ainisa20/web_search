# 使用官方Python 3.11镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖（根据你的实际需求调整）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 设置Python缓冲环境变量（推荐用于容器）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 设置Quart环境变量
ENV QUART_APP=app.app:app
ENV QUART_ENV=production
ENV PORT=5045

# 暴露端口
EXPOSE 5045

# 使用uvicorn运行Quart应用（Python 3.11优化参数）
CMD ["uvicorn", \
    "--host", "0.0.0.0", \
    "--port", "5045", \
    "--workers", "4", \
    "--loop", "uvloop", \
    "--http", "httptools", \
    "app.app:app"]