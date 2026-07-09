# --------------------------------------------------------------
# Builder stage - install exactly the deps we need
# --------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --------------------------------------------------------------
# Runtime stage - copy only what we really need
# --------------------------------------------------------------
FROM python:3.12-slim

RUN useradd -m -u 1000 appuser
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/ /usr/local/bin/

COPY api/server.py .
COPY data/ ./data/

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8002

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8002"]