# 多模态数据接入规范（P1-B）

| 项目 | 内容 |
| --- | --- |
| 版本 | 1.0（2026-11-22） |
| 依据 | 《AI 心探探多模态数据采集平台总体架构设计说明书 v1.4》§6 / §7 / §8 / §11 / §13 / §15；《P1 后续安全加固及设备能力升级清单》§3 |
| 定位 | **规范文本，不建表、不加字段、不预埋 migration、不改路由、不动生产** |
| 适用对象 | 后续接入心率、ECG、PPG、血检、语音等模态的设备端、App 端与服务端开发者 |
| 验收口径 | 任一新模态都能回答四件事：`modality` 是什么、时间用哪个原点、单位是什么、数据落在「流 + 文件」还是「离散行」 |

---

## 1. 范围与结论先行

### 1.1 本文回答什么

1. 新模态用什么标识、数据来源怎么记；
2. 时间、单位、采样信息怎么表达；
3. 连续时序（ECG 波形、心率序列）与离散结果（血检指标）分别怎么承载；
4. 元数据与数据体如何分离、文件型大负载如何登记；
5. 在不改现有核心业务逻辑的前提下，新增一种模态要做什么；
6. 新模态如何被导出与版本追溯覆盖；
7. 哪些做法明确不做。

### 1.2 两类结论

| 类别 | 结论 | 依据 |
| --- | --- | --- |
| **现在就能用现有结构承载** | ① 离散、低频、单次的结果（如血检一组指标）可作为 `timeline_events` 的一类事件，指标放 `payload`；② 连续流的**登记信息**可由 `upload_batches`（批次对账）+ `timeline_events`（起止标记）承载 | `timeline_events.payload` 是受控 JSON、允许非禁用键；`upload_batches` 已有 `batch_type/item_count/total_bytes/sha256` |
| **将来必须新增结构** | ① 波形/高采样序列的**样点本体**（ECG、原始 PPG）需要“流元数据表 + 分段文件”；② 文件生命周期、校验与导出需要文件登记表 | 现有表无 `stream_id` 主表、无文件 URI/sha256 登记行；`emotion_records` 列是表情专用且 NOT NULL，不能复用 |

> 关键判断：**P1 阶段不新增任何表**。真正接入 ECG 等波形时，再按本文 §8 的清单出一次 migration（对应原 P1-5 `raw_data_files` / P1-11 `data_streams`）。

---

## 2. 现有结构事实（以此为准）

### 2.1 会话与设备

| 表 | 关键字段 | 与多模态的关系 |
| --- | --- | --- |
| `sessions` | `session_id`(S1)、`device_id`(主设备)、`user_id`、`external_user_id`、`subject_ref`、`session_type`、`source_system`、`external_session_id`(N1)、`client_request_id`、`anchor_utc_at`、`anchor_source`、`anchor_uncertainty_ms`、`late_upload_deadline`、`status`、`started_at`、`ended_at` | 一个 S1 是所有模态的聚合根；`anchor_utc_at` 是会话内相对时间的唯一原点 |
| `session_devices` | `session_id`、`device_id`、`role`(`primary`/`auxiliary`)、`status`(`bound`/`unbound`)、`bound_at`、`unbound_at` | 心率/ECG 设备作为 `auxiliary` 挂到同一 S1；`UNIQUE(session_id, device_id)`；会话结束后仍保持 `bound`（迟到上传依赖） |
| `devices` | `device_id`、`device_type`、`manufacturer`、`model`、`firmware_version`、`status`、`capabilities`(JSON 数组)、`identity_changed_at`、`token_rotated_at`、`token_revoked_at` | `capabilities` 由设备注册时自报，默认空数组（`[]`）；是否允许该设备创建某类会话由服务端能力映射决定 |
| `device_connections` | `connection_id`、`device_id`、`app_client_id`、`user_id`、`status`、`connected_at`、`disconnected_at` | 手机侧建会话/绑定设备的连接依据，不承载模态数据 |

`session_type` 允许值（代码 `SessionType`，已冻结）：`emotion` / `multimodal` / `assessment` / `physiological` / `speech` / `custom`。
`source_system` 允许值（代码 `SourceSystem`）：`xin_tan_tan` / `standalone` / `custom`；`custom` 保留但客户端提交时拒绝。

### 2.2 数据表

| 表 | 关键字段 | 约束 / 语义 |
| --- | --- | --- |
| `timeline_events` | `event_id`、`session_id`、`device_id?`、`stream_id?`、`event_type`、`source`、`occurred_at`、`relative_seconds`、`device_timestamp?`、`monotonic_ms?`、`clock_offset_ms?`、`time_uncertainty_ms?`、`server_received_at`、`task_id?`、`questionnaire_id?`、`questionnaire_version?`、`question_id?`、`question_index?`、`duration_ms?`、`payload`(JSON)、`schema_version`、`protocol_version?`、`quality`、`quality_info`(JSON)、`client_request_id` | `UNIQUE(session_id, source, event_id)` 事件级幂等；`event_id` 全局唯一；`payload` 为受控白名单，答案类键会被裁掉；`quality` 由服务器计算，客户端声明忽略 |
| `emotion_records` | `session_id`、`device_id?`、`stream_id?`、`relative_seconds`、`expression`、`confidence`、`device_timestamp?`、`monotonic_ms?`、`clock_offset_ms?`、`frame_index?`、`quality`、`quality_info`、`protocol_version?` | **表情专用**：`expression` / `confidence` NOT NULL，不能用来存心率/ECG |
| `upload_batches` | `upload_id`、`session_id`、`device_id`、`app_client_id?`、`client_request_id`、`batch_type`、`status`、`item_count`、`total_bytes`、`sha256?`、`error_code?`、`received_at` | `UNIQUE(session_id, client_request_id)` 批次幂等；`batch_type` 现为 `event`/`emotion`/`raw`/`result`；是“对账”行，不是数据本体 |
| `emotion_results` | `session_id`(UNIQUE)、`client_request_id`、`valid_expression_seconds`、`dominant_expression`、`average_confidence`、`face_coverage_percent`、`result_json` | 表情派生结果，单会话单行；不承载其他模态 |
| `emotion_result_versions` | `session_id`、`result_id`、`version`、`algorithm_version`、`status`、`trigger_source`、`source_record_count`、`source_sha256`、`result_json`、`generated_at` | 版本追溯范式：算法版本 + 源记录指纹 + 结果快照 |
| `external_result_snapshots` | `snapshot_id`、`session_id`、`source_system`、`source_result_id?`、`questionnaire_code`、`questionnaire_version?`、`scale_algorithm_version?`、`result_version`、`supersedes_snapshot_id?`、`status`、`total_score?`、`dimensions?`、`radar_norm?`、`risk_level?`、`trust_level`、`signature?`、`source_payload_sha256?`、`idempotency_key`、`received_at` | **量表/问卷结果**专用（`questionnaire_code` 必填语义）；血检等非量表结果不要塞进来 |

### 2.3 时间原点与关联键

- `anchor_utc_at`：会话锚点（UTC），由服务端在会话创建/锚定流程中确定；`anchor_source` 记录来源，`anchor_uncertainty_ms` 记录不确定度。
- `relative_seconds`：相对 `anchor_utc_at` 的秒数，浮点，允许负值（首题前锚缓冲）；`timeline_events` 与 `emotion_records` 共用同一原点。
- 跨模态关联键固定为：`session_id` + `device_id` + `relative_seconds`（+ `stream_id` 区分同一设备的多个流）。
- `occurred_at` 是绝对 UTC 时间；**派生时间不得覆盖原始采集时间**，无法确定时用 `quality=estimated|missing|legacy` 表达。

### 2.4 质量口径（沿用，不新增枚举）

| `quality` | 含义 | 多模态用法 |
| --- | --- | --- |
| `device` | 设备提供了可信时间 | 设备带 RTC/单调时钟且已校准时 |
| `estimated` | 服务器按可用字段推导 | 仅设备墙钟或仅单调时钟时 |
| `missing` | 完全无时间信息 | 只有文件、无任何时间字段 |
| `legacy` | 旧客户端未上报时间字段 | 仅用于历史兼容，不得用于新模态 |

`quality_info` 是自由 JSON，建议键：`reason`、`clock_offset_ms`、`anchor_uncertainty_ms`、`dropped_samples`、`saturation`、`lead_off`、`unit_converted_from`。

---

## 3. 模态标识与命名

### 3.1 受控取值（只定取值，不建表）

| `modality` | 中文 | 形态 | 规范单位 | 典型采样 |
| --- | --- | --- | --- | --- |
| `facial_expression` | 表情识别 | 离散序列（帧级） | 类别 + 置信度（0~1） | 事件/帧驱动 |
| `speech_audio` | 语音/语言交互 | 离散事件 + 可选音频文件 | 毫秒 / 字节 | 事件驱动 |
| `heart_rate` | 心率 | 连续时间序列 | `bpm` | 1–10 Hz |
| `ecg` | 心电波形 | 连续波形 | `mV` | 250–1000 Hz |
| `blood_panel` | 血液检测 | 离散检测结果（一组指标） | 项目相关（`g/L`、`mmol/L`、`10^9/L`…） | 单次 |
| `questionnaire` | 量表过程 | 离散事件 | 无量纲 | 事件驱动 |

命名规则：

1. `modality` 一律小写 `snake_case`，不复用 `session_type` 的取值（`physiological` 是会话类型，不是模态）。
2. 新增模态先在本表登记，再写接入代码；未登记的字符串不得进入生产数据。
3. 同一物理信号的不同派生层级用后缀区分，例如 `heart_rate`（派生）与 `ecg`（原始波形）；不要用 `ecg_hr` 之类混合名。
4. 设备能力 `devices.capabilities` 与 `modality` 使用同一套字符串；一个设备可声明多个能力，如 `["emotion","heart_rate"]`。

### 3.2 数据来源标识

| 字段 | 取值 | 使用规则 |
| --- | --- | --- |
| `timeline_events.source` | `device` / `app` / `server` | 谁产生这条事件；**不新增来源枚举** |
| `sessions.source_system` | `xin_tan_tan` / `standalone` / `custom` | 会话来自哪个系统；`custom` 客户端不可提交；新增模态不改变该枚举 |
| `session_devices.role` | `primary` / `auxiliary` | 表情主设备为 `primary`，心率/ECG 设备为 `auxiliary` |
| `timeline_events.stream_id` / `emotion_records.stream_id` | 自由字符串（≤40） | 同一设备多流时必填；建议 `<modality>.<device_id>` 或 `<modality>.<channel>` |

---

## 4. 时间、单位与采样约定

1. **时间**：入库一律 UTC；绝对时间 ISO8601（毫秒 + `Z`）；会话内相对时间用 `relative_seconds`，原点只能是 `sessions.anchor_utc_at`。
2. **跨设备对齐**：只能声明“同会话内对齐”，不得声称同步采样；必须携带 `anchor_uncertainty_ms`（会话级）或 `time_uncertainty_ms`（事件级）。
3. **单位**：入库存规范单位，单位随值走（`unit` 键），**不允许裸数字**；换算在接入端完成，服务端不改写。
4. **采样**：连续流必须声明 `sample_rate_hz` 与 `channel_count`；分段文件必须声明每段起止相对时间与样点数。
5. **缺失**：`null` 表示未采集；`0` 不得表示缺失；超出量程用 `quality_info.saturation=true` 标记，不得截断后当正常值。
6. **时钟**：设备端优先上报 `monotonic_ms` + `device_timestamp`，由服务器计算 `clock_offset_ms`；禁止用上传时刻冒充采集时刻。

---

## 5. 两类承载形态

### 5.1 离散检测结果（现在可用现有结构）

适用：血检一组指标、单次体温/血压、单次问卷评分等“一次检测、多行指标”。

**承载方式**：每个检测项一条 `timeline_events`，`event_type=lab_result`（或 `physiological_measurement`），`source=device|app`，指标放 `payload`。

```json
{
  "event_id": "9f8e7d6c5b4a39281706f5e4d3c2b1a0",
  "event_type": "lab_result",
  "occurred_at": "2026-11-22T02:15:00.000Z",
  "relative_seconds": 3720.0,
  "device_id": "lab_gateway_01",
  "stream_id": "blood_panel.lab_gateway_01",
  "payload": {
    "modality": "blood_panel",
    "panel_code": "CBC_BASIC",
    "code": "HGB",
    "value": 135.0,
    "unit": "g/L",
    "ref_range": {"low": 115.0, "high": 150.0},
    "measured_at": "2026-11-22T02:10:00.000Z",
    "method": {"device": "Sysmex XN-550", "reagent_lot": "A1234", "assay_version": "1.0"}
  },
  "schema_version": "1.0",
  "client_request_id": "req-lab-20261122-0001"
}
```

规则：

- `payload` 必须包含 `modality` / `code` / `value` / `unit`，可选 `ref_range` / `measured_at` / `method` / `panel_code`。
- 同一 `panel_code` 的多个指标用多条事件、同一 `relative_seconds` 或各自 `measured_at`，共享 `stream_id`。
- 幂等由 `UNIQUE(session_id, source, event_id)` 保证；一次批次登记一条 `upload_batches(batch_type="raw")`。
- 不把血检写进 `external_result_snapshots`（其语义是量表结果，`questionnaire_code` 必填）。

### 5.2 连续时序（现在只能登记，样点需新增结构）

适用：ECG 波形、原始 PPG、高频心率序列。

**现在可做**（不改表）：

- 一次上传登记一条 `upload_batches`：`batch_type="raw"`、`item_count`=样点数、`total_bytes`、`sha256`、`status="received"`。
- `timeline_events` 写起止标记（`event_type=stream_started|stream_ended`），`payload` 记录 `modality`、`stream_id`、`sample_rate_hz`、`channel_count`、`unit`、`duration_seconds`、`file_name`（占位，不做 URI 解析）。
- 样点文件暂存设备/本地，不入库、不进入现有导出。

**必须新增结构**（触发条件满足时）：

- 单段样点 > 10,000 或字节 > 1 MB → 走“元数据表 + 分段文件”。
- 分段建议 10–60 秒或 ≤ 1 MB；文件与元数据同 `sha256`；导出按文件打包。
- 新表建议（名称待迁移评审，不在 P1 落地）：流表（`session_id`、`device_id`、`stream_id`、`modality`、`unit`、`sample_rate_hz`、`channel_count`、起止 `relative_seconds`、`quality`）与文件表（`stream_id`、`segment_index`、`uri`、`sha256`、`bytes`、`sample_count`）。
- 明确不采用：波形 BLOB 直接入库；对象存储（P2 再评估）。

### 5.3 判决表

| 数据 | 现在能否承载 | 落点 | 何时必须新增结构 |
| --- | --- | --- | --- |
| 血检指标 | ✅ | `timeline_events.payload` + `upload_batches` | 需要按指标类型做 SQL 聚合/索引时 |
| 单次心率/血压 | ✅ | 同上 | 同上 |
| 心率序列（≤1 万点/段） | ⚠️ 仅登记 | `upload_batches` + 起止事件 | 需要样点查询或统计时 |
| ECG/PPG 波形 | ⚠️ 仅登记 | `upload_batches` + 起止事件 | 立即需要（>1 万点/段必触发） |
| 语音文件 | ⚠️ 仅登记 | `upload_batches` + 起止事件 | 需要转写/特征对齐时 |

---

## 6. 元数据与数据体分离

| 层 | 内容 | 存放 |
| --- | --- | --- |
| 会话层 | S1、受试者、来源系统、锚点 | `sessions` |
| 设备层 | 设备身份、能力、固件 | `devices` |
| 绑定层 | 哪个设备参与哪个会话、主/辅 | `session_devices` |
| 批次层 | 一次上传的条数、字节、sha256、状态 | `upload_batches` |
| 事件/流层 | 模态、时间、单位、采样、质量 | `timeline_events`（+ 将来的流表） |
| 数据体 | 离散指标值 / 波形样点 | `payload`（小）或分段文件（大） |
| 派生层 | 算法结果及版本 | `*_result_versions` 范式 |

原则：**元数据可查询、数据体可校验、两者用 `stream_id` + `sha256` 对上**；不得把大二进制塞进 `payload`。

---

## 7. 新增一种模态的接入清单

以接入 `heart_rate`（设备直连、1 Hz、同会话 auxiliary）为例：

1. **登记标识**：在本文 §3.1 表中确认 `heart_rate` 已存在；设备注册时 `capabilities` 增加 `"heart_rate"`。
2. **绑定会话**：手机 `POST /sessions/{sid}/devices` 把心率设备以 `role=auxiliary` 绑到同一 S1；若设备无法绑定，才新建独立 S1（`session_type=physiological`）。
3. **上报**：按 §5.1（低频离散点）或 §5.2（高频序列）选择形态；统一携带 `relative_seconds`、`unit`、`quality` 所需字段。
4. **批次登记**：每次批量提交写 `upload_batches`，`client_request_id` 幂等。
5. **不改核心逻辑**：不新增路由、不改 `emotion.py`/`sessions.py`、不改 `session_type` 白名单；复用现有事件上传与批次机制。
6. **导出**：若只写 `timeline_events`，现有导出已覆盖（`data_types=timeline`）；若新增表，按 §8.2 注册。
7. **验收**：任取一条新模态数据，能从 S1 → 设备 → 批次 → 事件 → 导出文件完整追溯；`relative_seconds` 能被 `anchor_utc_at` 还原为绝对时间。

最小代码影响：**0 个路由、0 张表、0 个字段**；仅设备端上报内容与本文取值表。

---

## 8. 与导出、版本追溯的衔接

### 8.1 现有导出覆盖面

| 导出 `data_types` | 覆盖表 | 新模态是否自动覆盖 |
| --- | --- | --- |
| `timeline` | `timeline_events` | ✅ 离散结果与流起止标记自动进入 CSV/JSONL |
| `emotion` | `emotion_records` | ❌（表情专用，不复用） |
| `results` | `emotion_results` + `emotion_result_versions` | ❌（新模态需自己的结果表/版本表） |
| `snapshots` | `external_result_snapshots` | ❌（量表专用） |
| `batches` | `upload_batches` | ✅ 批次登记自动进入 |

导出入口：`scripts/research_export.py`（CLI）与 `/api/v1/research/*`（API，内部复用同一模块）。导出清单在 `manifest.json` 记录文件级 `sha256`，`quality_report.json` 记录质量分布与异常，`data_dictionary.md` 说明字段。

### 8.2 将来新增表时必须做的三件事

1. 在 `scripts/research_export.py` 的 `_DATA_TYPES` 增加 `"<name>": ["<table>", ...]`；
2. 在 `app/schemas/research_export.py` 的 `DataTypes` Literal 增加同名取值（CLI 与 API 的入参校验）；
3. 若表含 JSON 列，加入 `_JSON_COLUMNS`；若含时间列，加入 `_DT_COLUMNS`。

无需改导出路由、无需改 manifest 结构。未注册的表**不会**被导出——这是“新增模态必须登记”的硬约束。

### 8.3 版本追溯

- 派生结果沿用 `emotion_result_versions` 的范式：`version`、`algorithm_version`、`status`、`trigger_source`、`source_record_count`、`source_sha256`、`result_json`、`generated_at`。
- 新模态的版本表建议命名为 `<modality>_result_versions`，并保持同样的指纹字段，便于导出侧按 `results` 同级扩展。
- 外部系统结果继续走 `external_result_snapshots`，用 `result_version` + `supersedes_snapshot_id` 表达覆盖关系；该表不承载设备原始波形。

---

## 9. 明确不做

1. 不为未接入的模态建表、加字段、预埋空 migration。
2. 不用 `emotion_records` 存心率/ECG（列语义与 NOT NULL 约束不允许）。
3. 不把血检结果塞进 `external_result_snapshots`（量表专用）。
4. 不把波形 BLOB 直接入库；不引入对象存储（P2 再评估）。
5. 不新增 `source` / `source_system` 枚举值。
6. 不新增路由或改现有核心业务逻辑；不改 `session_type` 白名单。
7. 不做模态级权限、不做多中心、不做实时流式推送。
8. 不在 P1 阶段实现文件 URI 解析、生命周期清理与断点续传（这些属于文件登记落地时的配套）。

---

## 10. 变更记录

| 日期 | 版本 | 内容 |
| --- | --- | --- |
| 2026-11-22 | 1.0 | 首次发布：模态标识、时间/单位/采样、两类承载形态、导出与版本衔接、接入清单与明确不做事项 |
