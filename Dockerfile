FROM python:3.11-slim AS base

# Security: Create non-root user
RUN groupadd -r mcp && useradd -r -g mcp mcp

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY config.yaml .

# Set ownership
RUN chown -R mcp:mcp /app

USER mcp

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8080/health').raise_for_status()" || exit 1

# Default: run API
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8080"]