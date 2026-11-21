# 科研数据导出 API 快速上手

服务地址 `http://8.140.240.43:8000`，所有请求带密钥头
`X-Api-Key: change-me-query-key`（实际以服务器 `.env` 的 `QUERY_API_KEY` 为准）。

> Windows 用 `curl.exe`；密钥只放请求头。浏览器直接下载可在 URL 后加
> `?api_key=<密钥>`，但查询串会进访问日志和浏览器历史，仅限内网/本人使用，
> 不要用在其他业务接口上。

## 一、四条常用命令

```bash
# 1) 列出所有会话，拿到 S1（session_id）
curl.exe -H "X-Api-Key: change-me-query-key" "http://8.140.240.43:8000/api/v1/research/sessions"

# 2) 导出单个会话（默认全部数据类型、CSV+JSONL）
curl.exe -H "X-Api-Key: change-me-query-key" -o session.zip "http://8.140.240.43:8000/api/v1/research/sessions/82e81b0eb3504508ab1841445c47ccbf/export"

# 3) 一键全量导出（无参数）
curl.exe -H "X-Api-Key: change-me-query-key" -o all.zip "http://8.140.240.43:8000/api/v1/research/export/all"

# 4) 按北京时间段导出（可只给 from 或只给 to；都不给等同全量）
curl.exe -H "X-Api-Key: change-me-query-key" -o range.zip "http://8.140.240.43:8000/api/v1/research/export?from=2026-11-19&to=2026-11-19"
```

浏览器直接点：

```text
http://8.140.240.43:8000/api/v1/research/sessions?api_key=change-me-query-key
http://8.140.240.43:8000/api/v1/research/export/all?api_key=change-me-query-key
```

四条命令都返回 zip（会话清单除外），解压后结构见附录 A。

## 二、参数表（精简）

| 接口 | 参数 | 必填 | 说明 | 写错会怎样 |
| --- | --- | --- | --- | --- |
| `GET /research/sessions` | 无 | - | 全部会话，按开始时间倒序 | - |
| `GET /research/sessions/{session_id}/export` | `session_id` | 是 | 32 位十六进制 S1 | 不存在 → 404 |
| 同上 | `format` | 否 | `csv`/`jsonl`/`both`，默认 `both` | 非法 → 422 |
| 同上 | `data_types` | 否 | 逗号分隔，默认全部 | 非法 → 422 |
| 同上 | `from_seconds`/`to_seconds` | 否 | 按相对秒筛选行 | 下界大于上界 → 422 |
| `GET /research/export/all` | 无 | - | 全量导出 | - |
| `GET /research/export` | `from`/`to` | 否 | **北京时间**；日期 `2026-11-19` 或 ISO8601；`to` 为日期时含当天 23:59:59 | 格式错或 from 晚于 to → 422 |
| `POST /research/exports` | 见附录 D | - | 需要按 UTC/类型/格式精细筛选时用 | 参数冲突 → 422 |

`api_key` 查询参数仅上述四个 GET 接口支持；`POST` 仍只认请求头。

## 三、会话清单返回什么

```json
{
  "total": 78,
  "sessions": [
    {
      "session_id": "82e81b0eb3504508ab1841445c47ccbf",
      "external_session_id": "1790",
      "source_system": "xin_tan_tan",
      "status": "completed",
      "started_at_bj": "2026-11-19T18:24:46+08:00",
      "ended_at_bj": "2026-11-19T18:26:56+08:00",
      "emotion_records": 2307,
      "timeline_events": 74,
      "has_result": true
    }
  ]
}
```

把任一 `session_id` 填进单会话导出即可。S1 是 32 位十六进制；N1
（`external_session_id`）是 App 内部编号，两者不是一回事。

## 四、科研分析真正会用到的字段

| 字段 | 含义 |
| --- | --- |
| `session_id` | S1，关联所有表的主键 |
| `subject_ref` | 受试者脱敏 ID，跨会话关联同一人 |
| `status` | `completed` / `interrupted` / `aborted` 等 |
| `anchor_utc_at` | 会话时间锚点（UTC） |
| `relative_seconds` | 相对 `anchor_utc_at` 的秒数，可为负 |
| `event_timestamp` | 派生 UTC 时间 = `anchor_utc_at + relative_seconds` |
| `quality` | `device` / `estimated` / `missing` / `legacy` |
| `expression` / `confidence` | 8 类表情之一及置信度（0–1） |
| `valid_expression_seconds` | 有效表情累计时长 |
| `result_json.expressions` | 各表情 `seconds` / `percentage` |
| `result_json.quality` | 记录数、时间跨度、帧数、FPS 等质量指标 |
| `result_version` / `supersedes_snapshot_id` | 量表结果版本及上一版 |
| `trust_level` | 快照信任级别，一期为 `client_relayed` |
| `version` / `source_sha256` | 结果版本号与该版本对应的原始数据指纹 |

时间口径：库内与 `*_utc` 均为 UTC；会话清单的 `*_bj` 为北京时间（+08:00）。
`relative_seconds` 相对锚点，不是相对第一题；负值表示锚点之前，属正常。

---

# 附录

## A. zip 结构

```text
research_export_<UTC时间戳>/
├── data_dictionary.md
├── quality_report.json
├── sessions_index.csv
├── manifest.json
└── sessions/<S1>/
    ├── meta/{sessions, session_devices, upload_batches}.csv|jsonl
    ├── raw/{timeline_events, emotion_records}.csv|jsonl
    ├── derived/{emotion_results, emotion_result_versions, external_result_snapshots}.csv|jsonl
    ├── quality_report.json
    └── manifest.json
```

| 文件 | 用途 |
| --- | --- |
| `manifest.json`（根） | 参数、库指纹、会话清单、总行数、每个文件 sha256 |
| `manifest.json`（会话） | 该场各表行数与文件 sha256 |
| `quality_report.json` | 填充率、质量分布、flags、异常计数、被裁剪 payload 键 |
| `sessions_index.csv` | 每场事件/表情/结果/快照/批次行数一览 |
| `data_dictionary.md` | 列字典，随数据归档 |

CSV 为 UTF-8（无 BOM），嵌套 JSON 存为紧凑字符串；JSONL 保持对象。

## B. 九个文件的完整列说明

### B.1 `meta/sessions`（每场 1 行）

| 列 | 含义 |
| --- | --- |
| `session_id` | S1，32 位十六进制 |
| `subject_ref` | 受试者脱敏标识（HMAC 截断 16 位） |
| `deprecated_identity_used` | 是否回退旧身份计算 |
| `session_type` | `emotion`（当前） |
| `source_system` | `standalone` / `xin_tan_tan` / `custom` |
| `external_session_id` | N1，App 内部编号，可能为空 |
| `client_request_id` | 创建会话幂等键 |
| `device_id` | 主设备 ID |
| `anchor_utc_at` | 时间锚点（UTC） |
| `anchor_monotonic_ms` | 锚点设备单调时钟，常为空 |
| `anchor_source` | `server` / `device` |
| `anchor_uncertainty_ms` | 锚点误差上限（毫秒） |
| `late_upload_deadline` | 补传截止时间（UTC） |
| `ended_reason` | 结束原因 |
| `status` | `created` / `running` / `completed` / `interrupted` / `aborted` |
| `started_at` / `ended_at` | 会话起止（UTC） |
| `created_at` / `updated_at` | 记录时间（UTC） |

### B.2 `meta/session_devices`

| 列 | 含义 |
| --- | --- |
| `session_id` | S1 |
| `device_id` | 设备 ID |
| `role` | `primary` / `auxiliary` |
| `status` | `bound` / `unbound` |
| `bound_at` / `unbound_at` | 绑定/解绑时间（UTC） |
| `created_at` / `updated_at` | 记录时间（UTC） |

### B.3 `meta/upload_batches`

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
| `total_bytes` | 批内字节数 |
| `sha256` | 批次内容摘要 |
| `error_code` | 失败错误码，成功为空 |
| `received_at` | 接收时间（UTC） |
| `created_at` / `updated_at` | 记录时间（UTC） |

### B.4 `raw/timeline_events`

| 列 | 含义 |
| --- | --- |
| `event_id` | 客户端事件 ID |
| `session_id` | S1 |
| `device_id` | 设备 ID；纯量表事件为空 |
| `stream_id` | 数据流，纯量表事件可能为空 |
| `event_type` | 13 类白名单之一（见下） |
| `source` | `device` / `app` / `server` |
| `occurred_at` | 客户端上报发生时间（UTC），可能受设备时钟影响 |
| `relative_seconds` | 相对锚点秒数，可为负 |
| `device_timestamp` | 设备本地时间原值 |
| `monotonic_ms` | 设备单调时钟毫秒 |
| `clock_offset_ms` | 设备时钟偏移毫秒 |
| `time_uncertainty_ms` | 时间不确定度毫秒 |
| `server_received_at` | 服务端接收时间（UTC） |
| `task_id` | 任务 ID |
| `questionnaire_id` | 量表 ID |
| `questionnaire_version` | 量表版本 |
| `question_id` | 题目 ID |
| `question_index` | 题目序号 |
| `duration_ms` | 事件持续毫秒 |
| `payload` | 受控载荷（白名单键） |
| `schema_version` | 事件 Schema 版本 |
| `protocol_version` | 上传协议版本 |
| `quality` | 四档质量 |
| `quality_info` | 质量明细（JSON，含 flags） |
| `client_request_id` | 上报批次幂等键 |
| `created_at` | 入库时间（UTC） |
| `event_timestamp` | 派生 UTC：锚点 + `relative_seconds` |
| `event_timestamp_estimated` | 恒为 `true` |
| `derived_time_source` | 恒为 `server_anchor` |
| `anchor_uncertainty_ms` | 该场锚点误差（毫秒） |

`event_type`：`session_started`、`session_ended`、`device_connected`、
`device_disconnected`、`question_shown`、`response_started`、`response_submitted`、
`response_modified`、`question_skipped`、`question_completed`、`speech_started`、
`speech_ended`、`emotion_recorded`。

`payload` 允许键：`question_type`、`render_ms`、`input_method`、
`response_changed`、`modification_count`、`skip_reason_code`、`total_time_ms`、
`hesitation_count`、`audio_stream_id`、`duration_ms`、`bytes`、`ended_reason`、
`client_version`、`connection_id`、`rssi`、`record_index`、`window_ms`。

### B.5 `raw/emotion_records`

| 列 | 含义 |
| --- | --- |
| `session_id` | S1 |
| `device_id` | 采集设备 |
| `stream_id` | 表情流，通常 `facial_expression` |
| `relative_seconds` | 相对锚点秒数 |
| `expression` | `neutral`/`happiness`/`surprise`/`sadness`/`anger`/`disgust`/`fear`/`contempt` |
| `confidence` | 置信度 0–1 |
| `created_at` | 入库时间（UTC） |
| `device_timestamp` | 设备本地时间（P0 未填） |
| `monotonic_ms` | 设备单调时钟（P0 未填） |
| `clock_offset_ms` | 时钟偏移毫秒 |
| `frame_index` | 帧序号（P0 未填） |
| `quality` | 四档质量 |
| `quality_info` | 质量明细（JSON） |
| `protocol_version` | 上传协议版本 |
| `event_timestamp` | 派生 UTC 时间 |
| `event_timestamp_estimated` | 恒为 `true` |
| `derived_time_source` | 恒为 `server_anchor` |
| `anchor_uncertainty_ms` | 锚点误差（毫秒） |

### B.6 `derived/emotion_results`

| 列 | 含义 |
| --- | --- |
| `session_id` | S1 |
| `client_request_id` | 最近一次计算批次幂等键 |
| `valid_expression_seconds` | 有效表情累计秒数 |
| `dominant_expression` | 主导表情 |
| `average_confidence` | 平均置信度 |
| `face_coverage_percent` | 人脸覆盖率 |
| `result_json` | 完整结果 JSON（见下） |
| `created_at` / `updated_at` | 首次生成 / 最近重算（UTC） |

```json
{
  "expressions": [
    {"expression": "neutral", "seconds": 128.51, "percentage": 97.14}
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

时长按相邻两条的相对时间差归给前一条，最后一条只计数不计时长，故各表情
`seconds` 之和等于 `valid_expression_seconds`。

| 聚合字段 | 含义 |
| --- | --- |
| `expressions` | 各表情时长与占比数组 |
| `quality` | 采集质量指标对象 |
| `expressions[].seconds` | 该表情累计秒数 |
| `expressions[].percentage` | 占有效时长的百分比 |
| `quality.total_records` | 参与计算的表情记录条数 |
| `quality.timeline_span_seconds` | 首末记录的时间跨度 |
| `quality.valid_expression_seconds` | 有效表情累计秒数 |
| `quality.video_frames` | 采集视频帧数 |
| `quality.display_fps` | 显示帧率 |
| `quality.face_detected_frames` | 检出人脸帧数 |
| `quality.inference_runs` | 推理次数 |
| `quality.elapsed_seconds` | 采集耗时秒数 |
| `session_start` / `session_end` | 会话起止（UTC） |

### B.7 `derived/emotion_result_versions`

| 列 | 含义 |
| --- | --- |
| `session_id` | S1 |
| `result_id` | 对应 `emotion_results` 主键 |
| `version` | 会话内版本号，从 1 递增 |
| `algorithm_version` | 算法口径，当前 `emo_v1` |
| `status` | `generated` / `superseded` |
| `trigger_source` | `first_import` / `recompute` / `manual_recompute` / `backfill` |
| `source_record_count` | 生成时参与计算的表情记录数 |
| `source_sha256` | 当时源记录的规范化指纹 |
| `result_json` | 该版本完整结果快照 |
| `generated_at` / `created_at` | 生成 / 入库时间（UTC） |

### B.8 `derived/external_result_snapshots`

| 列 | 含义 |
| --- | --- |
| `snapshot_id` | 快照 ID |
| `session_id` | S1 |
| `source_system` | 来源系统 |
| `source_result_id` | 外部原始结果 ID |
| `questionnaire_code` | 量表编码 |
| `questionnaire_version` | 量表版本 |
| `scale_algorithm_version` | 量表算法版本 |
| `result_version` | 同量表结果版本，从 1 递增 |
| `supersedes_snapshot_id` | 上一版快照 ID；首版为空 |
| `status` | 结果状态 |
| `total_score` | 总分 |
| `scoring_mode` | 计分模式 |
| `state_update_status` | 状态更新结果 |
| `scale_name` | 量表名称 |
| `scale_question_count` | 题目数 |
| `dimensions` | 维度分 JSON：`{"schema":"raw_map_v1","items":[...]}` |
| `radar_norm` | 雷达归一化 JSON：`{"schema":"radar_norm_v1","series":[...]}` |
| `risk_level` | 风险等级 |
| `level_code` | 等级编码 |
| `recommendation_code` | 建议编码 |
| `ai_profile_score` | AI 画像分 |
| `summary_code` | 摘要编码 |
| `trust_level` | `client_relayed`（一期）/ `server_signed` / `server_verified` |
| `signature` | 来源签名 |
| `signature_algorithm` | 签名算法 |
| `source_payload_sha256` | 来源载荷摘要 |
| `idempotency_key` | 幂等键 `res:{S1}:{questionnaire_code}:v{result_version}` |
| `received_at` / `created_at` | 接收 / 入库时间（UTC） |

### B.9 `sessions_index.csv`

| 列 | 含义 |
| --- | --- |
| `session_id` | S1 |
| `timeline_events` | 事件行数 |
| `emotion_records` | 表情行数 |
| `emotion_results` | 当前结果行数 |
| `external_result_snapshots` | 量表快照数 |
| `upload_batches` | 上传批次数 |

## C. 校验 manifest

```powershell
$Dir = "$env:TEMP\research_export"
$M = Get-Content "$Dir\manifest.json" -Raw | ConvertFrom-Json
$Bad = @()
foreach ($f in $M.files) {
  if ((Get-FileHash (Join-Path $Dir $f.path) -Algorithm SHA256).Hash.ToLower() -ne $f.sha256) { $Bad += $f.path }
}
if ($Bad.Count -eq 0) { "全部文件校验通过" } else { "失败: $($Bad -join ', ')" }
```

响应头 `X-Export-Manifest-Sha256` 应等于解压后根 `manifest.json` 的 sha256。

## D. 进阶：`POST /research/exports`

需要 UTC 时间范围、按数据类型/格式筛选时使用：

| 参数 | 必填 | 取值 |
| --- | --- | --- |
| `session_ids` | 与 `export_all` 二选一 | 最多 200 个 S1 |
| `export_all` | 与 `session_ids` 二选一 | `true`/`false` |
| `from_utc` / `to_utc` | 否 | ISO8601 UTC，如 `2026-11-01T00:00:00Z` |
| `from_seconds` / `to_seconds` | 否 | 浮点秒 |
| `data_types` | 否 | `timeline`/`emotion`/`results`/`snapshots`/`batches` |
| `format` | 否 | `csv`/`jsonl`/`both` |

两个都填或都不填（会话选择）→ 422；下界大于上界 → 422；未知字段 → 422。

## E. 错误码与自查

| 状态码 | 原因 | 处理 |
| --- | --- | --- |
| 401 | 缺少/错误 `X-Api-Key`（或 `api_key`） | 核对密钥与头名 |
| 404 | S1 不存在，或时间条件筛完没有会话 | 先用会话清单确认 S1、放宽时间 |
| 422 | 时间格式错、from 晚于 to、参数冲突/拼写错 | 按提示改参数；日期用 `2026-11-01` 或 ISO8601 |
| 500 | 服务端数据库/导出异常 | 联系维护者看服务日志 |

- **导出为空**：看 `sessions_index.csv` 该场计数；确认时间用的是北京时间；
  若 `data_types` 只给了 `results`，不会有 `raw/`。
- **某列全空**：先看 `quality_report.json` 的 `fill_rates`。P0 阶段
  `device_timestamp`/`monotonic_ms`/`frame_index` 本就为空，对应 `quality=legacy`。
- **时间对不上**：按 `event_timestamp`（派生 UTC）排轴，加 8 小时即北京时间。

## F. 与命令行脚本的差异

HTTP 接口与 `scripts/research_export.py` 共用列白名单、隐私裁剪、质量报告和
manifest 逻辑。脚本可用 `--include-external-ids` 导出 `user_id`/`external_user_id`；
HTTP 接口固定不导出这两列。P0 的 `/api/v1/sessions/{id}/exports` 只导出表情结果序列，
科研分析请用本手册的 `/api/v1/research/...` 接口。
