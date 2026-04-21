# Red Hat UBI Python image - accessible from OpenShift
FROM registry.access.redhat.com/ubi9/python-311:latest AS base

# UBI image already has non-root user (default uid 1001)
WORKDIR /opt/app-root/src

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY config.yaml .

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health').raise_for_status()" || exit 1

# Default: run API
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8080"]