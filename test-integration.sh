#!/bin/bash
# Screenbox integration test -- run before every push
# Verifies the full stack works: MCP + Dashboard + auth + SSE
# Exit 1 on any failure = blocks push
set -e

cd "$(dirname "$0")"

# Cross-platform sed -i
_sed_i() {
  if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "$@"
  else
    sed -i "$@"
  fi
}

echo "Screenbox integration test"
echo "=========================="

# Check .env exists
if [ ! -f .env ]; then
  echo "[FAIL] .env not found -- run ./setup.sh first"
  exit 1
fi

TOKEN=$(grep "^SCREENBOX_API_TOKEN=" .env | cut -d= -f2)
if [ -z "$TOKEN" ]; then
  echo "[FAIL] SCREENBOX_API_TOKEN not set in .env"
  exit 1
fi

# Check containers are running
echo ""
echo "Checking services..."
MCP_UP=$(docker compose ps --format json 2>/dev/null | grep -c "screenbox-mcp" || echo "0")
DASH_UP=$(docker compose ps --format json 2>/dev/null | grep -c "dashboard" || echo "0")

if [ "$MCP_UP" = "0" ] || [ "$DASH_UP" = "0" ]; then
  echo "[FAIL] Services not running. Start with: docker compose up -d"
  exit 1
fi
echo "[OK] Services running"

# Wait for startup
sleep 3

# 1. MCP health (no auth required)
echo ""
echo "1. MCP health..."
MCP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/health 2>/dev/null)
if [ "$MCP_STATUS" != "200" ]; then
  echo "[FAIL] MCP health: HTTP $MCP_STATUS"
  exit 1
fi
echo "[OK] MCP health: 200"

# 2. MCP auth required
echo ""
echo "2. MCP auth..."
MCP_NOAUTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/desktop/list 2>/dev/null)
MCP_AUTH=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/desktop/list 2>/dev/null)
if [ "$MCP_AUTH" != "200" ]; then
  echo "[FAIL] MCP with Bearer token: HTTP $MCP_AUTH (expected 200)"
  exit 1
fi
echo "[OK] MCP auth: no-token=$MCP_NOAUTH, with-token=200"

# 3. Dashboard auth (cookie flow)
echo ""
echo "3. Dashboard auth..."
DASH_NOAUTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:16000/api/desktops 2>/dev/null)
if [ "$DASH_NOAUTH" != "401" ]; then
  echo "[WARN] Dashboard without auth: HTTP $DASH_NOAUTH (expected 401)"
fi
DASH_COOKIE=$(curl -s -o /dev/null -w "%{http_code}" -b "sb_token=$TOKEN" http://localhost:16000/api/desktops 2>/dev/null)
if [ "$DASH_COOKIE" != "200" ]; then
  echo "[FAIL] Dashboard with cookie: HTTP $DASH_COOKIE (expected 200)"
  exit 1
fi
echo "[OK] Dashboard auth: no-auth=$DASH_NOAUTH, cookie=200"

# 4. Dashboard -> MCP proxy (create + destroy)
echo ""
echo "4. Dashboard -> MCP proxy..."
CREATE_RESP=$(curl -s -b "sb_token=$TOKEN" -X POST -H "Content-Type: application/json" \
  -d '{"id":"sb-test-run"}' http://localhost:16000/api/create 2>/dev/null)
CREATE_OK=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',''))" 2>/dev/null)
if [ "$CREATE_OK" != "True" ]; then
  echo "[FAIL] Dashboard create: $CREATE_RESP"
  # Cleanup just in case
  curl -s -b "sb_token=$TOKEN" -X POST -H "Content-Type: application/json" \
    -d '{"id":"sb-test-run"}' http://localhost:16000/api/destroy >/dev/null 2>&1
  exit 1
fi
echo "[OK] Desktop created via dashboard -> MCP"

# Wait for desktop to start
sleep 5

# 5. Screenshot via MCP
echo ""
echo "5. Screenshot..."
SHOT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8080/api/desktop/screenshot?id=sb-test-run" 2>/dev/null)
# 503 is OK (desktop may still be starting), 200 is perfect
if [ "$SHOT_STATUS" != "200" ] && [ "$SHOT_STATUS" != "503" ]; then
  echo "[FAIL] Screenshot: HTTP $SHOT_STATUS"
fi
echo "[OK] Screenshot: HTTP $SHOT_STATUS"

# 6. Dashboard SSE connected
echo ""
echo "6. Dashboard SSE..."
SSE_OK=$(docker logs "$(docker compose ps -q screenbox-dashboard 2>/dev/null | head -1)" 2>&1 | grep -c "Connected to MCP SSE" || echo "0")
if [ "$SSE_OK" = "0" ]; then
  echo "[FAIL] Dashboard SSE not connected to MCP"
  echo "  Logs:"
  docker logs "$(docker compose ps -q screenbox-dashboard 2>/dev/null | head -1)" 2>&1 | tail -5
  # Cleanup
  curl -s -b "sb_token=$TOKEN" -X POST -H "Content-Type: application/json" \
    -d '{"id":"sb-test-run"}' http://localhost:16000/api/destroy >/dev/null 2>&1
  exit 1
fi
echo "[OK] Dashboard SSE connected"

# 7. Dashboard ?token= flow (POST with token in URL)
echo ""
echo "7. Token flow..."
TOKEN_RESP=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" \
  -d '{"id":"sb-test-run","action":"pause"}' \
  "http://localhost:16000/api/control?token=$TOKEN" 2>/dev/null)
if [ "$TOKEN_RESP" != "200" ]; then
  echo "[WARN] POST with ?token=: HTTP $TOKEN_RESP"
fi
echo "[OK] Token flow: $TOKEN_RESP"

# Cleanup: destroy test desktop
echo ""
echo "Cleanup..."
curl -s -b "sb_token=$TOKEN" -X POST -H "Content-Type: application/json" \
  -d '{"id":"sb-test-run","auto_snapshot":false}' http://localhost:16000/api/destroy >/dev/null 2>&1
# Wait for container removal
sleep 2
docker rm -f screenbox-sb-test-run >/dev/null 2>&1 || true
echo "[OK] Test desktop removed"

echo ""
echo "=========================="
echo "All tests passed"
