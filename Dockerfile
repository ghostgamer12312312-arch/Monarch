FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/app/data/monarch.sqlite3
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 monarch \
    && mkdir -p /app/data \
    && chown -R monarch:monarch /app
COPY monarchbot ./monarchbot
USER monarch
VOLUME ["/app/data"]
CMD ["python", "-m", "monarchbot.main"]
