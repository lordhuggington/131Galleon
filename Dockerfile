FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build            # outDir ../static, so this writes /static

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Los_Angeles \
    HRS_DB_PATH=/data/house.db \
    HRS_PHOTOS_DIR=/data/photos

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --system --uid 10001 hrs \
 && mkdir -p /data && chown hrs /data

COPY app ./app
COPY migrations ./migrations
COPY seed ./seed
COPY --from=frontend /static ./static

USER hrs
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

# One worker on purpose: SQLite, the login throttle and menu jobs all live in this process.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
