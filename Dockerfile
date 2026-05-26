FROM python:3.12-slim-bookworm

ARG VERSION=0.1.0b2
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
    CSW_CAMOUFOX_HEADLESS=false \
    CSW_BROWSER_CONSOLE_PUBLIC_PORT=47832 \
    CSW_ENABLE_VNC=true \
    CSW_VNC_PORT=6080 \
    CSW_VNC_SCREEN=1920x1080x24 \
    DISPLAY=:99

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    dbus-x11 \
    fluxbox \
    fonts-dejavu-core \
    libgtk-3-0 \
    libdbus-glib-1-2 \
    libgbm1 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libxrender1 \
    libxt6 \
    libasound2 \
    libnss3 \
    libxss1 \
    novnc \
    websockify \
    x11vnc \
    xvfb \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts/docker-entrypoint.sh /usr/local/bin/csw-docker-entrypoint
RUN pip install --no-cache-dir ".[full]" \
  && chmod +x /usr/local/bin/csw-docker-entrypoint \
  && python -m camoufox fetch

VOLUME ["/data"]
EXPOSE 47831 6080

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:47831/health', timeout=5).read()" || exit 1

ENTRYPOINT ["csw-docker-entrypoint"]
CMD ["claude-session-watcher", "serve"]
