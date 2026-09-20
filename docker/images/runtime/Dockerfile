# orgagents-runtime: one tenant's agent runtime — one container per tenant
# (ADR-0049, ADR-0050, ADR-0053).
#
# Built from this repository, FROM python:3.11-slim like every other image we
# build, so there is one base and one patch cadence. Build it from the
# repository root:
#
#   docker build -f docker/images/runtime/Dockerfile -t orgagents-runtime:local .
#
# One image, one container per tenant, never one container serving two: the
# tenant boundary is a process and a volume boundary here, not a row filter
# (ADR-0050). The sandbox a tenant's code executes in is a different image
# again — the toolchain images in docker/images.lock.

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
      "python-multipart>=0.0.9" "pyyaml>=6.0"

FROM base AS runtime
# A non-root user with no login shell: an agent runtime that could log in is
# an agent runtime that can be logged into.
RUN useradd --system --create-home --shell /usr/sbin/nologin --uid 10004 runtime
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    ORGAGENTS_DB=/data/runtime.db \
    ORGAGENTS_HOST=0.0.0.0 \
    ORGAGENTS_PORT=8000 \
    ORGAGENTS_BASE_URL=http://localhost:8000

WORKDIR /app
COPY --chown=runtime:runtime pyproject.toml README.md ./
COPY --chown=runtime:runtime src/ ./src/
# api.py resolves the web assets relative to its own file (parents[2]/web), so
# the layout has to match the repository even for a plane that serves no UI.
COPY --chown=runtime:runtime web/ ./web/
COPY --chown=runtime:runtime docker/images/runtime/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p /data && chown runtime:runtime /data

# This tenant's sessions and memory live here, on this tenant's own volume.
# Sharing one volume between tenants would undo the boundary the rest of the
# design pays for.
VOLUME ["/data"]
EXPOSE 8000
USER runtime

# Hits the application's own health endpoint rather than a port check, so a
# process that is up but broken is reported unhealthy.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('ORGAGENTS_PORT','8000')+'/healthz', timeout=2).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["serve"]
