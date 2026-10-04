# psychology-server

多设备心理测评云平台后端（第一阶段：设备管理 + 会话管理 + 表情识别）。

架构：**通用 Device 管理 + 通用 Session 管理 + 独立业务 API**

```text
Device → Session → Emotion API → Emotion Service → Emotion Result
```

未来新增业务（心率 / EEG / 眼动）时，新增各自的 `api / models / schemas / services`，
复用 Device 与 Session 层，互不影响。

## 技术栈

- Python 3.10+ / FastAPI / Uvicorn
- SQLAlchemy 2.0（SQLite 起步，可切换 MySQL，只改 `.env` 的 `DATABASE_URL`）
- Pydantic v2 / pydantic-settings

## 目录结构

```text
app/
├── main.py            # 应用入口
├── api/               # 路由层：health / devices / sessions / emotion
├── core/              # config / security / logging
├── db/                # database.py（engine、session、Base）
├── models/            # ORM：device / session / emotion_record / emotion_result
├── schemas/           # Pydantic 出入参
└── services/          # 业务逻辑（算法只在这里，API 层不写业务）
tests/                 # pytest（12 项流程测试）
scripts/               # 部署/运维脚本
```

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # 修改 SECRET_KEY 与 DATABASE_URL
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

运行测试：

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## API 总表

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health` | 健康检查 |
| POST | `/api/v1/devices/register` | 注册设备（返回 device_token） |
| GET | `/api/v1/devices/{device_id}` | 查询设备（惰性判断 online/offline） |
| POST | `/api/v1/devices/{device_id}/heartbeat` | 心跳（约 30s 一次，>90s 判 offline） |
| POST | `/api/v1/sessions` | 创建会话 |
| GET | `/api/v1/sessions/{session_id}` | 查询会话 |
| POST | `/api/v1/sessions/{session_id}/status` | 状态流转 running / completed |
| POST | `/api/v1/emotion/sessions/{session_id}/data` | 批量上传 timeline（测评结束后一次性） |
| GET | `/api/v1/emotion/sessions/{session_id}/result` | 获取服务器计算的结果（Flutter 用） |

### 认证

设备操作需携带请求头：

```text
X-Device-Id: atlas_001
X-Device-Token: <register 返回的 token>
```

## curl 示例（完整流程）

```bash
BASE=http://127.0.0.1:8000/api/v1

# 1. 注册设备
curl -s -X POST $BASE/devices/register -H 'Content-Type: application/json' -d '{
  "device_id": "atlas_001",
  "device_name": "心理测评设备01",
  "device_type": "edge_ai_device",
  "manufacturer": "Huawei",
  "model": "Atlas 200I DK A2",
  "firmware_version": "1.0.0",
  "capabilities": ["emotion"]
}'
# 返回 device_token，后续作为 X-Device-Token

# 2. 心跳
curl -s -X POST $BASE/devices/atlas_001/heartbeat \
  -H 'X-Device-Id: atlas_001' -H 'X-Device-Token: <TOKEN>' -H 'Content-Type: application/json' -d '{}'

# 3. 创建会话
curl -s -X POST $BASE/sessions \
  -H 'X-Device-Id: atlas_001' -H 'X-Device-Token: <TOKEN>' -H 'Content-Type: application/json' \
  -d '{"device_id": "atlas_001", "session_type": "emotion"}'
# 返回 session_id

# 4. 上传 timeline（测评结束后一次性批量上传）
curl -s -X POST $BASE/emotion/sessions/<SESSION_ID>/data \
  -H 'X-Device-Id: atlas_001' -H 'X-Device-Token: <TOKEN>' -H 'Content-Type: application/json' -d '{
  "client_request_id": "req-20261003-0001",
  "session_meta": {
    "elapsed_seconds": 60.0, "video_frames": 1800, "display_fps": 30,
    "face_detected_frames": 1500, "inference_runs": 120
  },
  "timeline": [
    {"relative_seconds": 0.5, "expression": "neutral", "confidence": 0.82},
    {"relative_seconds": 1.0, "expression": "neutral", "confidence": 0.85},
    {"relative_seconds": 1.5, "expression": "happiness", "confidence": 0.78},
    {"relative_seconds": 2.0, "expression": "happiness", "confidence": 0.80}
  ]
}'

# 5. 查询结果（Flutter 使用）
curl -s $BASE/emotion/sessions/<SESSION_ID>/result
```

## 业务规则

1. **结果由服务器计算**：开发板只上传原始 timeline；时长、百分比、主导表情、
   平均置信度、人脸覆盖率全部由 `services/emotion_service.py` 重算。
2. **时长算法**：区间 `[t_i, t_{i+1})` 归属于第 i 条记录的表情。
   例：0.5 neutral / 1.0 neutral / 1.5 happiness / 2.0 happiness → neutral 1.0s、happiness 0.5s。
3. **校验**：expression 仅允许 8 类（neutral/happiness/surprise/sadness/anger/disgust/fear/contempt）；
   confidence ∈ [0,1]；timeline 非空；session 必须存在。
4. **防重复上传**：`client_request_id` + `session_id` 幂等；网络重试不会重复插入
   `emotion_records`；已完成会话默认拒绝再次上传（409）。
5. **心跳**：heartbeat 正常 → online；超过 `DEVICE_OFFLINE_AFTER_SECONDS`（默认 90s）
   无心跳 → offline（查询时惰性判断）。

## 数据表

- `devices`：通用设备表（含 capabilities、device_token）
- `sessions`：通用会话表（session_type 当前仅 emotion）
- `emotion_records`：原始 timeline（必须保留原始时间序列）
- `emotion_results`：服务器计算结果（result_json 存详细统计）

## 第二阶段备忘（未开发）

心率 / EEG / 眼动 / WebSocket / MQTT / Redis / Nginx / HTTPS / Docker 生产部署。
