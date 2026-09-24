# The agentic designer application itself, as a container.
#
# Note this is *not* the same thing as the Compose stack the compiler
# generates for a designed system (ADR-0011, `orgagents compile --target
# local`). That output describes somebody's agents; this image runs the
# designer they build them in.
#
# The package is installed, but PYTHONPATH still points at /app/src and the
# source tree is kept: `api.py` resolves the web assets relative to its own
# file, so the UI is only found when the layout on disk matches the repo.
# Changing that resolution belongs in the application, not in a Dockerfile
# workaround.

FROM python:3.11-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

FROM base AS builder
WORKDIR /app
# Dependencies resolve from the project metadata alone, so this layer is
# rebuilt only when the metadata changes, not on every source edit.
# constraints.txt pins every version, so the image installs what CI tested
# rather than whatever resolved on the day it was built.
COPY pyproject.toml README.md constraints.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install -c constraints.txt \
      "pydantic>=2.6" "fastapi>=0.110" "uvicorn>=0.29" \
      "python-multipart>=0.0.9" "pyyaml>=6.0" "cryptography>=42,<47"       "sqlalchemy>=2,<3" "psycopg[binary]>=3.1,<4"

FROM base AS runtime
# A non-root user with no login shell: the designer writes to one directory
# and nothing else needs an identity that can log in.
RUN useradd --system --create-home --shell /usr/sbin/nologin --uid 10001 designer
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    ORGAGENTS_DB=/data/designer.db \
    ORGAGENTS_HOST=0.0.0.0 \
    ORGAGENTS_PORT=8000 \
    ORGAGENTS_BASE_URL=http://localhost:8000

WORKDIR /app
COPY --chown=designer:designer pyproject.toml README.md ./
COPY --chown=designer:designer src/ ./src/
COPY --chown=designer:designer web/ ./web/
COPY --chown=designer:designer examples/ ./examples/
COPY --chown=designer:designer docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
 && chmod +x /usr/local/bin/entrypoint.sh \
 && mkdir -p /data && chown designer:designer /data

# /data is where the only mutable state lives. Declared so a `docker run`
# without an explicit volume still keeps the database off the image layer.
VOLUME ["/data"]
EXPOSE 8000
USER designer

# Hits the application's own health endpoint rather than a port check, so a
# process that is up but broken is reported unhealthy.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('ORGAGENTS_PORT','8000')+'/healthz', timeout=2).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["serve"]
