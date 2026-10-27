# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 — build the virtualenv (wheels only end up in the runtime image)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip==24.3.1 \
    && /opt/venv/bin/pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2 — minimal runtime, unprivileged user, no build tools
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd --system --gid 10001 fluxpay \
    && useradd --system --uid 10001 --gid fluxpay --home-dir /app --shell /usr/sbin/nologin fluxpay

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=fluxpay:fluxpay app ./app
COPY --chown=fluxpay:fluxpay pyproject.toml README.md ./

RUN mkdir -p /app/data && chown -R fluxpay:fluxpay /app

USER fluxpay

EXPOSE 8000
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3); sys.exit(0 if r.status==200 else 1)"]

CMD ["python", "-m", "app"]
