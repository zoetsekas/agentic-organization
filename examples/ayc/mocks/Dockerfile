# A mock of one of AYC's backing systems (ADR-0109). `MOCK_SYSTEM` picks which
# of the five; its seed is baked in, and a restart puts the seed back.
#
#   docker build -t ayc-local/mock-system examples/ayc/mocks
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MOCK_PORT=8080

WORKDIR /mock
COPY server.py ./
COPY seed/ ./seed/

# Nothing here needs root, so nothing here runs as root.
RUN useradd --system --no-create-home --shell /usr/sbin/nologin --uid 10005 mock
USER mock

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"

CMD ["python", "server.py"]
