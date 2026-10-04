#!/usr/bin/env bash
# 通过真实 HTTP 请求（127.0.0.1:8000）对运行中的服务做端到端冒烟测试。
set -euo pipefail
BASE=http://127.0.0.1:8000/api/v1

echo '--- register ---'
REG=$(curl -s -X POST "$BASE/devices/register" -H 'Content-Type: application/json' -d '{
  "device_id": "atlas_live_01",
  "device_name": "线上冒烟设备",
  "device_type": "edge_ai_device",
  "manufacturer": "Huawei",
  "model": "Atlas 200I DK A2",
  "capabilities": ["emotion"]
}')
echo "$REG" | head -c 200; echo
TOKEN=$(echo "$REG" | python3 -c 'import sys,json; print(json.load(sys.stdin)["device_token"])')

echo '--- heartbeat ---'
curl -s -X POST "$BASE/devices/atlas_live_01/heartbeat" \
  -H "X-Device-Id: atlas_live_01" -H "X-Device-Token: $TOKEN" -d '{}'
echo

echo '--- create session ---'
SESS=$(curl -s -X POST "$BASE/sessions" -H 'Content-Type: application/json' \
  -H "X-Device-Id: atlas_live_01" -H "X-Device-Token: $TOKEN" \
  -d '{"device_id":"atlas_live_01","session_type":"emotion"}')
echo "$SESS"
SID=$(echo "$SESS" | python3 -c 'import sys,json; print(json.load(sys.stdin)["session_id"])')

echo '--- upload timeline ---'
UP=$(curl -s -X POST "$BASE/emotion/sessions/$SID/data" \
  -H 'Content-Type: application/json' \
  -H "X-Device-Id: atlas_live_01" -H "X-Device-Token: $TOKEN" -d "{
    \"client_request_id\": \"live-req-0001\",
    \"session_meta\": {\"video_frames\": 100, \"face_detected_frames\": 80},
    \"timeline\": [
      {\"relative_seconds\": 0.5, \"expression\": \"neutral\", \"confidence\": 0.9},
      {\"relative_seconds\": 1.0, \"expression\": \"happiness\", \"confidence\": 0.8},
      {\"relative_seconds\": 1.5, \"expression\": \"happiness\", \"confidence\": 0.7}
    ]
  }")
echo "$UP" | python3 -m json.tool

echo '--- get result ---'
curl -s "$BASE/emotion/sessions/$SID/result" | python3 -m json.tool

echo '--- duplicate upload ---'
curl -s -X POST "$BASE/emotion/sessions/$SID/data" \
  -H 'Content-Type: application/json' \
  -H "X-Device-Id: atlas_live_01" -H "X-Device-Token: $TOKEN" -d "{
    \"client_request_id\": \"live-req-0001\",
    \"session_meta\": {},
    \"timeline\": [
      {\"relative_seconds\": 0.5, \"expression\": \"neutral\", \"confidence\": 0.9}
    ]
  }" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("duplicate flag =", d["duplicate"])'

echo '===== LIVE SMOKE TEST DONE ====='
