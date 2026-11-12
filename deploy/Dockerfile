FROM docker.m.daocloud.io/library/python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    NODE_PATH=/app/vendor/house-plan-designer/scripts/node_modules

# Debian 官方源在国内慢，换阿里云镜像
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends \
      nodejs npm chromium fonts-noto-cjk ca-certificates \
      libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
      -r /app/backend/requirements.txt

COPY . /app

# check.mjs 只需要 puppeteer-core（不下载浏览器，用系统 chromium）
RUN cd /app/vendor/house-plan-designer/scripts \
    && sed -i 's|registry.npmjs.org|registry.npmmirror.com|g' package-lock.json 2>/dev/null || true \
    && npm install --registry=https://registry.npmmirror.com --no-audit --no-fund

ENV CHROME_PATH=/usr/bin/chromium

WORKDIR /app/backend
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
