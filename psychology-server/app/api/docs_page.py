"""根路径使用说明总览页：GET /。

自包含 HTML（无外部依赖），写清架构、认证、API、业务规则、调用顺序与 curl 示例。
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["docs"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    return HTMLResponse(_HTML)


_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>psychology-server 使用说明</title>
<style>
  :root { --fg:#1f2937; --muted:#6b7280; --line:#e5e7eb; --brand:#2563eb; --bg:#f9fafb; --code:#0f172a; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; color:var(--fg); background:var(--bg); line-height:1.7; }
  .wrap { max-width:980px; margin:0 auto; padding:32px 20px 80px; }
  header h1 { margin:0 0 4px; font-size:28px; }
  header p { margin:0; color:var(--muted); }
  .links { margin:14px 0 6px; }
  .links a { display:inline-block; margin-right:10px; padding:6px 14px; border-radius:6px; background:var(--brand); color:#fff; text-decoration:none; font-size:14px; }
  .links a.alt { background:#fff; color:var(--brand); border:1px solid var(--brand); }
  section { background:#fff; border:1px solid var(--line); border-radius:10px; padding:20px 24px; margin-top:20px; }
  h2 { font-size:19px; margin:0 0 12px; padding-bottom:8px; border-bottom:1px solid var(--line); }
  h3 { font-size:15px; margin:18px 0 6px; }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { background:#f3f4f6; font-weight:600; }
  code { font-family:Consolas,Menlo,monospace; background:#eef2ff; color:#3730a3; padding:1px 6px; border-radius:4px; font-size:13px; }
  pre { background:var(--code); color:#e2e8f0; padding:16px; border-radius:8px; overflow:auto; font-size:13px; line-height:1.55; }
  pre code { background:none; color:inherit; padding:0; }
  .flow { font-family:Consolas,monospace; background:#f1f5f9; border-radius:8px; padding:14px; font-size:13px; white-space:pre; overflow:auto; }
  .pill { display:inline-block; background:#ecfdf5; color:#065f46; border:1px solid #a7f3d0; border-radius:999px; padding:1px 10px; font-size:12px; margin:2px 3px 2px 0; }
  ul { margin:6px 0; padding-left:22px; }
  .muted { color:var(--muted); font-size:13px; }
  .note { background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:10px 14px; font-size:14px; }
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>psychology-server 心理测评云平台</h1>
  <p>多设备云服务器后端 · 第一阶段：设备管理 + 会话管理 + 表情识别</p>
  <div class="links">
    <a href="/docs">交互式 API 文档 /docs</a>
    <a href="/redoc" class="alt">ReDoc 文档 /redoc</a>
    <a href="/api/v1/health" class="alt">健康检查</a>
  </div>
</header>

<section>
  <h2>1. 架构</h2>
  <p>采用 <b>通用 Device 管理 + 通用 Session 管理 + 独立业务 API</b>。每种设备业务使用各自独立的 API、Schema、Service 与数据表，<b>不设万能数据接口</b>。</p>
  <div class="flow">Device  →  Session  →  Emotion API  →  Emotion Service  →  Emotion Result   （当前）

Device  →  Session  →  Heart Rate API  →  Heart Rate Service              （未来新增）
Device  →  Session  →  EEG API        →  EEG Service                     （未来新增）</div>
</section>

<section>
  <h2>2. 设备认证</h2>
  <p>设备先调用注册接口获得 <code>device_token</code>；之后除注册和健康检查外，所有设备接口都必须携带请求头：</p>
  <pre><code>X-Device-Id: atlas_001
X-Device-Token: &lt;注册时返回的 token&gt;</code></pre>
  <p class="muted">token 由服务器生成并存入数据库，不出现在代码中；请设备端妥善保存。</p>
</section>

<section>
  <h2>3. API 列表</h2>
  <table>
    <thead><tr><th>方法</th><th>路径</th><th>说明</th></tr></thead>
    <tbody>
      <tr><td>GET</td><td><code>/api/v1/health</code></td><td>健康检查</td></tr>
      <tr><td>POST</td><td><code>/api/v1/devices/register</code></td><td>注册设备，返回 device_token（重复注册幂等）</td></tr>
      <tr><td>GET</td><td><code>/api/v1/devices</code></td><td>列出所有设备（免认证），不知道 device_id 时先查这里</td></tr>
      <tr><td>GET</td><td><code>/api/v1/devices/{device_id}</code></td><td>查询设备（查询时惰性判断 online/offline）</td></tr>
      <tr><td>GET</td><td><code>/api/v1/devices/{device_id}/active-session</code></td><td>查询设备当前活跃会话（免认证，Atlas 轮询获取 session_id）</td></tr>
      <tr><td>POST</td><td><code>/api/v1/devices/{device_id}/heartbeat</code></td><td>心跳，每 20 秒一次</td></tr>
      <tr><td>POST</td><td><code>/api/v1/devices/{device_id}/connect</code></td><td>手机连接设备（无需设备 token）</td></tr>
      <tr><td>POST</td><td><code>/api/v1/devices/{device_id}/disconnect</code></td><td>手机断开设备</td></tr>
      <tr><td>POST</td><td><code>/api/v1/sessions</code></td><td>创建测评会话（设备必须在线，否则 409）</td></tr>
      <tr><td>GET</td><td><code>/api/v1/sessions/{session_id}</code></td><td>查询会话</td></tr>
      <tr><td>POST</td><td><code>/api/v1/sessions/{session_id}/status</code></td><td>状态流转：running / completed</td></tr>
      <tr><td>POST</td><td><code>/api/v1/emotion/sessions/{session_id}/data</code></td><td>测评结束后批量上传原始 timeline</td></tr>
      <tr><td>GET</td><td><code>/api/v1/emotion/sessions/{session_id}/result</code></td><td>获取服务器计算的最终结果（Flutter 用）</td></tr>
      <tr><td>PUT</td><td><code>/api/v1/emotion/devices/{device_id}/status</code></td><td>上报设备实时表情状态（每约 3 秒，需设备认证）</td></tr>
      <tr><td>GET</td><td><code>/api/v1/emotion/devices/{device_id}/status</code></td><td>查询实时表情状态（Flutter 轮询，无需设备 token）</td></tr>
    </tbody>
  </table>
  <p class="muted">除健康检查外，业务/参数错误统一返回 <code>{"code":..., "message":..., "data":null}</code>；校验失败 code=422。</p>
</section>

<section>
  <h2>4. 表情识别业务规则</h2>
  <h3>上传方式</h3>
  <p>不是实时上传。Atlas 本地完成「摄像头采集 → 人脸检测 → NPU 推理 → 记录 timeline」，<b>测评结束后一次性批量上传</b>。</p>
  <h3>合法表情（8 类，服务器强制校验）</h3>
  <p>
    <span class="pill">neutral</span><span class="pill">happiness</span><span class="pill">surprise</span>
    <span class="pill">sadness</span><span class="pill">anger</span><span class="pill">disgust</span>
    <span class="pill">fear</span><span class="pill">contempt</span>
  </p>
  <h3>结果由服务器计算</h3>
  <ul>
    <li>按相邻 <code>relative_seconds</code> 的差值计算每种表情时长：区间 [t<sub>i</sub>, t<sub>i+1</sub>) 归第 i 条记录。</li>
    <li>计算每种表情秒数与百分比、dominant_expression、average_confidence。</li>
    <li>face_coverage_percent 由 <code>face_detected_frames / video_frames</code> 服务器重算，不直接采用开发板给的值。</li>
  </ul>
  <div class="flow">0.5 neutral
1.0 neutral      →  neutral ≈ 1.0 秒
1.5 happiness
2.0 happiness    →  happiness ≈ 0.5 秒</div>
  <h3>校验与防重复</h3>
  <ul>
    <li>timeline 为空 → 拒绝；confidence 必须在 0~1；expression 必须属于 8 类；session 必须存在。</li>
    <li>已 completed 的会话默认禁止再次上传（返回 409）。</li>
    <li>每次上传携带 <code>client_request_id</code>；<code>(session_id, client_request_id)</code> 有唯一约束，网络重试<b>不会重复插入</b>，服务器幂等返回首次结果。</li>
  </ul>
  <h3>实时表情状态（设备级，可选）</h3>
  <ul>
    <li>Atlas 在采集过程中约每 <b>3 秒</b>通过 <code>PUT /emotion/devices/{id}/status</code> 上报当前人脸/表情。</li>
    <li>同一设备只保留最新一行，不存历史；与心跳<b>相互独立</b>，不影响 online/offline。</li>
    <li>Flutter 每 3~5 秒轮询 GET；返回 <code>stale=true</code> 表示超过 <b>10 秒</b>未收到上报，应提示实时信号中断。</li>
  </ul>
</section>

<section>
  <h2>5. Atlas 调用顺序</h2>
  <pre><code>1. POST /api/v1/devices/register          → 保存 device_token
2. POST /api/v1/devices/{id}/heartbeat    → 每 20s 一次（贯穿全程）
3. POST /api/v1/sessions                  → 拿到 session_id
4. 本地采集 + 推理 + 记录 timeline
5. POST /api/v1/emotion/sessions/{id}/data → 测评结束批量上传（client_request_id 用 UUID）
6. GET  /api/v1/emotion/sessions/{id}/result → Flutter 获取结果</code></pre>
  <h3>Flutter 驱动流程（手机拿不到设备 token，用 connection_id）</h3>
  <pre><code>1. POST /api/v1/devices/{id}/connect        → 手机保存 connection_id
2. POST /api/v1/sessions                    → body 带 connection_id，拿到 session_id
3. Atlas 每 2~3s GET /devices/{id}/active-session → 免认证轮询，拿到 session_id 后开始测评
4. 测评中轮询 GET /emotion/devices/{id}/status  → 实时表情（可选）
5. 测评结束 Atlas 上传，手机 GET /emotion/sessions/{id}/result → 获取最终结果
6. POST /api/v1/devices/{id}/disconnect     → 退出时断开</code></pre>
  <div class="note">关键点：Atlas 无需设备 token 即可通过 active-session 接口查到由手机创建的 session_id；无活跃会话时返回 null。<b>设备离线时无法创建会话（409），需先让设备上线。</b></div>
</section>

<section>
  <h2>6. 完整 curl 示例</h2>
  <pre><code>BASE=http://8.140.240.43:8000/api/v1

# 注册
curl -s -X POST $BASE/devices/register -H 'Content-Type: application/json' -d '{
  "device_id": "atlas_001", "device_name": "心理测评设备01",
  "device_type": "edge_ai_device", "manufacturer": "Huawei",
  "model": "Atlas 200I DK A2", "capabilities": ["emotion"]}'

# 心跳
curl -s -X POST $BASE/devices/atlas_001/heartbeat \\
  -H 'X-Device-Id: atlas_001' -H 'X-Device-Token: &lt;TOKEN&gt;' -d '{}'

# 创建会话
curl -s -X POST $BASE/sessions \\
  -H 'X-Device-Id: atlas_001' -H 'X-Device-Token: &lt;TOKEN&gt;' \\
  -H 'Content-Type: application/json' \\
  -d '{"device_id":"atlas_001","session_type":"emotion"}'

# 上传 timeline
curl -s -X POST $BASE/emotion/sessions/&lt;SESSION_ID&gt;/data \\
  -H 'X-Device-Id: atlas_001' -H 'X-Device-Token: &lt;TOKEN&gt;' \\
  -H 'Content-Type: application/json' -d '{
  "client_request_id": "b6f3e2a0-aaaa-bbbb-cccc-dddddddddddd",
  "session_meta": {"elapsed_seconds":60,"video_frames":1800,
    "face_detected_frames":1500,"display_fps":30,"inference_runs":120},
  "timeline": [
    {"relative_seconds":0.5,"expression":"neutral","confidence":0.82},
    {"relative_seconds":1.0,"expression":"neutral","confidence":0.85},
    {"relative_seconds":1.5,"expression":"happiness","confidence":0.78},
    {"relative_seconds":2.0,"expression":"happiness","confidence":0.80}]}'

# 查询结果
curl -s $BASE/emotion/sessions/&lt;SESSION_ID&gt;/result</code></pre>
  <div class="note">心跳在线规则：heartbeat 正常 → <b>online</b>；超过 30 秒没有心跳 → <b>offline</b>。</div>
</section>

<p class="muted" style="margin-top:24px">psychology-server v0.1.0 · 服务通过 systemd 托管（开机自启 / 崩溃重启）</p>
</div>
</body>
</html>
"""
