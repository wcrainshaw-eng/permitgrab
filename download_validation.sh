#!/bin/bash
# PermitGrab - Download Validation Results from Server
# V12.31: Fetches endpoint validation files for local processing

set -e

# Configuration
SERVER_URL="${PERMITGRAB_URL:-https://permitgrab.com}"
ADMIN_KEY="${ADMIN_KEY:-permitgrab-reset-2026}"
DATA_DIR="$(dirname "$0")/data"

echo "========================================"
echo "PermitGrab Validation Results Download"
echo "========================================"
echo "Server: $SERVER_URL"
echo "Output: $DATA_DIR/"
echo ""

# Create data directory if it doesn't exist
mkdir -p "$DATA_DIR"

# Download endpoint_validation.json
echo "Downloading endpoint_validation.json..."
HTTP_CODE=$(curl -s -w "%{http_code}" \
    -H "X-Admin-Key: $ADMIN_KEY" \
    "$SERVER_URL/api/admin/validation-results" \
    -o "$DATA_DIR/endpoint_validation.json")

if [ "$HTTP_CODE" = "200" ]; then
    echo "  OK: endpoint_validation.json downloaded"
else
    echo "  ERROR: HTTP $HTTP_CODE"
    cat "$DATA_DIR/endpoint_validation.json"
    exit 1
fi

# Download suggested_fixes.json
echo "Downloading suggested_fixes.json..."
HTTP_CODE=$(curl -s -w "%{http_code}" \
    -H "X-Admin-Key: $ADMIN_KEY" \
    "$SERVER_URL/api/admin/suggested-fixes" \
    -o "$DATA_DIR/suggested_fixes.json")

if [ "$HTTP_CODE" = "200" ]; then
    echo "  OK: suggested_fixes.json downloaded"
else
    echo "  ERROR: HTTP $HTTP_CODE"
    cat "$DATA_DIR/suggested_fixes.json"
    exit 1
fi

# Show summary
echo ""
echo "========================================"
echo "Download Complete!"
echo "========================================"

# Count fixes
if [ -f "$DATA_DIR/suggested_fixes.json" ]; then
    FIX_COUNT=$(python3 -c "import json; print(len(json.load(open('$DATA_DIR/suggested_fixes.json'))))" 2>/dev/null || echo "?")
    echo "  Suggested fixes: $FIX_COUNT"
fi

if [ -f "$DATA_DIR/endpoint_validation.json" ]; then
    python3 -c "
import json
data = json.load(open('$DATA_DIR/endpoint_validation.json'))
print(f\"  Working: {len(data.get('working', []))}\")
print(f\"  Wrong fields: {len(data.get('wrong_fields', []))}\")
print(f\"  Dead URLs: {len(data.get('dead_url', []))}\")
print(f\"  Discoveries: {len(data.get('discoveries', []))}\")
" 2>/dev/null || true
fi

echo ""
echo "Next steps:"
echo "  1. Preview changes:  python apply_fixes.py --preview"
echo "  2. Apply fixes:      python apply_fixes.py --apply"
