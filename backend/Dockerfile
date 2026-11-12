FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --system app && useradd --system --gid app --home-dir /app app
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app app ./app
# 运维脚本（支付自检、通道体检）要能在容器里直接跑：生产只装了运行时依赖，
# 宿主机再搭一套 python 环境反而更容易把密钥拷来拷去。
COPY --chown=app:app scripts ./scripts

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=8s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5)"]
# --no-access-log：访问日志由 app/core/request_logging.py 统一输出（带 request_id 和耗时），
# 保留 uvicorn 的会变成每个请求两行，且其中一行没有排查所需的字段。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log"]
