FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/app/data

WORKDIR /app

# Install deps first for better layer caching
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# App code
COPY backend/ backend/
COPY frontend/ frontend/

# --- run as a non-root user ---------------------------------------------------
# uid 10001, primary group root (gid 0) + group-writable data dir. This is the
# "arbitrary uid" pattern: the app never runs as root, yet the data volume stays
# writable even if you override the runtime uid.
RUN useradd -r -u 10001 -g root appuser \
 && mkdir -p /app/data \
 && chown -R 10001:0 /app \
 && chmod -R g=u /app/data

USER 10001

EXPOSE 8000
WORKDIR /app/backend
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
