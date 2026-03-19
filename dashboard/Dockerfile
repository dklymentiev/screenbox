FROM python:3.12-slim@sha256:ccc7089399c8bb65dd1fb3ed6d55efa538a3f5e7fca3f5988ac3b5b87e593bf0
LABEL org.screenbox.component="dashboard"
ENV PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir aiohttp
WORKDIR /app
COPY server.py config.py state.py docker_helpers.py mcp_client.py mcp_events.py index.html view.html share.html ./
COPY routes/ ./routes/
COPY novnc/ ./novnc/
COPY static/ ./static/
RUN useradd -r -u 1000 -m screenbox
USER screenbox

EXPOSE 16000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:16000/', timeout=3)" || exit 1

# Single port: HTTP + WebSocket (VNC/RDP proxy) on 16000
CMD ["python3", "server.py"]
