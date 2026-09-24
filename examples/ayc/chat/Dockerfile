# AYC chat (ADR-0109): a page and a router to the agents' own workers.
#
#   docker build -t ayc-local/chat examples/ayc/chat
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHAT_PORT=8080

WORKDIR /chat
# Ed25519 for signing approvals (ADR-0114); bounded like the platform's pin.
RUN pip install --no-cache-dir "cryptography>=42,<47"
COPY server.py ./
COPY static/ ./static/

RUN useradd --system --no-create-home --shell /usr/sbin/nologin --uid 10006 chat
USER chat

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"

CMD ["python", "server.py"]
