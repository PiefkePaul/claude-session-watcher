FROM python:3.12-slim

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
RUN pip install --no-cache-dir . \
  && python -m camoufox fetch

VOLUME ["/data"]
EXPOSE 47831

CMD ["claude-session-watcher", "serve"]
