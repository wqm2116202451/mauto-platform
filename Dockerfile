FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    MAUTO_HOST=0.0.0.0 \
    MAUTO_PORT=8000 \
    MAUTO_ENV=production \
    MAUTO_DATA_DIR=/app/data

WORKDIR /app

# 中文字体用于 PPT / PDF / PNG 渲染；ca-certificates 用于 HTTPS 抓取
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY static ./static

RUN groupadd --system mauto \
    && useradd --system --gid mauto --home-dir /app mauto \
    && mkdir -p /app/data \
    && chown -R mauto:mauto /app

USER mauto

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]

CMD ["python", "-m", "mauto.serve"]
