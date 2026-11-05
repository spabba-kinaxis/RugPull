FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY rugpull_mcp ./rugpull_mcp

# Run as a non-root user. The baseline is mounted read-only at /baseline.
RUN useradd --uid 10001 --no-create-home appuser
USER 10001

EXPOSE 8000
ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    BASELINE_PATH=/baseline/approved-tools.json

ENTRYPOINT ["python", "-m", "rugpull_mcp"]
