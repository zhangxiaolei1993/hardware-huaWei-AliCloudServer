# 科研数据导出 API 使用手册

面向非专职后端的科研人员：照着本文从上到下做，就能把一场或多场测评的
**原始数据 + 分析结果**导出到本地，并核对文件完整性。

本文所有列名、字段值、zip 结构都取自本地只读库副本的真实导出产物
（2026-11-21 实测，脚本版本 1.0.0）。

---

## 0. 先知道你会拿到什么

一次导出得到一个 zip，里面按会话分目录，原始数据和服务器计算结果分开：

- **raw**：事件级原始行（每条量表/语音事件一条）、表情逐条记录；
- **derived**：服务器算出的表情汇总结果、结果版本历史、外部量表结果快照；
- **meta**：会话、会话设备、上传批次；
- 另附 `data_dictionary.md`（列字典）、`quality_report.json`（质量报告）、
  `manifest.json`（文件清单 + 每个文件的 sha256）。

---

## 1. 三步开始

### 1.1 第一步：确认服务地址和密钥

| 项目 | 值 |
| --- | --- |
| 服务根地址 | `http://8.140.240.43:8000`（生产机；本机调试用 `http://127.0.0.1:8000`） |
| 接口前缀 | `/api/v1` |
| 鉴权头 | `X-Api-Key: change-me-query-key` |
| 密钥来源 | 服务器 `/opt/psychology-server/.env` 的 `QUERY_API_KEY` |

> `change-me-query-key` 是配置默认值，仅作示例。生产环境的实际值以服务器
> `.env` 为准，拿到后替换本文所有示例中的 `change-me-query-key`。
> 密钥只放在请求头，不要拼进 URL，也不要发到聊天群。

### 1.2 第二步：健康检查（10 秒）

PowerShell：

```powershell
$Base = "http://8.140.240.43:8000/api/v1"
Invoke-WebRequest -Uri "$Base/health" -UseBasicParsing |
  Select-Object -ExpandProperty Content
```

Python：

```python
import requests
print(requests.get("http://8.140.240.43:8000/api/v1/health", timeout=10).json())
```

看到 `200` 且返回 `status: ok` 之类内容，说明服务和数据库都正常。

### 1.3 第三步：导出第一场会话

先挑一场有数据的会话（示例用生产库中的 `82e81b0eb3504508ab1841445c47ccbf`，
该场有 74 条事件和 2307 条表情）。

#### PowerShell 完整示例（可直接复制）

```powershell
# ==== 只改这三行 ====
$Base    = "http://8.140.240.43:8000/api/v1"
$Key     = "change-me-query-key"
$Session = "82e81b0eb3504508ab1841445c47ccbf"

$ZipPath = Join-Path $env:TEMP "research_export.zip"
$OutDir  = Join-Path $env:TEMP "research_export"

$Body = @{
  session_ids = @($Session)
  format      = "both"           # csv / jsonl / both
} | ConvertTo-Json -Depth 5

$Resp = Invoke-WebRequest `
  -Uri "$Base/research/exports" `
  -Method Post `
  -Headers @{ "X-Api-Key" = $Key } `
  -ContentType "application/json" `
  -Body $Body `
  -OutFile $ZipPath `
  -PassThru

Write-Host "HTTP:" $Resp.StatusCode
Write-Host "会话数:" $Resp.Headers["X-Export-Sessions"]
Write-Host "manifest sha256:" $Resp.Headers["X-Export-Manifest-Sha256"]

# 解压并核对 manifest
if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
Expand-Archive -Path $ZipPath -DestinationPath $OutDir
$Manifest = Join-Path $OutDir "manifest.json"
Write-Host "本地 manifest sha256:" (Get-FileHash $Manifest -Algorithm SHA256).Hash.ToLower()

Get-ChildItem $OutDir -Recurse -File | Select-Object FullName, Length
```

#### Python `requests` 完整示例（可直接复制）

```python
import hashlib
import io
import json
import zipfile
from pathlib import Path

import requests

BASE = "http://8.140.240.43:8000/api/v1"
KEY = "change-me-query-key"
SESSION = "82e81b0eb3504508ab1841445c47ccbf"

headers = {"X-Api-Key": KEY}

# 1) 健康检查
health = requests.get(f"{BASE}/health", timeout=10)
health.raise_for_status()
print("health:", health.json())

# 2) 导出（整包下载，超时给足）
resp = requests.post(
    f"{BASE}/research/exports",
    headers=headers,
    json={"session_ids": [SESSION], "format": "both"},
    timeout=300,
)
resp.raise_for_status()
print("HTTP:", resp.status_code)
print("会话数:", resp.headers["X-Export-Sessions"])
expected = resp.headers["X-Export-Manifest-Sha256"]
print("manifest sha256:", expected)

# 3) 保存 zip
zip_path = Path.home() / "research_export.zip"
zip_path.write_bytes(resp.content)

# 4) 解压并核对 manifest
zf = zipfile.ZipFile(io.BytesIO(resp.content))
prefix = zf.namelist()[0].split("/")[0]
manifest_bytes = zf.read(f"{prefix}/manifest.json")
actual = hashlib.sha256(manifest_bytes).hexdigest()
print("本地 manifest sha256:", actual)
assert actual == expected, "manifest 校验失败，文件可能不完整"

out_dir = Path.home() / "research_export"
zf.extractall(out_dir)
manifest = json.loads(manifest_bytes)
print("导出会话:", manifest["sessions"])
print("总行数:", manifest["total_counts"])
```

> 如果 `Invoke-WebRequest` 报证书/跳转问题，说明你连的不是本文这台的 8000 端口，
> 先确认地址；内网环境直接用 `http://` 即可。

---

## 2. `session_id` 从哪找

导出只需要一个东西：**S1**。先分清两个编号：

| 名称 | 存在哪 | 形态 | 用途 |
| --- | --- | --- | --- |
| **S1** | `sessions.session_id` | 32 位十六进制，如 `82e81b0eb3504508ab1841445c47ccbf` | 服务端主键，表情/事件/结果/快照/版本全部靠它关联；导出用这个 |
| **N1** | `sessions.external_session_id` | App/外部系统内部编号，如 `1790`，可能为空 | 跨系统对账；**不能**直接当导出参数 |

两者不是一回事：S1 由服务器生成，N1 由 App 侧生成；一场会话两列都有值，
但值完全不同。

### 途径 ①：先全导，再从 zip 目录名取 S1

不指定会话、直接导出全部：

```json
{"export_all": true, "data_types": ["emotion"]}
```

zip 解压后 `sessions/` 下每个子目录名就是 S1。适合第一次使用、或不知道有哪些会话时。

### 途径 ②：在生产库只读查询 `sessions`

在服务器上执行（**只读打开，不会写库**）：

```bash
sqlite3 "file:/opt/psychology-server/psychology.db?mode=ro" "
SELECT session_id, external_session_id, source_system, status,
       started_at, ended_at,
       (SELECT COUNT(*) FROM emotion_records r WHERE r.session_id = s.session_id) AS emotion_rows,
       (SELECT COUNT(*) FROM timeline_events e WHERE e.session_id = s.session_id) AS event_rows
FROM sessions s
ORDER BY COALESCE(started_at, created_at) DESC;"
```

`mode=ro` + `PRAGMA query_only=ON` 保证只读。字段含义：

| 字段 | 含义 |
| --- | --- |
| `session_id` | S1，导出参数 |
| `external_session_id` | N1，App 内部编号 |
| `source_system` | `standalone`（设备独立）/ `xin_tan_tan`（App 接入）/ `custom` |
| `status` | `completed` / `interrupted` / `aborted` / `running` / `created` |
| `started_at` / `ended_at` | 会话起止，**UTC**（北京时间需 +8 小时） |
| `emotion_rows` / `event_rows` | 该场表情条数 / 事件条数，为 0 说明没有对应原始数据 |

也可以只查最近几场：

```sql
SELECT session_id, external_session_id, source_system, status, started_at
FROM sessions
ORDER BY COALESCE(started_at, created_at) DESC
LIMIT 20;
```

### 途径 ③：从 App 的 N1 反查 S1

```sql
SELECT session_id, external_session_id, source_system, status, started_at
FROM sessions
WHERE external_session_id = '1790'
ORDER BY created_at DESC;
```

注意：`external_session_id` **没有唯一约束**，不同 `source_system` 可能复用同一个
N1。跨来源时把来源也带上：

```sql
SELECT session_id, external_session_id, source_system, status
FROM sessions
WHERE external_session_id = '1790' AND source_system = 'xin_tan_tan';
```

拿到结果第一列的 32 位值，就是导出要用的 S1。

---

## 3. 接口与参数

### 3.1 创建导出（单会话 / 多会话 / 全量）

```text
POST /api/v1/research/exports
X-Api-Key: <密钥>
Content-Type: application/json
```

请求体参数：

| 参数 | 必填 | 取值 / 范围 | 示例 | 写错会怎样 |
| --- | --- | --- | --- | --- |
| `session_ids` | 与 `export_all` 二选一 | 字符串数组，最多 200 个，每个为 32 位 S1 | `["82e8...ccbf"]` | 与 `export_all` 同时给、或两个都不给 → 422 |
| `export_all` | 与 `session_ids` 二选一 | `true` / `false` | `true` | 同上 |
| `from_utc` | 否 | ISO8601，允许 `Z` 或 `+08:00`；按会话 `started_at`（空则 `created_at`）比较 | `"2026-11-01T00:00:00Z"` | 格式非法或晚于 `to_utc` → 422 |
| `to_utc` | 否 | 同上 | `"2026-11-30T23:59:59Z"` | 同上 |
| `from_seconds` | 否 | 浮点秒，可负；只作用于事件/表情行 | `30` | 大于 `to_seconds` → 422 |
| `to_seconds` | 否 | 浮点秒 | `120` | 同上 |
| `data_types` | 否 | `timeline` / `emotion` / `results` / `snapshots` / `batches` 的数组；默认全部 | `["timeline","emotion"]` | 非法值 → 422 |
| `format` | 否 | `csv` / `jsonl` / `both`，默认 `both` | `"csv"` | 其他值 → 422 |

补充规则：

- `sessions` 与 `session_devices` **始终导出**，不受 `data_types` 控制；
- `data_types: []` 只导出会话与设备两张表（一般不要这么用）；
- 未提供的字段用默认值；多写未知字段 → 422（接口拒绝拼写错误）；
- 时间筛选按**会话**过滤，行级筛选按 `relative_seconds` 过滤。

响应：`200`，`Content-Type: application/zip`。响应头：

| 响应头 | 含义 |
| --- | --- |
| `X-Export-Manifest-Sha256` | zip 内根 `manifest.json` 的 sha256 |
| `X-Export-Sessions` | 本次导出的会话数 |
| `X-Export-Total-Counts` | 各表总行数（JSON 字符串） |
| `X-Export-Missing-Tables` | 旧库缺少的表，正常为 `none` |

### 3.2 导出单场会话

```text
GET /api/v1/research/sessions/{session_id}/export?format=both
X-Api-Key: <密钥>
```

| 参数 | 必填 | 取值 | 示例 | 写错会怎样 |
| --- | --- | --- | --- | --- |
| `session_id`（路径） | 是 | 32 位 S1 | `82e81b0eb3504508ab1841445c47ccbf` | 不存在 → 404 |
| `format` | 否 | `csv` / `jsonl` / `both` | `csv` | 非法值 → 422 |
| `from_seconds` / `to_seconds` | 否 | 浮点秒 | `30` / `120` | 下界大于上界 → 422 |
| `data_types` | 否 | 逗号分隔的枚举 | `timeline,emotion` | 非法值 → 422 |

```bash
curl -sS "http://8.140.240.43:8000/api/v1/research/sessions/82e81b0eb3504508ab1841445c47ccbf/export?format=csv&data_types=timeline,emotion" \
  -H "X-Api-Key: change-me-query-key" \
  -o research_single.zip
```

### 3.3 常用筛选组合

```jsonc
// 只要原始数据（事件 + 表情），用于自己做统计
{"session_ids": ["<S1>"], "data_types": ["timeline", "emotion"], "format": "csv"}

// 按时间范围批量导出全部会话的结果与快照
{"export_all": true, "from_utc": "2026-11-01T00:00:00Z",
 "to_utc": "2026-11-30T23:59:59Z", "data_types": ["results", "snapshots"]}

// 只看某场 30–120 秒的原始行
{"session_ids": ["<S1>"], "from_seconds": 30, "to_seconds": 120,
 "data_types": ["timeline", "emotion"]}
```

---

## 4. 返回 zip 结构

```text
research_export_<UTC时间戳>/
├── data_dictionary.md                 # 列字典（与本文一致，随产物生成）
├── quality_report.json                # 全局质量报告（各会话 + totals）
├── sessions_index.csv                 # 每场各表行数一览
├── manifest.json                      # 根清单：参数、库指纹、每个文件 sha256
└── sessions/
    └── <S1>/
        ├── meta/
        │   ├── sessions.csv|jsonl
        │   ├── session_devices.csv|jsonl
        │   └── upload_batches.csv|jsonl      # data_types 含 batches 时才有
        ├── raw/
        │   ├── timeline_events.csv|jsonl     # data_types 含 timeline 时才有
        │   └── emotion_records.csv|jsonl     # data_types 含 emotion 时才有
        ├── derived/
        │   ├── emotion_results.csv|jsonl             # results
        │   ├── emotion_result_versions.csv|jsonl     # results
        │   └── external_result_snapshots.csv|jsonl   # snapshots
        ├── quality_report.json          # 该场质量报告
        └── manifest.json                # 该场清单（含 counts 与各文件 sha256）
```

文件用途：

| 文件 | 用途 |
| --- | --- |
| `manifest.json`（根） | 导出参数、源库大小与 sha256、总会话、总行数、每个文件 sha256 |
| `manifest.json`（会话） | 该场各表 `counts`、该场每个文件 sha256 |
| `quality_report.json` | 各表填充率、质量分布、flags 统计、时间范围、异常计数、被裁剪 payload 键 |
| `sessions_index.csv` | 快速看每场有多少事件/表情/结果/快照/批次 |
| `data_dictionary.md` | 与本文第 5 节一致的列级说明，适合随数据一起归档 |

CSV 为 UTF-8（无 BOM），嵌套 JSON 字段以紧凑 JSON 字符串保存；JSONL 中保持对象。

---

## 5. 关键字段含义（以真实产物为准）

### 5.1 三个通用概念

**`relative_seconds` 相对谁**：相对该会话的服务器锚点
`sessions.anchor_utc_at`，不是相对第一题、也不是相对录音开始。

**锚点怎么换算成 UTC**：

```text
event_timestamp = anchor_utc_at + relative_seconds
```

例：`anchor_utc_at = 2026-11-19T10:24:45.646Z`，某事件
`relative_seconds = -24.318` → `event_timestamp = 2026-11-19T10:24:21.328Z`。

- 负值表示发生在锚点之前（App 先本地记录、后上传，属于正常现象）；
- 误差上限看 `anchor_uncertainty_ms`（常见 5000ms）；
- `event_timestamp_estimated` **恒为 `true`**：它是导出时算的派生值，不是设备上报时间；
- 派生值只出现在导出物中，不回写数据库、不改变原始 `quality`。

**`quality` 四档**：

| 值 | 含义 |
| --- | --- |
| `device` | 设备时间戳可信（完成对时）。P0 阶段不产出 |
| `estimated` | 有设备时间证据（`device_timestamp` 或 `monotonic_ms`），但时钟未校验，只能估算 |
| `missing` | 既无设备时间戳也无单调时钟，无法定位到真实时刻 |
| `legacy` | v1 固件或未提供 `stream_id`，按旧协议接收，时间仅作参考 |

`quality_info` 是 JSON 对象，常见键与 flags：

| 键 / flag | 含义 |
| --- | --- |
| `flags: ["batch_upload"]` | 该条随批量上传写入 |
| `flags: ["clock_unverified"]` | 设备与服务器时钟未校验 |
| `flags: ["stream_defaulted"]` | 客户端没给 `stream_id`，服务器补成 `facial_expression` |
| `flags: ["pre_anchor"]` | `-5 ≤ relative_seconds < 0`，锚点前的数据 |
| `flags: ["out_of_range"]` | 超出会话窗口上限（结束时间 + 600 秒） |
| `flags: ["clock_skew"]` | 设备时钟偏移超过阈值 |
| `stream_source` | `client`（客户端提供）或 `server_default`（服务器补全） |
| `clock_sync_status` | 当前恒为 `unverified` |

**`stream_id`**：同一会话内区分数据流。已知值：

| 值 | 含义 |
| --- | --- |
| `facial_expression` | 表情流（默认值） |
| `app_timeline` | App 量表/流程事件流 |
| `speech` / 其他设备流 | 预留的语音、生理等多模态流 |
| 空 | 纯量表事件未带流标识（事件表允许为空） |

### 5.2 `meta/sessions`（每场 1 行）

| 列 | 含义 |
| --- | --- |
| `session_id` | S1，32 位十六进制 |
| `subject_ref` | 受试者脱敏标识（HMAC 截断 16 位），跨会话关联受试者用 |
| `deprecated_identity_used` | 是否因缺少外部用户 ID 而回退旧身份计算 |
| `session_type` | `emotion`（当前）/ 未来 `physiological` 等 |
| `source_system` | `standalone` / `xin_tan_tan` / `custom` |
| `external_session_id` | N1，App 内部编号，可能为空 |
| `client_request_id` | 创建会话的幂等键 |
| `device_id` | 主设备 ID |
| `anchor_utc_at` | 时间锚点（UTC） |
| `anchor_monotonic_ms` | 锚点对应的设备单调时钟毫秒，常为空 |
| `anchor_source` | `server` / `device` |
| `anchor_uncertainty_ms` | 锚点误差上限（毫秒） |
| `late_upload_deadline` | 允许补传的截止时间（UTC） |
| `ended_reason` | 结束原因（正常完成/中断原因等） |
| `status` | `created` / `running` / `completed` / `interrupted` / `aborted` |
| `started_at` / `ended_at` | 会话起止（UTC） |
| `created_at` / `updated_at` | 记录创建/更新时间（UTC） |

### 5.3 `meta/session_devices`（会话—设备绑定）

| 列 | 含义 |
| --- | --- |
| `session_id` | S1 |
| `device_id` | 设备 ID |
| `role` | `primary`（主设备）/ `auxiliary`（辅设备） |
| `status` | `bound` / `unbound` |
| `bound_at` / `unbound_at` | 绑定/解绑时间（UTC） |
| `created_at` / `updated_at` | 记录时间（UTC） |

### 5.4 `meta/upload_batches`（上传批次）

| 列 | 含义 |
| --- | --- |
| `upload_id` | 批次 ID |
| `session_id` | S1 |
| `device_id` | 设备 ID |
| `app_client_id` | App 安装实例 ID，可能为空 |
| `client_request_id` | 批次幂等键 |
| `batch_type` | `event` / `emotion` / `raw` / `result` |
| `status` | `open` / `received` / `verified` / `failed` |
| `item_count` | 批内条数 |
| `total_bytes` | 批内总字节数 |
| `sha256` | 批次内容摘要（可与表情记录 `source_sha256` 对照） |
| `error_code` | 失败错误码，成功为空 |
| `received_at` | 服务端接收时间（UTC） |
| `created_at` / `updated_at` | 记录时间（UTC） |

### 5.5 `raw/timeline_events`（事件级原始行，每条事件 1 行）

| 列 | 含义 |
| --- | --- |
| `event_id` | 客户端事件 ID（32 位十六进制） |
| `session_id` | S1 |
| `device_id` | 设备 ID；纯量表事件为空 |
| `stream_id` | 数据流，纯量表事件可能为空 |
| `event_type` | 13 类白名单之一，见下表 |
| `source` | `device` / `app` / `server` |
| `occurred_at` | 客户端上报的发生时间（UTC），可能受设备时钟影响 |
| `relative_seconds` | 相对锚点秒数，可为负 |
| `device_timestamp` | 设备本地时间字符串，原样保留 |
| `monotonic_ms` | 设备单调时钟毫秒 |
| `clock_offset_ms` | 设备时钟偏移毫秒 |
| `time_uncertainty_ms` | 时间不确定度毫秒 |
| `server_received_at` | 服务端接收时间（UTC） |
| `task_id` | 任务 ID |
| `questionnaire_id` | 量表 ID |
| `questionnaire_version` | 量表版本 |
| `question_id` | 题目 ID |
| `question_index` | 题目序号 |
| `duration_ms` | 事件持续毫秒（如语音/作答） |
| `payload` | 受控载荷，只保留该事件类型白名单键，见下 |
| `schema_version` | 事件 Schema 版本 |
| `protocol_version` | 上传协议版本 |
| `quality` | 四档质量，见 5.1 |
| `quality_info` | 质量明细（JSON，含 flags） |
| `client_request_id` | 上报批次幂等键 |
| `created_at` | 服务端入库时间（UTC） |
| `event_timestamp` | **派生**：锚点 + `relative_seconds`，UTC |
| `event_timestamp_estimated` | 恒为 `true` |
| `derived_time_source` | 恒为 `server_anchor` |
| `anchor_uncertainty_ms` | 该场锚点误差（毫秒） |

`event_type` 取值：`session_started`、`session_ended`、`device_connected`、
`device_disconnected`、`question_shown`、`response_started`、`response_submitted`、
`response_modified`、`question_skipped`、`question_completed`、`speech_started`、
`speech_ended`、`emotion_recorded`。

`payload` 允许键（按事件类型裁剪，答案类字段一律不出现）：
`question_type`、`render_ms`、`input_method`、`response_changed`、
`modification_count`、`skip_reason_code`、`total_time_ms`、`hesitation_count`、
`audio_stream_id`、`duration_ms`、`bytes`、`ended_reason`、`client_version`、
`connection_id`、`rssi`、`record_index`、`window_ms`。

### 5.6 `raw/emotion_records`（表情逐条记录）

| 列 | 含义 |
| --- | --- |
| `session_id` | S1 |
| `device_id` | 采集设备 |
| `stream_id` | 表情流，通常 `facial_expression` |
| `relative_seconds` | 相对锚点秒数 |
| `expression` | 8 类之一：`neutral`/`happiness`/`surprise`/`sadness`/`anger`/`disgust`/`fear`/`contempt` |
| `confidence` | 置信度 0–1 |
| `created_at` | 服务端入库时间（UTC） |
| `device_timestamp` | 设备本地时间（P0 客户端未填） |
| `monotonic_ms` | 设备单调时钟（P0 客户端未填） |
| `clock_offset_ms` | 设备时钟偏移毫秒 |
| `frame_index` | 帧序号（P0 客户端未填） |
| `quality` | 四档质量，见 5.1 |
| `quality_info` | 质量明细（JSON） |
| `protocol_version` | 上传协议版本 |
| `event_timestamp` | 派生：锚点 + `relative_seconds` |
| `event_timestamp_estimated` | 恒为 `true` |
| `derived_time_source` | 恒为 `server_anchor` |
| `anchor_uncertainty_ms` | 该场锚点误差（毫秒） |

### 5.7 `derived/emotion_results`（当前结果，每场 1 行）

| 列 | 含义 |
| --- | --- |
| `session_id` | S1 |
| `client_request_id` | 最近一次计算批次幂等键 |
| `valid_expression_seconds` | 有效表情累计秒数 |
| `dominant_expression` | 主导表情（时长最长；并列时取记录更多者） |
| `average_confidence` | 平均置信度 |
| `face_coverage_percent` | 人脸覆盖率（服务器按帧重算） |
| `result_json` | 完整结果 JSON，见下 |
| `created_at` / `updated_at` | 首次生成 / 最近重算时间（UTC） |

`result_json` 结构：

```json
{
  "expressions": [
    {"expression": "neutral",   "seconds": 128.51, "percentage": 97.14},
    {"expression": "happiness", "seconds": 3.79,   "percentage": 2.86}
  ],
  "quality": {
    "total_records": 2307,
    "timeline_span_seconds": 132.301,
    "valid_expression_seconds": 132.301,
    "video_frames": 2342,
    "display_fps": 17.67,
    "face_detected_frames": 2307,
    "inference_runs": 2307,
    "elapsed_seconds": 132.56
  },
  "session_start": "2026-11-19T10:24:46+00:00",
  "session_end": "2026-11-19T10:26:58+00:00"
}
```

- `expressions[].seconds`：该表情累计持续时间。算法按相邻两条的相对时间差归给前一条，
  最后一条只参与计数、不计时长，因此各表情秒数之和等于 `valid_expression_seconds`；
- `expressions[].percentage`：占有效时长的百分比，保留 2 位小数；
- `quality.video_frames` / `display_fps` / `face_detected_frames` / `inference_runs` /
  `elapsed_seconds` 来自上传时的会话元数据，用于交叉核对采集质量。

### 5.8 `derived/emotion_result_versions`（结果版本历史，只追加）

| 列 | 含义 |
| --- | --- |
| `session_id` | S1 |
| `result_id` | 对应 `emotion_results` 的主键 |
| `version` | 会话内版本号，从 1 单调递增 |
| `algorithm_version` | 算法口径，当前 `emo_v1` |
| `status` | `generated`（当前）/ `superseded`（已被新版本取代） |
| `trigger_source` | `first_import`（首次导入）/ `recompute`（追加上传后重算）/ `manual_recompute`（手动重算）/ `backfill`（历史回填） |
| `source_record_count` | 生成该版本时参与重算的表情记录数 |
| `source_sha256` | 生成时源记录的规范化 sha256，用于回溯当时的数据 |
| `result_json` | 该版本完整结果快照（结构同 5.7） |
| `generated_at` | 版本生成时间（UTC） |
| `created_at` | 入库时间（UTC） |

用法：需要"当时算出来是多少"时查历史版本；`source_sha256` + `source_record_count`
可确认该版本对应哪批原始记录（不必存原始数据副本）。

### 5.9 `derived/external_result_snapshots`（外部量表结果快照）

| 列 | 含义 |
| --- | --- |
| `snapshot_id` | 快照 ID |
| `session_id` | S1 |
| `source_system` | 来源系统 |
| `source_result_id` | 外部系统原始结果 ID |
| `questionnaire_code` | 量表编码（如 `22`） |
| `questionnaire_version` | 量表版本 |
| `scale_algorithm_version` | 量表算法版本 |
| `result_version` | 同一量表结果的版本号，从 1 递增 |
| `supersedes_snapshot_id` | 被本快照取代的上一快照 ID；首版为空 |
| `status` | 结果状态（如 `complete`） |
| `total_score` | 总分（聚合值） |
| `scoring_mode` | 计分模式（如 `ai_text`） |
| `state_update_status` | 状态更新结果 |
| `scale_name` | 量表名称 |
| `scale_question_count` | 题目数 |
| `dimensions` | 维度分 JSON：`{"schema":"raw_map_v1","items":[{"code","name","score"}...]}` |
| `radar_norm` | 雷达图归一化 JSON：`{"schema":"radar_norm_v1","series":[...]}` |
| `risk_level` | 风险等级 |
| `level_code` | 等级编码 |
| `recommendation_code` | 建议编码 |
| `ai_profile_score` | AI 画像分 |
| `summary_code` | 摘要编码 |
| `trust_level` | 信任级别：一期恒为 `client_relayed`；枚举另有 `server_signed` / `server_verified` |
| `signature` | 来源签名 |
| `signature_algorithm` | 签名算法 |
| `source_payload_sha256` | 来源载荷摘要 |
| `idempotency_key` | 幂等键，格式 `res:{S1}:{questionnaire_code}:v{result_version}` |
| `received_at` | 接收时间（UTC） |
| `created_at` | 入库时间（UTC） |

同量表多版本时，取 `result_version` 最大的一行即最新；需要历史时按
`supersedes_snapshot_id` 向前追溯。

### 5.10 `sessions_index.csv`

| 列 | 含义 |
| --- | --- |
| `session_id` | S1 |
| `timeline_events` | 该场事件行数 |
| `emotion_records` | 该场表情行数 |
| `emotion_results` | 该场当前结果行数（通常 1） |
| `external_result_snapshots` | 该场量表快照数 |
| `upload_batches` | 该场上传批次数 |

---

## 6. 校验 manifest（确认下载完整）

`manifest.json` 的 `files[]` 给出每个文件的相对路径和 sha256（各表行数在
`counts` / `total_counts` / `sessions_index.csv` 中）。收到 zip 后任选一种方式核对。

PowerShell：

```powershell
$Dir = "$env:TEMP\research_export"
$Manifest = Get-Content "$Dir\manifest.json" -Raw | ConvertFrom-Json
$Bad = @()
foreach ($f in $Manifest.files) {
  $p = Join-Path $Dir $f.path
  $h = (Get-FileHash $p -Algorithm SHA256).Hash.ToLower()
  if ($h -ne $f.sha256) { $Bad += $f.path }
}
if ($Bad.Count -eq 0) { "全部文件校验通过" } else { "校验失败: $($Bad -join ', ')" }
```

Python：

```python
import hashlib, json
from pathlib import Path

root = Path.home() / "research_export"
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
bad = []
for item in manifest["files"]:
    p = root / item["path"].replace("\\", "/")
    if hashlib.sha256(p.read_bytes()).hexdigest() != item["sha256"]:
        bad.append(item["path"])
print("全部通过" if not bad else f"失败: {bad}")
```

再核对根 manifest 与响应头：两者应完全一致（本文 1.3 节示例已包含）。

---

## 7. 常见错误码与自查

| 状态码 | 常见原因 | 自查 |
| --- | --- | --- |
| 401 | 没带 `X-Api-Key`、头名写错、密钥不对 | 头名必须是 `X-Api-Key`；值与服务器 `.env` 的 `QUERY_API_KEY` 一致 |
| 404 | S1 不存在；或时间/条件筛完没有会话 | 先用第 2 节 SQL 核对 S1；放宽 `from_utc` / `to_utc` |
| 422 | 参数冲突或格式错：两个都填/都不填、时间下界大于上界、`data_types` 拼错、未知字段 | 对照第 3 节参数表；时间用 `2026-11-01T00:00:00Z` 这种格式 |
| 500 | 服务端数据库文件不可读、导出模块异常 | 看 `journalctl -u psychology-server`；联系维护者 |
| 501 | 数据库不是 SQLite | 当前仅支持 SQLite 文件 |

### 7.1 "导出来是空的"

按顺序排查：

1. 解压后有没有 `sessions/<S1>/` 目录？没有说明 S1 不对或时间筛选把会话排除了；
2. 看 `sessions_index.csv` 对应行：`timeline_events` / `emotion_records` 是否为 0；
3. 如果用了 `from_seconds` / `to_seconds`，窗口内可能确实没有行；
4. 如果 `data_types` 只填了 `results`，就不会有 `raw/`，这是正常的；
5. 仍为空时按第 2 节 SQL 直接查该场条数，确认库里到底有没有数据。

### 7.2 "401 但密钥看着没错"

- PowerShell 的 `-Headers` 用 `@{ "X-Api-Key" = $Key }`，不要写成 `X-Api_Key`；
- 复制密钥时不要带引号或空格；
- 确认连的是同一台服务器（别把测试密钥用到了生产）。

### 7.3 "某一列全是空"

先看该场 `quality_report.json` 的 `tables.<表>.fill_rates`，再看列本身的设计：

| 列 | 为什么常为空 |
| --- | --- |
| `emotion_records.device_timestamp` / `monotonic_ms` / `frame_index` | P0 客户端未上报，属预期；对应 `quality=legacy` |
| `sessions.anchor_monotonic_ms` | 锚点由服务器生成，没有设备单调时钟 |
| `upload_batches.app_client_id` / `error_code` | 旧客户端未填 / 成功批次无错误码 |
| `external_result_snapshots.supersedes_snapshot_id` | 第一版结果没有上一版 |
| `session_devices.unbound_at` | 仍处于绑定状态 |
| `timeline_events.stream_id` / `device_id` | 纯量表事件不来自设备 |

若某列**本应有值却全空**，把该场 `quality_report.json` 与 `manifest.json` 一起发给维护者，
并说明导出参数（会话号、时间范围、`data_types`、`format`）。

### 7.4 关于时间对不上

- 库内与导出都是 UTC；北京时间 = UTC + 8 小时；
- 优先用派生的 `event_timestamp` 排时间轴，不要直接用 `occurred_at`（受设备时钟影响）；
- 负 `relative_seconds` 是正常的"锚点前"数据，不是脏数据。

---

## 8. 与命令行脚本、P0 导出接口的关系

- 本文接口与 `scripts/research_export.py` 共用同一套列白名单、隐私裁剪、
  质量报告与 manifest 逻辑；参数一一对应。
- 命令行脚本支持 `--include-external-ids` 导出 `user_id` / `external_user_id`，
  **HTTP 接口固定不导出**这两列，避免经网络扩大身份数据暴露面；确有需要请在服务器本地用脚本。
- P0 的 `/api/v1/sessions/{id}/exports` 只导出表情结果序列（csv/json），
  不含事件级原始行；科研分析请使用本文的 `/api/v1/research/...` 接口。
