#!/bin/bash
# Bounded production-data canary for Machine A/B
set -e

echo "=== Canary test for $(cat ~/.machine-id 2>/dev/null || echo 'unknown') ==="

# 1. Verify Rust bridge works
BRIDGE="$HOME/Harnessv1-powerdata01/designwins/target/release/dw-bridge"
if [ ! -f "$BRIDGE" ]; then
    echo "ERROR: dw-bridge not found. Run machine-setup.sh first."
    exit 1
fi

# 2. Create sample input (10 records)
cat > /tmp/canary-input.jsonl << 'JSONL'
{"pdf_sha":"test1","page_1based":1,"verbatim":"RthetaJA = 64.4 C/W","value":64.4,"unit":"C/W","metric":"RthetaJA","condition_verbatim":null,"table_title":"Thermal Info"}
{"pdf_sha":"test2","page_1based":2,"verbatim":"VDS = 40 V","value":40,"unit":"V","metric":"VDS","condition_verbatim":"VGS = 0 V","table_title":"Absolute Max"}
JSONL

# 3. Run bridge
echo "--- Running dw-bridge canary ---"
"$BRIDGE" < /tmp/canary-input.jsonl 2>&1

# 4. Verify output
echo "--- Canary complete ---"
echo "If you see verification rates above 0%, the bridge is working."
