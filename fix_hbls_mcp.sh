#!/bin/bash
# fix_hbls_mcp.sh — Apply dispatch bug fix, rebuild image, restart container
set -e

CONTAINER_NAME="hbls-mcp-test"
IMAGE="hbls-mcp:latest"
SRC="/home/dh/hbls_mcp/server.py"

echo "=== HBLS MCP Fix ==="

# 1. Check if already patched
if grep -q "return await mcp_asgi" "$SRC"; then
    echo "[1/5] Applying bug fix to $SRC ..."
    sed -i 's/return await mcp_asgi(request.scope, request.receive, request._send)/await mcp_asgi(request.scope, request.receive, request._send)/' "$SRC"
else
    echo "[1/5] Bug fix already applied"
fi

# 2. Rebuild Docker image
echo "[2/5] Rebuilding Docker image ..."
cd /home/dh/hbls_mcp
docker build -t "$IMAGE" . --no-cache | tail -5

# 3. Stop and remove old container
echo "[3/5] Removing old container ..."
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

# 4. Start new container
echo "[4/5] Starting new container ..."
docker run -d --name "$CONTAINER_NAME" \
    -p 8003:8003 \
    -v /home/dh/hbls_data:/data \
    -e PYTHONUNBUFFERED=1 \
    "$IMAGE"

# 5. Wait and verify
echo "[5/5] Waiting for server to come up ..."
sleep 6

# Test MCP flow
SID=$(curl -sN http://127.0.0.1:8003/sse \
    -H "Accept: text/event-stream" \
    -H "Host: 127.0.0.1" 2>/dev/null \
    | grep -m1 "session_id=" | cut -d= -f2 | cut -d\& -f1)

if [ -z "$SID" ]; then
    echo "FAIL: Could not get SSE session"
    docker logs "$CONTAINER_NAME" --tail 10
    exit 1
fi
echo "SSE session: $SID"

RESULT=$(curl -s -X POST "http://127.0.0.1:8003/messages/?session_id=$SID" \
    -H "Content-Type: application/json" \
    -H "Host: 127.0.0.1" \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}')

STATUS=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if 'tools' in str(d) else 'FAIL: '+str(d)[:100])" 2>/dev/null || echo "FAIL: $RESULT")

if [ "$STATUS" = "OK" ]; then
    TOOLS=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); t=[t['name'] for t in d.get('result',{}).get('tools',[])]; print(f'{len(t)} tools: {t}')")
    echo "✓ POST /messages → $STATUS"
    echo "  $TOOLS"
    echo ""
    echo "=== All tests passed — HBLS MCP is working ==="
else
    echo "✗ POST /messages → $STATUS"
    docker logs "$CONTAINER_NAME" --tail 5
    exit 1
fi