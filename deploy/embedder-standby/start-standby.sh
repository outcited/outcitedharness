#!/bin/bash
# Start the embedder standby on asus2 from the verified local weight copy.
#
# Guardrails, both deliberate:
#   - binds 127.0.0.1 ONLY. A standby that answers on the network can shadow the
#     production embedder, and port-number shadowing with different weights is
#     the 2026-08-29 incident class. Reach it with an ssh tunnel if you must.
#   - refuses port 8800 outright, which is production's port everywhere.
#
# Usage: ./start-standby.sh [PORT]        (default 8801)
set -euo pipefail

PORT="${1:-8801}"
R="${EMBEDDER_STANDBY_ROOT:-$HOME/embedder-rehearsal}"
MODEL_PATH="${BGE_MODEL_PATH:-$R/weights/bge-m3-fae-search-v4/checkpoints_20260822T045420Z}"

if [ "$PORT" = "8800" ]; then
  echo "refusing: 8800 is the production embedder port" >&2
  exit 2
fi
[ -d "$MODEL_PATH" ] || { echo "missing weights: $MODEL_PATH" >&2; exit 3; }
[ -x "$R/venv/bin/python" ] || { echo "missing venv: $R/venv" >&2; exit 3; }
[ -f "$R/app/bge_m3_service.py" ] || { echo "missing app: $R/app/bge_m3_service.py" >&2; exit 3; }

cd "$R/app"
BGE_MODEL_PATH="$MODEL_PATH" BGE_DEVICE=cuda BGE_BATCH_SIZE=64 \
  nohup "$R/venv/bin/python" -m uvicorn bge_m3_service:app \
  --host 127.0.0.1 --port "$PORT" > "$R/serve.log" 2>&1 < /dev/null &

echo "started pid=$! on 127.0.0.1:$PORT; model load takes ~2 min"
for i in $(seq 1 40); do
  sleep 5
  body=$(curl -s --max-time 5 "http://127.0.0.1:$PORT/healthz" || true)
  if echo "$body" | grep -q '"ready":true'; then
    echo "READY: $body"
    exit 0
  fi
done
echo "did not become ready in 200 s; see $R/serve.log" >&2
exit 1
