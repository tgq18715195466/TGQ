FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app
COPY --chown=app:app backend/ ./backend/
COPY --chown=app:app src/agent/ ./src/agent/
COPY --chown=app:app src/api_view/ ./src/api_view/
COPY --chown=app:app src/mcp_server/ ./src/mcp_server/
COPY --chown=app:app src/skills/ ./src/skills/
COPY --chown=app:app deploy/healthcheck.py deploy/run_web.py ./deploy/
RUN mkdir -p /app/src/local_sandbox /app/src/download \
    && chown -R app:app /app
USER app
EXPOSE 8080 8001 8090
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8080"]
