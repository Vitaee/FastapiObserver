#!/usr/bin/env bash
# ============================================================================
# generate_traffic.sh — Automated traffic generation for the demo stack
#
# Usage:
#   ./generate_traffic.sh              # Run for 60 seconds (default)
#   ./generate_traffic.sh 120          # Run for 120 seconds
#   APPS="http://localhost:8000" ./generate_traffic.sh   # Single app
# ============================================================================

set -euo pipefail

DURATION="${1:-60}"
APPS="${APPS:-http://localhost:8000 http://localhost:8001 http://localhost:8002}"
READY_TIMEOUT="${READY_TIMEOUT:-30}"

for value in "$DURATION" "$READY_TIMEOUT"; do
    if ! [[ "$value" =~ ^[1-9][0-9]*$ ]]; then
        echo "Duration and READY_TIMEOUT must be a positive integer." >&2
        exit 2
    fi
done
read -r -a APP_ARRAY <<< "$APPS"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

ENDPOINTS=(
    "/items/1"
    "/items/2"
    "/items/42"
    "/items/100"
    "/users/1"
    "/users/2"
    "/users/10"
    "/slow"
    "/error?status=400"
    "/error?status=500"
    "/chain"
    "/health"
)

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  fastapi-observer — Traffic Generator${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Duration:${NC}  ${DURATION}s"
echo -e "${GREEN}  Targets:${NC}   ${APPS}"
echo -e "${GREEN}  Endpoints:${NC} ${#ENDPOINTS[@]} routes"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Wait for apps to be ready
echo -e "${YELLOW}⏳ Waiting for services to be ready...${NC}"
DEADLINE=$((SECONDS + READY_TIMEOUT))
for app in "${APP_ARRAY[@]}"; do
    until curl -sf --max-time 1 "${app}/health" > /dev/null 2>&1; do
        if [ "$SECONDS" -ge "$DEADLINE" ]; then
            echo "Service not ready within ${READY_TIMEOUT}s: ${app}" >&2
            exit 1
        fi
        sleep 0.2
    done
    echo -e "${GREEN}  ✔ ${app} is ready${NC}"
done
echo ""

# Traffic generation
REQUEST_COUNT=0
ERROR_COUNT=0
START_TIME=$(date +%s)
END_TIME=$((START_TIME + DURATION))

echo -e "${GREEN}🚀 Generating traffic...${NC}"
echo ""

while [ "$(date +%s)" -lt "$END_TIME" ]; do
    # Pick a random app and endpoint
    APP=${APP_ARRAY[$((RANDOM % ${#APP_ARRAY[@]}))]}
    ENDPOINT=${ENDPOINTS[$((RANDOM % ${#ENDPOINTS[@]}))]}

    URL="${APP}${ENDPOINT}"
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "${URL}" 2>/dev/null) || STATUS=000
    REQUEST_COUNT=$((REQUEST_COUNT + 1))

    ELAPSED=$(($(date +%s) - START_TIME))
    REMAINING=$((DURATION - ELAPSED))

    if [ "$STATUS" -ge 400 ] 2>/dev/null; then
        ERROR_COUNT=$((ERROR_COUNT + 1))
        echo -e "  ${RED}✗${NC} ${STATUS} ${URL}  ${YELLOW}[${REMAINING}s left]${NC}"
    elif [ "$STATUS" = "000" ]; then
        ERROR_COUNT=$((ERROR_COUNT + 1))
        echo -e "  ${RED}✗${NC} TIMEOUT ${URL}  ${YELLOW}[${REMAINING}s left]${NC}"
    else
        echo -e "  ${GREEN}✔${NC} ${STATUS} ${URL}  ${YELLOW}[${REMAINING}s left]${NC}"
    fi

    # Random delay between 0.1s and 0.5s
    sleep "0.$(( (RANDOM % 4) + 1 ))"
done

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✅ Done!${NC}"
echo -e "${GREEN}  Total requests: ${REQUEST_COUNT}${NC}"
echo -e "${RED}  Errors:         ${ERROR_COUNT}${NC}"
echo -e "${GREEN}  Success rate:   $(( (REQUEST_COUNT - ERROR_COUNT) * 100 / (REQUEST_COUNT > 0 ? REQUEST_COUNT : 1) ))%${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${YELLOW}📊 Open Grafana: http://localhost:3000${NC}"
echo -e "${YELLOW}   Dashboard: FastAPI Observer — API Overview${NC}"
