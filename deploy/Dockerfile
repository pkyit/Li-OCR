# Li-OCR R1.0 · CPU 镜像（有镜像仓库时使用）
# 构建：docker build -f deploy/Dockerfile -t <registry>/li-ocr:r1.0 .
# GPU 版本请基于 paddlepaddle/paddle:3.0.0-gpu-cuda12.6-cudnn9.0 之类的官方镜像自行扩展，
# 并在环境变量中设置 LIOCR_DEVICE=gpu:0、LIOCR_WORKERS=1。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIOCR_HOST=0.0.0.0 \
    LIOCR_PORT=8080 \
    LIOCR_DATA_DIR=/data \
    LIOCR_DEVICE=cpu \
    LIOCR_CROSSCHECK=off

WORKDIR /app
COPY deploy/requirements-serve.txt /app/requirements.txt
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r /app/requirements.txt

COPY backend /app/backend
COPY frontend /app/frontend

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/health')"

CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
