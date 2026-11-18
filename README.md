# AI 用量与实时模型监控面板（Claude Code · Codex · Antigravity）

零外部依赖的本机 AI 编程助手用量分析与 **2 秒级实时对话 / 底层真实模型监控面板**，同时附带 Google Antigravity 专用的 **`Gemini 4 Carbon (High)` 全局模型强制锁插件（`carbon-high-lock`）**。

所有前端与后端均使用 **Python 3 标准库 + 单页 HTML（D3 v7）** 实现，无需安装任何第三方 `pip` / `npm` 依赖，所有对话内容仅在本机只读解析，不上传任何第三方服务器。

---

## 核心功能

### 1. 2 秒级「实时对话与真实模型监控」流（`GET /live.json`）
- **实时在途状态捕捉**：每 2 秒直读本机 SQLite / WAL 与 JSONL 会话状态，精确展示当前**正在回答**（动态脉冲圆点 `正在回答...` / `正在对话 · 执行工具中...`）、**刚完成**（绿色圆点 + 输出 Token 数）及**被模型锁拦截**（红色圆点 `已被全局锁拦截 (0 tok)`）的会话。
- **底层 Protobuf 模型真值核验**：针对 Google Antigravity，直接解析 `~/.gemini/antigravity/conversations/*.db` 中 `gen_metadata` 的 Protobuf 原始字段（`gm[19]` slug + `gm[21]` 显示名 + `gm[3]` 模型枚举号）并结合 `PreInvocation` 实时钩子：
  - 一眼区分 **`gemini-4-carbon`（`Carbon #1427`）** 与 **`gemini-4-hydrogen-tiered-high`（`Hydrogen #1530`）**；
  - 精确展示推理强度档位（`high` / `max` / `medium` / `low` / `xhigh`）。
- **父子代理穿透定位项目目录**：自动通过 `conversation_summaries.db` 的父子会话链向上回溯工作区 URI，准确标出每一个深层子代理（如 `DeepCoderWorkerL0`、`DeepInvestigatorWorkerL0`、`browser`、`self`）所属的**真实项目文件夹**与会话短 ID。
- **三端同屏**：同时支持 **Google Antigravity（Gemini）**、**OpenAI Codex CLI（ChatGPT）** 与 **Anthropic Claude Code（Claude）**，支持一键切换「全部来源 / 仅当前订阅 / 仅看正在对话」。

### 2. 全维度本地 Token 用量、套餐额度与等值 API 花费看板（`GET /usage.json`）
- **三端套餐额度实时卡片与周消耗曲线**：
  - 自动连接本机正在运行的 Antigravity 本地服务读取 Google AI Ultra / Pro 的 **每周额度** 与 **5 小时窗口额度**；
  - 自动解析 Codex 日志中的速率窗口百分比与 Claude `limits.json` 套餐卡片。
- **多维统计拆解**：
  - 按天堆叠趋势（全部 Tokens / 等值美元花费 / 输出 Tokens / 缓存读取 / 缓存写入 / 未缓存输入 / 响应次数）；
  - 各模型明细表（含推理 Token 占比、缓存命中率、按公开 API 单价折算的等值美元花费）；
  - 7×24 小时活跃热力图（UTC+8）；
  - **真实项目目录排行榜**与**最大的 25 个会话明细**；
  - 工具调用次数 Top 25 排行；
  - 细分维度：入口、电脑名、**主线程 / 子代理**、**推理强度**、**底层模型 ID**。
- **多台电脑无感同步（可选）**：若将本仓库作为你自己的私有 Git 仓库使用，每台电脑每 15 分钟自动将纯统计聚合表写入 `data/usage-<主机名>.json` 并跨设备合并展示（绝不包含任何对话文本）。

### 3. Antigravity 全局模型强制锁插件（`plugins/carbon-high-lock`）
针对 Antigravity 中「明明在界面和规则里选了 `Gemini 4 Carbon (High)`，但后台 `/teamwork` 子代理却一直跑在 `Gemini 4 Hydrogen (High)`（`#1530`）」的底层硬编码问题，提供五重生命周期钩子硬约束：

1. **`/teamwork` 透明改写为纯 `Carbon (High)` 多代理编排（`PreToolUse` `overwrite`）**：
   - 经实测，Antigravity 内置编译的 `teamwork` / `teamwork_*` 子代理内部写死了 `gemini-4-hydrogen-tiered-high`（`#1530`），无视 `Model="inherit"` 参数。
   - 本插件在 `invoke_subagent` 执行前自动将 `teamwork` 及 `teamwork_*` 覆写为 `TypeName="self"` + `Model="inherit"`，并自动注入多代理 Teamwork 编排协议（并行探索 → 竞品与架构方案 → 无冲突并行编码 → 独立审查验收），让 `/teamwork` 100% 运行在 `Carbon (High)` 上。
2. **大模型推理前 `0-Token` 熔断门禁（`PreInvocation` `terminate`）**：
   - 每次调用模型前检查 `modelName`，默认仅放行 `gemini-4-carbon-high` 与 `gemini-pro-orchestrator`（`Orchestrator (Carbon)`）。
   - 后台残留的 Hydrogen 定时心跳任务、或客户端重启后意外回退到 `Carbon (Low)` / `Flash` 的会话，均会在消耗 `0 Token` 时立即拦截终止。
3. **禁止唤醒历史 Hydrogen 旧子代理（`PreToolUse` `send_message`）**：
   - 毫秒级只读检查目标子代理数据库，拒绝通过 `send_message` 唤醒曾运行在非 Carbon 模型上的旧代理，引导主控销毁旧代理并派生全新 `Carbon (High)` 子代理。
4. **全量子代理强制继承（含 `browser`）**：
   - 自动将所有 `invoke_subagent`（包括默认传 `Model="flash"` 的 `browser` 子代理）强制覆写为 `Model="inherit"`。
5. **`swarm` 与 `run_workflow` 全量锁死**：
   - `swarm` 的 `add` / `model` 动作强制锁定 `model="gemini-4-carbon-high"`；拦截 `run_workflow` 中任何非 `inherit` 模型或 `teamwork*` 调用。

---

## 目录结构

```text
ai-usage-monitor/
├── dashboard.html                 # 单页实时监控与用量可视化前端（D3 v7 + 亮暗自适应）
├── serve.py                       # 本地常驻 HTTP 服务（提供 2s 实时 /live.json 与 60s 聚合刷新）
├── live_feed.py                   # 2 秒级在途对话状态与 SQLite Protobuf / JSONL 真实模型采集器
├── agg.py                         # Claude Code / Codex / Antigravity 全量日志聚合与跨设备合并脚本
├── plugins/
│   └── carbon-high-lock/          # Antigravity 全局 Carbon (High) 强制锁插件
│       ├── plugin.json            # 插件清单
│       ├── hooks.json             # PreInvocation 与 PreToolUse 生命周期钩子配置
│       ├── hooks/
│       │   └── enforce_carbon_high.py  # 五重模型拦截、Teamwork 透明改写与在途状态上报脚本
│       ├── rules/
│       │   └── AGENTS.md          # 全局常驻模型与编排策略规则
│       └── README.md              # 插件独立说明文档
└── scripts/
    ├── install.ps1                # Windows 一键部署脚本（安装插件 + 启动面板 + 开机自启）
    └── install.sh                 # macOS / Linux 一键部署脚本
```

---

## 快速开始

### 环境要求
- **Python 3.10+**（仅使用标准库：`sqlite3`、`http.server`、`json`、`urllib` 等，无需 `pip install`）
- 本机安装并运行过 Claude Code（`~/.claude`）、Codex（`~/.codex`）或 Google Antigravity（`~/.gemini/antigravity`）中的任意一种或多种。

### 方式一：Windows 一键安装与启动（推荐）

在 PowerShell 中运行：

```powershell
git clone https://github.com/ypyik0669/ai-usage-monitor.git "$HOME\usage-dashboard"
powershell -ExecutionPolicy Bypass -File "$HOME\usage-dashboard\scripts\install.ps1"
```

脚本会自动完成三件事：
1. 将 `carbon-high-lock` 全局模型锁部署到 `~/.gemini/config/plugins/carbon-high-lock`（立即对所有 Antigravity 会话生效，无需重启）；
2. 运行一次 `agg.py --no-git` 生成初始统计文件；
3. 在后台无窗口启动 `serve.py` 并配置登录自启，随后直接在浏览器打开 **`http://127.0.0.1:8797/`** 即可查看。

> 如果只想启动监控面板、不安装 `carbon-high-lock` 模型锁插件，可加上 `-SkipPlugin` 参数：
> ```powershell
> powershell -ExecutionPolicy Bypass -File "$HOME\usage-dashboard\scripts\install.ps1" -SkipPlugin
> ```

### 方式二：macOS / Linux 一键安装与启动

```bash
git clone https://github.com/ypyik0669/ai-usage-monitor.git ~/usage-dashboard
bash ~/usage-dashboard/scripts/install.sh
```

### 方式三：手动运行

```bash
# 1. 手动生成一次聚合统计（跳过 Git 同步）
python -X utf8 agg.py --no-git

# 2. 启动本地实时监控面板（默认监听 127.0.0.1:8797）
python -X utf8 serve.py
```

启动后访问：`http://127.0.0.1:8797/`

---

## 环境变量与自定义配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `USAGE_DASHBOARD_PORT` | `8797` | `serve.py` 监听的本地端口号 |
| `USAGE_SHOW_FULL_EMAIL` | `0`（默认自动脱敏） | 实时流默认将账号邮箱脱敏显示为 `ab***yz@gmail.com`（方便直接截图分享）；设为 `1` 可显示完整邮箱 |
| `CARBON_LOCK_MODELS` | `gemini-4-carbon-high,gemini-pro-orchestrator` | `carbon-high-lock` 插件允许放行的底层 `modelName` 列表（逗号分隔） |

---

## 多台电脑私有库自动同步（可选）

如果你有多台电脑（例如台式机 + 笔记本），希望在同一个面板里合并查看所有电脑的累计用量：

1. Fork 或创建一个你自己的 **Private（私有）** GitHub 仓库；
2. 在每台电脑克隆你的私有仓库，并在 `.gitignore` 中移除 `data/*.json` 一行；
3. `serve.py` 每 15 分钟会自动运行一次不带 `--no-git` 的 `agg.py`，将当前电脑的纯数字聚合表提交到 `data/usage-<主机名>.json` 并自动拉取合并其他电脑的数据。

---

## 隐私与安全说明

- **纯本地只读采集**：所有 SQLite 数据库均以 `?mode=ro` 只读模式打开，不会修改 Claude Code、Codex 或 Antigravity 的任何历史记录文件。
- **默认脱敏与 `.gitignore` 保护**：本地生成的 `usage.json`、`antigravity_quota.json`、`limits.json`、`serve.log` 及 `data/*.json` 默认均已加入 `.gitignore`，避免在公开仓库中误提交个人统计日志；实时流账号标识默认自动打码脱敏。

---

## 开源协议

本项目基于 [MIT License](./LICENSE) 开源。
