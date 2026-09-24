# orgagents-fabric: the control plane API — tenants, deployments, quotas,
# health (ADR-0049, ADR-0053).
#
# Built from this repository, FROM python:3.11-slim like every other image we
# build, so there is one base and one patch cadence. Build it from the
# repository root:
#
#   docker build -f docker/images/fabric/Dockerfile -t orgagents-fabric:local .
#
# It is not the designer image. The designer authors specs; this one decides
# whether, where and for whom they run, and the two are separate failure
# domains on purpose.

FROM python:3.11-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

FROM base AS builder
WORKDIR /app
# Dependencies resolve from the project metadata alone, so this layer is
# rebuilt only when the metadata changes, not on every source edit.
COPY pyproject.toml README.md ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install \
      "pydantic>=2.6" "fastapi>=0.110" "uvicorn>=0.29" \
      "python-multipart>=0.0.9" "pyyaml>=6.0" "cryptography>=42,<47"

FROM base AS runtime
# A non-root user with no login shell: the control plane writes to one
# directory and nothing else needs an identity that can log in.
RUN useradd --system --create-home --shell /usr/sbin/nologin --uid 10002 fabric
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    ORGAGENTS_DB=/data/fabric.db \
    ORGAGENTS_HOST=0.0.0.0 \
    ORGAGENTS_PORT=8000 \
    ORGAGENTS_BASE_URL=http://localhost:8000

WORKDIR /app
COPY --chown=fabric:fabric pyproject.toml README.md ./
COPY --chown=fabric:fabric src/ ./src/
# api.py resolves the web assets relative to its own file (parents[2]/web), so
# the layout has to match the repository even for a plane that serves no UI.
COPY --chown=fabric:fabric web/ ./web/
COPY --chown=fabric:fabric docker/images/fabric/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p /data && chown fabric:fabric /data

# The fabric's own records — tenants, deployments, quota counters — are the
# only mutable state, and they do not belong on the image layer.
VOLUME ["/data"]
EXPOSE 8000
USER fabric

# Hits the application's own health endpoint rather than a port check, so a
# process that is up but broken is reported unhealthy.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('ORGAGENTS_PORT','8000')+'/healthz', timeout=2).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["serve"]
