#!/bin/bash
# Screenbox setup & update
# Works on Linux, macOS, Windows (Git Bash / WSL)
# First run: generates .env, builds images, shows onboarding
# Update: rebuilds images, restarts services
set -e

cd "$(dirname "$0")"

# Cross-platform sed -i (macOS requires '' argument)
_sed_i() {
  if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "$@"
  else
    sed -i "$@"
  fi
}

# Detect: first install or update
IS_UPDATE=false
if [ -f .env ] && docker compose ps --quiet 2>/dev/null | grep -q .; then
  IS_UPDATE=true
fi

if [ "$IS_UPDATE" = true ]; then
  echo "Screenbox update"
  echo "================"
else
  echo "Screenbox setup"
  echo "==============="
fi
echo ""

# 1. Generate .env if missing
if [ ! -f .env ]; then
  TOKEN=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")
  cp .env.example .env
  _sed_i "s/^SCREENBOX_API_TOKEN=$/SCREENBOX_API_TOKEN=${TOKEN}/" .env
  echo "[OK] Created .env with API token"
else
  echo "[OK] .env already exists"
fi

# Check token is set
TOKEN_VAL=$(grep "^SCREENBOX_API_TOKEN=" .env | cut -d= -f2)
if [ -z "$TOKEN_VAL" ]; then
  TOKEN=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")
  _sed_i "s/^SCREENBOX_API_TOKEN=$/SCREENBOX_API_TOKEN=${TOKEN}/" .env
  TOKEN_VAL="$TOKEN"
  echo "[OK] Generated API token"
fi

# 2. Create data directories and set host paths
mkdir -p data/desktops data/logs data/knowledge data/recordings
if ! grep -q "^SCREENBOX_RECORDINGS_HOST_DIR=" .env 2>/dev/null; then
  echo "SCREENBOX_RECORDINGS_HOST_DIR=$(pwd)/data/recordings" >> .env
fi
echo "[OK] Data directories"

# 3. Generate .mcp.json for Claude Code
TOKEN_VAL=$(grep "^SCREENBOX_API_TOKEN=" .env | cut -d= -f2)
cat > .mcp.json << MCPEOF
{
  "mcpServers": {
    "screenbox": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer ${TOKEN_VAL}"
      }
    }
  }
}
MCPEOF
echo "[OK] .mcp.json"

# 4. Build all images
echo ""
echo "Building desktop image..."
docker build -f docker/Dockerfile -t screenbox:latest docker/
echo "[OK] Desktop image"

echo ""
echo "Building MCP server and dashboard..."
docker compose build screenbox-mcp screenbox-dashboard
echo "[OK] All images built"

# 5. Stop old containers and start new ones
echo ""
if [ "$IS_UPDATE" = true ]; then
  echo "Restarting services..."
  docker compose down 2>/dev/null || true
fi
docker compose up -d
echo "[OK] Services running"

# 5b. Create demo desktop (first install only)
if [ "$IS_UPDATE" = false ]; then
  echo ""
  echo "Creating demo desktop..."
  MCP_PORT=$(docker compose port screenbox-mcp 8080 2>/dev/null | cut -d: -f2)
  MCP_PORT=\${MCP_PORT:-8080}
  # Wait for MCP to be healthy (up to 30s)
  for i in $(seq 1 6); do
    docker compose ps screenbox-mcp 2>/dev/null | grep -q healthy && break
    sleep 5
  done
  MCP_PORT=${MCP_PORT:-8080}
  MCP_CONTAINER=$(docker compose ps -q screenbox-mcp 2>/dev/null)
  RESULT=$(docker exec "$MCP_CONTAINER" python3 -c "
import httpx, asyncio, os
async def go():
    token = os.environ["SCREENBOX_API_TOKEN"]
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient(timeout=60) as c:
        await c.post("http://localhost:8080/mcp", headers=h, json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"setup","version":"1.0"}}})
        r = await c.post("http://localhost:8080/mcp", headers=h, json={"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"desktop_manage","arguments":{"action":"create","desktop_id":"desktop-1","label":"My Desktop"}}})
        print(r.text[:200])
asyncio.run(go())
" 2>&1)
  if echo "$RESULT" | grep -q running; then
    echo "[OK] Demo desktop created (desktop-1)"
  else
    echo "[WARN] Could not create demo desktop -- create one from Dashboard"
  fi
fi

# 6. Integration test
echo ""
echo "Running integration test..."
if ./test-integration.sh; then
  echo "[OK] Integration test passed"
else
  echo ""
  echo "[WARN] Integration test failed -- services may still be starting."
  echo "       Wait 30 seconds and try: curl http://localhost:8080/mcp"
fi

# 7. Show result
echo ""
echo "==============="
if [ "$IS_UPDATE" = true ]; then
  echo "Update complete!"
  echo ""
  echo "Services restarted on new images."
  echo "Existing desktops use the old image -- recreate them:"
  echo "  - Dashboard: destroy + create"
  echo "  - Or: curl -s -X POST -H 'Content-Type: application/json' \\"
  echo "    -d '{\"id\":\"desktop-1\",\"auto_snapshot\":false}' http://localhost:8080/api/desktop/destroy"
  echo "  - Then: curl -s -X POST -H 'Content-Type: application/json' \\"
  echo "    -d '{\"id\":\"desktop-1\"}' http://localhost:8080/api/desktop/create"
else
  echo "Screenbox ready!"
  echo ""
  echo "Dashboard:"
  echo "  http://localhost:16000?token=${TOKEN_VAL}"
  echo ""
  echo "Connect Claude Code (option A -- from this directory):"
  echo "  cd $(pwd) && claude"
  echo "  (.mcp.json is already configured)"
  echo ""
  echo "Connect Claude Code (option B -- from anywhere):"
  echo "  claude mcp add screenbox --transport http \\"
  echo "    --header \"Authorization: Bearer ${TOKEN_VAL}\" \\"
  echo "    http://localhost:8080/mcp"
fi
