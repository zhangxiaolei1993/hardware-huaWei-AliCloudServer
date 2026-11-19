# P1-A 科研数据导出使用说明

> 适用版本：`scripts/research_export.py` v1.0.0（2026-11-19）
> 关联计划：《P1 后续能力升级清单（2026-11-19 重排版）》任务一
> 性质：**只读脚本**。不经过 API、不写数据库、不改表、不改接口、不改 `main.py`。

---

## 1. 它能做什么

- 按会话（单个 / 多个 / 全部）、会话时间范围、行级相对时间、数据类型筛选；
- 每场会话输出**原始数据（raw）**与**分析结果（derived）**两组文件，目录上直接分开；
- 同时生成 `CSV` 与 `JSONL`（`CSV` 供 Pandas 直接读，`JSONL` 逐行加载）；
- 生成**质量报告**、**数据字典**、**manifest**（每个文件的 sha256、行数、脚本版本、库文件指纹、生成时间）；
- 批量导出自动打 zip；
- 默认不导出 `user_id` / `external_user_id`，事件 `payload` 按事件类型白名单裁剪。

---

## 2. 怎么跑

在 `psychology-server/` 目录下：

```bash
# 单场会话
python scripts/research_export.py --db psychology.db --session <S1>

# 多场会话（从文件读）
python scripts/research_export.py --db psychology.db --sessions-file sids.txt --zip

# 全部会话 + 时间范围 + 只看原始表情与事件
python scripts/research_export.py --db copy.db --all \
  --from-utc 2026-11-01T00:00:00Z --to-utc 2026-11-20T00:00:00Z \
  --data-type timeline --data-type emotion --zip

# 指定输出目录（默认 exports/research/research_export_<UTC 时间>）
python scripts/research_export.py --db copy.db --all --out exports/research/batch_20261119

# 行级相对时间过滤（relative_seconds）
python scripts/research_export.py --db copy.db --session <S1> --from 0 --to 300
```

**生产数据请先做只读副本**（例如把 `/opt/psychology-server/psychology.db` 复制到本机临时目录），再把 `--db` 指向副本。脚本本身使用 `file:...?mode=ro` 与 `PRAGMA query_only=ON`，不会产生写操作。

查看全部参数：`python scripts/research_export.py --help`。

---

## 3. 输出结构

```text
exports/research/research_export_<ts>/
├── manifest.json            # 根清单：会话列表、总行数、库指纹、全部文件 sha256
├── quality_report.json      # 全部会话的质量报告（按 session_id 分组 + totals）
├── data_dictionary.md       # 字段说明（列名 / 含义 / 单位 / 派生说明）
├── sessions_index.csv       # 会话行数索引，便于先筛后读
└── sessions/<S1>/
    ├── meta/
    │   ├── sessions.csv|jsonl
    │   ├── session_devices.csv|jsonl
    │   └── upload_batches.csv|jsonl
    ├── raw/                 # 原始采集数据（不覆盖、不重算）
    │   ├── timeline_events.csv|jsonl
    │   └── emotion_records.csv|jsonl
    ├── derived/             # 分析结果（服务端计算/外部量表结果）
    │   ├── emotion_results.csv|jsonl
    │   └── external_result_snapshots.csv|jsonl
    ├── quality_report.json  # 本会话质量报告
    └── manifest.json        # 本会话文件清单（sha256/行数）
```

批量导出时，在输出目录同级生成 `<目录名>.zip`（多会话默认打包，`--zip` 可强制单会话也打包）。

---

## 4. 字段与时间口径

- 所有时间字段统一为 **UTC ISO8601（毫秒 + `Z`）**；
- 原始行同时保留 `relative_seconds`（相对会话锚点秒数，允许负值）；
- `event_timestamp` / `event_timestamp_estimated` / `derived_time_source` / `anchor_uncertainty_ms` 为**导出派生列**，由 `app/services/export_service.py` 的锚点函数计算，**不回写数据库**；
- 嵌套 JSON 列（`payload`、`quality_info`、`result_json`、`dimensions`、`radar_norm`）在 CSV 中是紧凑 JSON 字符串，在 JSONL 中是对象；
- 字段含义与来源以输出目录中的 `data_dictionary.md` 为准（脚本内维护，随代码更新）。

---

## 5. 质量报告包含什么

`quality_report.json` 每场会话包含：

| 项 | 内容 |
| --- | --- |
| `tables` | 每张导出表的行数 + 每个字段的填充率（含 `device_timestamp`/`monotonic_ms`/`frame_index`/`stream_id`） |
| `quality_distribution` | `quality` 四值分布（device/estimated/missing/legacy） |
| `quality_info_flags` | `quality_info.flags` 各标记计数（如 `stream_defaulted`、`pre_anchor`、`clock_unverified`、`batch_upload`） |
| `time_range` | 每表 `relative_seconds` 最小/最大；会话 `started_at`/`ended_at`/锚点/不确定度 |
| `anomalies` | 负 `relative_seconds`、超出会话窗口、重复事件键、空 `event_id`、结果/批次重复键 |
| `payload_keys_dropped` | 被白名单裁剪掉的 `payload` 键名及次数（**只记键名，不记值**） |

根级 `quality_report.json` 汇总全部会话，并给出 `totals`。

---

## 6. 隐私与禁字段

- 默认**不输出** `user_id`、`external_user_id`；确需时加 `--include-external-ids`（会写入导出物，请自行控制分发）。
- 事件 `payload` 只保留该事件类型白名单内的键；答案原文、逐题分值、转写文本等键会被丢弃，并在质量报告 `payload_keys_dropped` 中计数。
- 所有表按**显式列白名单**取数，不使用 `SELECT *`；导出文件不含 `answer_text`/`answer_value`/`transcript`/`speech_text` 等禁字段。

---

## 7. 校验与复现

- 每份文件的行数与 sha256 见对应 `manifest.json`；
- 同一份库、同一组筛选参数重复导出，除 `manifest.json` 的 `generated_at` 外，所有数据文件 sha256 完全一致（已用生产只读副本验证）；
- 用 Pandas 读取：

```python
import pandas as pd
df = pd.read_csv("sessions/<S1>/raw/emotion_records.csv")
ev = pd.read_json("sessions/<S1>/raw/timeline_events.jsonl", lines=True)
```

- 跨模态关联统一用 `session_id`（S1）；同一会话的多设备信息在 `meta/session_devices.csv`。

---

## 8. 限制

- 只读副本导出的是「某一时刻」的快照；脚本记录库文件 sha256 与生成时间，便于对账。
- `outside_session_window` 与负 `relative_seconds` 是**提示**，脚本不修改数据。
- 不导出答案类内容、不导出设备 token、不导出 JSON 原始未知列。
- 本脚本不部署为线上接口；若后续需要接口化，按计划 P1-A3 单独批准，并遵守「先模块、后 `main.py`」的部署顺序。
