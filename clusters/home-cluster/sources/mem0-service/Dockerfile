# mem0 memory service and management UI.
#
# Build for the amd64 k3s nodes from this directory:
#   docker buildx build --platform linux/amd64 \
#     -t example.invalid/portfolio/memory-service:REDACTED --push .

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create the runtime user before copying source so ownership is correct in a
# single layer. The cluster's restricted containers run this image as uid 1000.
RUN useradd -u 1000 -m -d /home/appuser appuser \
    && mkdir -p /app/app /data \
    && chown appuser:appuser /data
COPY --chown=appuser:appuser __init__.py main.py memory.py context_store.py /app/app/
COPY --chown=appuser:appuser ui/ /app/app/ui/

USER 1000

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
