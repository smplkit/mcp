FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

FROM python:3.13-slim

ARG APP_VERSION="dev"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    APP_VERSION=$APP_VERSION

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src ./src

EXPOSE 8000

# Stateless streamable-HTTP MCP server. /health (ALB probe) and the MCP
# endpoint at /api/mcp are both served by this app.
CMD ["uvicorn", "smplkit_mcp.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
