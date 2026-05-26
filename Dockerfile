FROM python:3.12-slim-bookworm

ARG VERSION=0.1.0b1
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="Claude Session Watcher" \
      org.opencontainers.image.description="Background watcher that pauses and resumes selected Claude Code Remote Control sessions near usage limits." \
      org.opencontainers.image.source="https://github.com/PiefkePaul/claude-session-watcher" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CSW_HOST=0.0.0.0 \
    CSW_PORT=47831 \
    CSW_DATA_DIR=/data \
    CSW_CAMOUFOX_HEADLESS=virtual

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libgtk-3-0 \
    libx11-xcb1 \
    libasound2 \
    libnss3 \
    libxss1 \
    xvfb \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[full]" \
  && python -m camoufox fetch

VOLUME ["/data"]
EXPOSE 47831

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:47831/health', timeout=5).read()" || exit 1

CMD ["claude-session-watcher", "serve"]
