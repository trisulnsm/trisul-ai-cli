# syntax=docker/dockerfile:1
# API-only image. The installed console script is still `trisul_ai_cli`;
# the entrypoint always launches `trisul_ai_cli api`.

FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml setup.cfg MANIFEST.in README.md ./
COPY trisul_ai_cli ./trisul_ai_cli
# Local session memory is excluded by .dockerignore. Ship an empty file so
# a fresh container starts with no user name or preferences.
RUN printf '%s\n' '[]' > trisul_ai_cli/trisul_ai_memory.json \
    && pip install --upgrade pip wheel \
    && pip wheel --wheel-dir /wheels .

FROM python:3.12-slim-bookworm

ARG TRISUL_AI_VERSION=1.0.2

LABEL org.opencontainers.image.title="Trisul AI" \
      org.opencontainers.image.description="Trisul AI REST API for WebTrisul. Starts the API even when no LLM is configured." \
      org.opencontainers.image.version="${TRISUL_AI_VERSION}" \
      org.opencontainers.image.source="https://github.com/trisulnsm/trisul-ai-cli" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    TRISUL_AI_HOST=0.0.0.0 \
    TRISUL_AI_PORT=8200 \
    TRISUL_AI_LOG_LEVEL=info \
    TRISUL_AI_LOG_DIR=/var/log/trisul-ai \
    TRISUL_AI_LOG_STDOUT=1 \
    TRISUL_AI_ENV_FILE=/var/lib/trisul-ai/.env

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libfreetype6 \
        libgomp1 \
        libpng16-16 \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/log/trisul-ai /var/lib/trisul-ai /etc/trisul-ai \
    && chmod 1777 /tmp

COPY --from=builder /wheels /wheels
RUN pip install --upgrade pip \
    && pip install /wheels/*.whl \
    && rm -rf /wheels

COPY docker/entrypoint.sh /usr/local/bin/trisul-ai-entrypoint
COPY docker/healthcheck.py /usr/local/bin/trisul-ai-healthcheck
RUN chmod 755 /usr/local/bin/trisul-ai-entrypoint /usr/local/bin/trisul-ai-healthcheck

WORKDIR /var/log/trisul-ai
EXPOSE 8200

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["/usr/local/bin/trisul-ai-healthcheck"]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/trisul-ai-entrypoint"]
