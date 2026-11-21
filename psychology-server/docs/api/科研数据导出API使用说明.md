# 科研数据导出 API 使用说明

本文说明如何通过 HTTP 直接调用科研数据导出接口，输出与命令行脚本
`scripts/research_export.py` 完全同口径：原始数据与派生结果分目录、CSV/JSONL、
数据字典、质量报告、含 sha256 的 manifest，多会话自动打包 zip。

- 适用版本：psychology-server（迁移 head `0011_p1d_emotion_result_versions`）
- 实现位置：`app/api/exports.py`、`app/services/research_export_service.py`
- 导出口径来源：`scripts/research_export.py`（列白名单、隐私裁剪、质量报告均复用，未另写）

---

## 1. 鉴权

所有科研导出接口沿用现有查询鉴权：

| 请求头 | 说明 |
| --- | --- |
| `X-Api-Key` | 服务端 `QUERY_API_KEY` 配置值；缺失或错误返回 401 |

设备侧使用的 `X-Device-Id` / `X-Device-Token` **不适用于**本接口。

---

## 2. 端点

### 2.1 创建科研导出（单会话 / 多会话 / 全量）

```text
POST /api/v1/research/exports
Content-Type: application/json
X-Api-Key: <QUERY_API_KEY>
```

请求体：

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `session_ids` | string[] | `[]` | 会话号 S1 列表；与 `export_all` 二选一 |
| `export_all` | bool | `false` | 导出全部会话（仍受 `from_utc` / `to_utc` 约束） |
| `from_utc` | string | `null` | 会话时间下界，ISO8601；按 `coalesce(started_at, created_at)` 比较 |
| `to_utc` | string | `null` | 会话时间上界，ISO8601 |
| `from_seconds` | float | `null` | 行级 `relative_seconds` 下界（仅 timeline_events / emotion_records） |
| `to_seconds` | float | `null` | 行级 `relative_seconds` 上界 |
| `data_types` | string[] | 全部 | `timeline` / `emotion` / `results` / `snapshots` / `batches` |
| `format` | string | `both` | `csv` / `jsonl` / `both` |

约束：

- `session_ids` 与 `export_all=true` 必须且只能提供一个；
- 时间上下界、秒级上下界不允许下界大于上界（否则 422）；
- `sessions` 与 `session_devices` 始终导出，不受 `data_types` 影响；
- 不提供外部用户 ID 开关，`user_id` / `external_user_id` 固定不导出。

`data_types` 与表的对应关系：

| data_types | 导出表 |
| --- | --- |
| `timeline` | `timeline_events` |
| `emotion` | `emotion_records` |
| `results` | `emotion_results`、`emotion_result_versions` |
| `snapshots` | `external_result_snapshots` |
| `batches` | `upload_batches` |
| 始终 | `sessions`、`session_devices` |

响应：`200`，`Content-Type: application/zip`，zip 内结构见第 4 节。

响应头：

| 响应头 | 说明 |
| --- | --- |
| `X-Export-Manifest-Sha256` | 包内根 `manifest.json` 的 sha256 |
| `X-Export-Sessions` | 本次导出的会话数 |
| `X-Export-Total-Counts` | 各表总行数（JSON 字符串） |
| `X-Export-Missing-Tables` | 旧库缺少的表（正常为 `none`） |

### 2.2 导出单场会话

```text
GET /api/v1/research/sessions/{session_id}/export
X-Api-Key: <QUERY_API_KEY>
```

查询参数：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `format` | string | `both` | `csv` / `jsonl` / `both` |
| `from_seconds` | float | `null` | 行级相对时间下界 |
| `to_seconds` | float | `null` | 行级相对时间上界 |
| `data_types` | string | 全部 | 逗号分隔，如 `timeline,emotion,results` |

会话不存在返回 `404`；返回结构与 2.1 相同（zip）。

### 2.3 相关但非本次新增的接口

以下 P0 接口保持不变，可按需配合使用：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/sessions/{session_id}/manifest` | 单会话清单（条数、锚点、派生时间语义） |
| POST | `/api/v1/sessions/{session_id}/exports` | P0 单会话导出（仅表情序列，csv/json） |
| GET | `/api/v1/exports/{export_id}?download=true` | P0 导出任务查询/下载 |

科研分析请优先使用 `research/exports`：它保留事件级原始行、区分 raw/derived，
并提供数据字典、质量报告与全量 manifest。

---

## 3. curl 示例

### 3.1 单会话导出（全部数据类型，CSV+JSONL）

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v1/research/exports" \
  -H "X-Api-Key: $QUERY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"session_ids":["029641f3afa3432180d572c154063e97"],"format":"both"}' \
  -o research_single.zip
```

### 3.2 按时间范围批量导出（只要原始事件与表情）

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/v1/research/exports" \
  -H "X-Api-Key: $QUERY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "export_all": true,
        "from_utc": "2026-11-01T00:00:00Z",
        "to_utc": "2026-11-30T23:59:59Z",
        "data_types": ["timeline", "emotion"],
        "format": "jsonl"
      }' \
  -o research_batch.zip
```

### 3.3 单会话时间窗（动作编码分析只看 30–120 秒）

```bash
curl -sS "http://127.0.0.1:8000/api/v1/research/sessions/S1/export?format=csv&from_seconds=30&to_seconds=120&data_types=emotion,results" \
  -H "X-Api-Key: $QUERY_API_KEY" \
  -o research_window.zip
```

PowerShell 中请使用 `curl.exe`，并将 JSON 写入文件后用
`--data-binary "@body.json"` 传递，避免引号转义问题。

---

## 4. 返回 zip 结构

```text
research_export_<UTC时间戳>/
├── data_dictionary.md          # 列级数据字典（含脱敏与派生时间说明）
├── quality_report.json         # 全局质量报告（各会话 + totals）
├── sessions_index.csv          # 会话行数索引
├── manifest.json               # 根清单：文件 sha256、筛选条件、库指纹、missing_tables
└── sessions/
    └── <session_id>/
        ├── meta/
        │   ├── sessions.csv|jsonl
        │   └── session_devices.csv|jsonl
        ├── raw/                # 原始数据：事件级完整行
        │   ├── timeline_events.csv|jsonl
        │   └── emotion_records.csv|jsonl
        ├── derived/            # 服务端计算结果
        │   ├── emotion_results.csv|jsonl
        │   ├── emotion_result_versions.csv|jsonl
        │   ├── external_result_snapshots.csv|jsonl
        │   └── upload_batches.csv|jsonl
        ├── quality_report.json
        └── manifest.json
```

说明：

- `format=csv` 时只生成 `.csv`，`jsonl` 同理，`both` 同时生成；
- 未选择的 `data_types` 不会产生目录/文件；
- 根 `manifest.json` 的 `files[].sha256` 覆盖包内每个文件，可用于落盘后校验；
- 旧库副本若缺少某张表（如未做 P1-D 迁移），该表跳过并记录在 `missing_tables`，不中断导出。

---

## 5. 字段口径与隐私限制

- **时间**：库内时间为 naive UTC；导出统一转 ISO8601（`...Z`）。
  `timeline_events` / `emotion_records` 额外输出派生 `event_timestamp`
  （锚点 + `relative_seconds`），并标记 `event_timestamp_estimated=true`；
  派生值不回写数据库、不修改 `quality`。
- **原始数据完整保留**：事件按 `relative_seconds` 升序逐行导出，包含事件类型、
  来源、质量、`payload`（按事件类型白名单裁剪后）等，不做统计汇总替代。
- **禁字段**：`payload` 中的答案原文、逐题 score、转写文本等一律不导出；
  质量报告只记录被裁剪的键名与次数，不记录值。
- **身份字段**：`subject_ref`（HMAC 脱敏）默认导出；`user_id` / `external_user_id`
  固定不导出；`external_session_id`（N1）原样导出用于跨系统关联。
- **只读**：每次导出以 `file:...?mode=ro` 独立打开 SQLite，不写库、不改表。

---

## 6. 常见错误码

| 状态码 | 场景 | 处理建议 |
| --- | --- | --- |
| 401 | 缺失/错误 `X-Api-Key` | 检查 `QUERY_API_KEY` 配置与请求头 |
| 404 | 指定会话不存在，或无会话命中时间范围 | 用 `GET /api/v1/sessions` 核对 S1 |
| 422 | 参数校验失败（二选一冲突、时间上下界颠倒、data_types 非法） | 按响应 `message` 修正 |
| 500 | 数据库文件缺失/不可读、导出模块不可用 | 检查 `DATABASE_URL` 与服务端日志 |
| 501 | 非 SQLite 数据库 | 当前仅支持 SQLite 文件 |

错误响应沿用全局结构：

```json
{"code": 404, "message": "no sessions matched the filters", "data": null}
```

---

## 7. 与命令行脚本、P0 导出接口的差异

| 维度 | 科研导出 API | `scripts/research_export.py` | P0 `/sessions/{id}/exports` |
| --- | --- | --- | --- |
| 入口 | HTTP | 命令行 | HTTP |
| 数据库 | 服务端 `DATABASE_URL` 指向的文件 | `--db` 任意路径 | 当前应用库 |
| 输出 | 始终 zip | 目录，可选 zip | 单文件 csv/json |
| 原始事件 | 完整导出 | 完整导出 | 不包含 |
| raw/derived | 分目录 | 分目录 | 无 |
| 数据字典/质量报告 | 包含 | 包含 | manifest 仅条数与派生时间 |
| 外部用户 ID | 固定不导出 | `--include-external-ids` 可选 | 无 |
| 时间筛选 | 会话级 + 行级 | 会话级 + 行级 | 行级 |

API 未扩展 CLI 的 `--include-external-ids`，以避免经网络接口扩大身份数据暴露面；
确有需要时在服务器本地用脚本显式开启。
