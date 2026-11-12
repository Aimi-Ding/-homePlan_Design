# 户型图设计器（House Plan Designer Web）

把一张户型图（位图 / PDF / DXF）还原成**可交互的装修设计器网页**：在线查看 2D 平面、3D 漫游，编辑家具与水电，下载单文件 HTML / zip。

本项目是在开源技能 [ZWD11/house-plan-designer](https://github.com/ZWD11/house-plan-designer)（MIT）之上做的一层 Web 产品化封装，包含上传、生成流水线、任务进度、结果页、登录/留资与匿名访客埋点。

---

## 功能

- **多格式输入**：JPG / PNG / WEBP / PDF（矢量或位图）/ DXF；DWG 会提示转存。
- **入口预检**：分辨率、深色底、矢量图元、描边墙风格自动判别，不合格的图直接拦下并给出原因。
- **几何识别**：调用上游脚本得到墙体、墙厚、比例、底图坐标；再做墙体拉直、断段合并、端点吸附等拓扑修复。
- **可选模型补全**：配置视觉模型后，自动补全门窗类型/开向、房间命名、家具、吊顶；带「自查 → 修正」循环。
- **设计器交付**：输出 2.1 MB 单文件 HTML（three.js 已内联，离线可用），支持 2D 布置、3D 漫游、水电点位、报价算量、DXF 导出。
- **任务化生成**：进程内队列 + 阶段进度（SSE 推送），支持失败重试、结果回看。
- **账号与留资**：手机号 + 短信验证码登录（可选）；未登录也能完整使用，下载前可选留手机号。
- **匿名埋点**：visitor_id + 事件表，记录来源、上传、生成、下载、留资，便于做转化分析。

---

## 技术栈

### 前端

| 技术 | 用途 |
|---|---|
| 原生 JavaScript（ES2020） | 单页应用：上传、进度、结果页、登录/留资弹框 |
| 原生 CSS | 设计系统（CSS 变量、响应式、弹框动画） |
| EventSource (SSE) + 轮询兜底 | 生成进度实时更新 |
| iframe | 内嵌生成好的设计器页面 |

### 后端

| 技术 | 用途 |
|---|---|
| Python 3.11+ | 服务端语言 |
| FastAPI | HTTP API、依赖注入、静态资源托管 |
| Uvicorn | ASGI 服务器 |
| SQLAlchemy 2.0 | ORM |
| SQLite（WAL） | 默认数据库，可通过 `DATABASE_URL` 切 PostgreSQL |
| Pydantic | 请求体校验 |
| httpx | 调用视觉模型 / 阿里云短信 |
| ThreadPoolExecutor + SSE | 单机任务队列与进度推送 |

### 图纸识别与校验

| 技术 | 用途 |
|---|---|
| OpenCV（headless） | 墙体掩膜、形态学、连通域、描边墙识别 |
| NumPy | 像素与矩阵运算 |
| Pillow | 图片读写、EXIF 摆正、图片压缩 |
| PyMuPDF | PDF 渲染、矢量图元提取 |
| ezdxf | DXF 解析 |
| pypdf | 导出文件校验 |
| Node.js 18+ / puppeteer-core / Headless Chrome | 打开生成的设计器，跑 `check.mjs` 自查并截图 |

### 生成与设计器

| 技术 | 说明 |
|---|---|
| 上游 `assets/template.html` | 单文件设计器，three.js 已内联（不修改） |
| `build.py` | 把 HouseSpec JSON 注入模板，产出单文件 HTML |
| 视觉大模型（OpenAI 兼容） | 读图补全语义，输出 HouseSpec；可插拔，未配置时退化 |

### 登录 / 短信 / 数据

| 技术 | 用途 |
|---|---|
| 阿里云短信 Dysmsapi | `SendSms`，RPC 签名（HMAC-SHA1，RFC3986） |
| HttpOnly Cookie + 服务端会话表 | 登录态（默认 30 天） |
| SQLite `visitors / events / leads` | 匿名访客、行为埋点、留资手机号 |

---

## 系统架构

```
浏览器（原生 JS）
  │  上传 / 进度(SSE) / 结果页(iframe) / 登录·留资弹框
  ▼
FastAPI
  │  预检 → 创建项目 → 入队 → 状态与事件
  ├── SQLite（projects / job_events / users / leads / events / visitors）
  ├── 对象目录 var/projects/{id}/（原图、designer.html、zip、截图）
  └── 进程内线程池 Worker
        ├─ analyze.py / cadvec.py     ← 纯算法：墙 + 比例
        ├─ repair.py                  ← 墙体拓扑修复
        ├─ 视觉模型（可选）            ← 门窗/房间/家具语义
        ├─ build.py                   ← spec.json → 单文件 HTML
        └─ check.mjs + Headless Chrome ← 自查报告 + 叠图
```

---

## 目录结构

```
backend/
  app/
    main.py                 API、静态页、匿名访客中间件、登录/留资接口
    auth.py                 验证码登录、会话、阿里云短信签名
    analytics.py            visitor / event / lead 记录
    runner.py               任务执行与阶段进度
    jobs.py                 进程内任务队列
    models.py  db.py  config.py
    pipeline/
      probe.py              输入预检与质量分流
      geometry.py           调上游脚本得到 draft.json
      repair.py             墙体拉直 / 合并 / 吸附
      rule_spec.py          无模型时的规则版 HouseSpec
      agent.py              生成编排（模型补全 + 自查修正循环）
      llm.py                模型接入层
      prompts.py            分层 Prompt
      schema.py             HouseSpec 校验与清洗
      render.py  checker.py 构建与自查封装
  requirements.txt
web/                        原生前端（index.html / app.js / styles.css）
vendor/house-plan-designer/ 上游技能（模板 + 识别脚本，commit d4e7374）
var/                        运行时数据（不入库）
.env.example                环境变量模板
```

---

## 快速开始

### 1. 依赖

```bash
# Python
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

# Node（自查脚本用，只装 puppeteer-core，不下载浏览器）
cd vendor/house-plan-designer/scripts && npm install

# 需要本机有 Chrome / Edge / Chromium；没有时：
npx playwright install chromium
```

### 2. 配置

```bash
cp .env.example .env
# 按需填写：短信、模型；本地不填也能跑（console 验证码 + 规则初稿）
```

### 3. 启动

```bash
cd backend
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 。

---

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `SMS_PROVIDER` | `console` | `console` 验证码写日志；`aliyun` 真实发送 |
| `SMS_DEBUG` | console=1 | 1 时验证码随接口返回，**上线必须 0** |
| `ALIYUN_ACCESS_KEY_ID` / `_SECRET` | — | RAM 子账号密钥（仅短信权限） |
| `ALIYUN_SMS_SIGN_NAME` | — | 已审核通过的短信签名 |
| `ALIYUN_SMS_TEMPLATE_CODE` | — | 验证码模板 CODE |
| `ALIYUN_SMS_PARAM_KEY` | `code` | 模板变量名 |
| `LEAD_REQUIRED` | `1` | 下载前是否要求留手机号 |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | — / OpenAI / gpt-4o | 视觉模型 |
| `LLM_MAX_ROUNDS` | `3` | 自查修正轮次上限 |
| `DATABASE_URL` | `sqlite:///var/app.db` | 可切 PostgreSQL |
| `MAX_JOBS` | `2` | 并发任务数 |
| `MAX_UPLOAD_MB` | `30` | 上传大小限制 |

---

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/projects` | 上传图纸并创建任务（支持匿名） |
| GET | `/api/projects` | 方案列表（登录按账号，匿名按 visitor_id） |
| GET | `/api/projects/{id}` | 方案详情、状态、问题清单 |
| GET | `/api/projects/{id}/stream` | SSE 进度流 |
| POST | `/api/projects/{id}/retry` | 重新生成 |
| DELETE | `/api/projects/{id}` | 删除方案与产物 |
| GET | `/p/{id}` | 设计器页面 |
| GET | `/api/projects/{id}/download/html\|zip` | 下载产物 |
| POST | `/api/auth/send-code` / `login` / `logout` | 手机号验证码登录 |
| POST | `/api/leads` | 下载前留手机号（需勾选同意） |

---

## 数据表

| 表 | 内容 |
|---|---|
| `projects` | 方案：状态、spec、报告、产物路径、归属（user_id / visitor_id） |
| `job_events` | 每个生成阶段的进度与日志 |
| `users` / `login_codes` / `auth_sessions` | 账号、验证码、会话 |
| `visitors` | 匿名访客：IP、UA、来源、首访/活跃时间 |
| `events` | 行为埋点：visit / upload / generate_start / generate_done / download / designer_open / lead |
| `leads` | 留资手机号与来源 |

---

## 识别流水线说明

- **算法负责「坐标」**：墙的位置、墙厚、比例、底图对齐，全部来自上游脚本，结果确定、可重放。
- **模型负责「语义」**：门窗类型与开向、房间命名、家具清单与朝向、吊顶。未配置模型时输出「几何初稿」，并在结果页明确标注。
- **自查**：`check.mjs` 打开生成页读 `report.json`，检查悬空墙端、被吞的墙、门窗丢失、尺寸链超差、页面报错；有硬错误时会带报告让模型再修一轮（上限 `LLM_MAX_ROUNDS`）。

---

## 已知限制

- 带尺寸标注的彩色户型图、矢量 PDF / DXF 效果最好；拍照歪斜、扫描件、纯线稿会被预检拦下。
- 无尺寸图会缺少比例，需要用户补一个总宽/总深。
- 全量验收脚本 `qa.mjs`（11–40 分钟）不在线上链路，只用于离线回归。
- 用户在设计器里的编辑目前保存在浏览器 localStorage，尚未回写云端。
- 短信签名除了阿里云审核，还需要运营商报备；报备完成前发送会返回成功但可能不送达。
- 下载留资是前端软门槛（后端只校验 visitor_id 归属），未做一次性下载 token。

---

## 隐私与安全

- `.env`、`var/`、`node_modules/` 均已在 `.gitignore` 中，**不要把 AccessKey、短信密钥、用户手机号提交到仓库**。
- 真实密钥只放 `.env`；仓库里只保留 `.env.example`。
- 建议使用 RAM 子账号 AccessKey，且仅授予短信发送权限；密钥一旦外泄应立即轮换。
- 上线前需补充隐私政策页面，明确手机号、IP、设备信息的收集目的、保留期限与删除方式。

---

## 上游与许可

- 设计器模板、识别脚本来自 [ZWD11/house-plan-designer](https://github.com/ZWD11/house-plan-designer)，遵循 MIT License，版权归原作者所有；`vendor/house-plan-designer/LICENSE` 保留原始许可。
- 本仓库的 Web 封装层同样以 MIT License 发布。
- 对上游 `analyze.py` 的两个小改动：新增 `--outline-max`、`--outline-min-len`，用于识别「两条细线中间留白」的描边墙。
