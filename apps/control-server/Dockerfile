FROM python:3.11-slim@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PROBE_DB_PATH=/data/probe.db

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap git \
    && rm -rf /var/lib/apt/lists/*

COPY apps/control-server/requirements.lock ./requirements.lock
RUN pip install --require-hashes -r requirements.lock

COPY apps/control-server/pyproject.toml ./
COPY apps/control-server/app ./app
COPY packages/python-probe /opt/probe-agent/packages/python-probe
RUN pip install --no-build-isolation --no-deps . \
    && pip install --no-build-isolation --no-deps /opt/probe-agent/packages/python-probe

RUN mkdir -p /data /workspace /execution-spool /execution-workspaces \
    && chown 65534:65534 /execution-spool /execution-workspaces \
    && chmod -R a+rX /app/app
VOLUME ["/data"]

EXPOSE 8000

# interview_refresh_job currently relies on process-local serialization.
# Keep one ASGI worker until the multi-worker prerequisites documented in
# apps/control-server/README.md and docs/deployment-https.md are implemented.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
