FROM python:3.12-slim@sha256:ccc7089399c8bb65dd1fb3ed6d55efa538a3f5e7fca3f5988ac3b5b87e593bf0
LABEL org.screenbox.component="mcp"

# Install Docker CLI from official repo (docker.io in Debian Trixie lacks the CLI binary)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && apt-get purge -y gnupg && apt-get autoremove -y \
    && apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/
COPY docker/vision-watch.py /opt/vision-watch.py

RUN pip install --no-cache-dir .

# Run as non-root (docker access via DOCKER_HOST tcp proxy, no socket needed)
RUN groupadd -g 1000 screenbox \
    && useradd -r -u 1000 -g screenbox -m screenbox

ENV SCREENBOX_TRANSPORT=both
ENV SCREENBOX_HOST=0.0.0.0
ENV SCREENBOX_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -sf http://localhost:8080/api/health || exit 1

# Start as root to fix volume permissions, then drop to screenbox
RUN apt-get update && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*
CMD ["bash", "-c", "chown -R screenbox:screenbox ${SCREENBOX_BASE_DIR:-/data/screenbox} 2>/dev/null; exec gosu screenbox python -m screenbox"]
