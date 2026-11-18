# Gemini 4 Carbon (High) 全局模型锁（`carbon-high-lock`）

适用于 Google Antigravity 的全局生命周期钩子与规则插件，将主会话、`/teamwork` 多智能体团队、全部子代理（`self`、`browser`、`DeepInvestigator`、`DeepCoder`、`research`）、`run_workflow` 动态工作流及原生 `swarm` **硬性锁定在 `Gemini 4 Carbon (High)`（`effort: high`）**。

## 解决什么问题？

在 Antigravity 中，即使你在界面和 `AGENTS.md` 里要求全程使用 `Gemini 4 Carbon (High)`，以下场景仍会悄悄跑向 `Hydrogen` 或低档模型：
1. **内置 `/teamwork` 硬编码锁定 Hydrogen**：系统编译内置的 `teamwork` 及 `teamwork_*` 子代理内部写死了 `gemini-4-hydrogen-tiered-high`（Protobuf 枚举 `#1530`），即使显式传入 `Model="inherit"` 也会被忽略，且后台 `schedule` 定时心跳会持续派生新的 Hydrogen 子代理。
2. **`browser` 等子代理默认切 `flash`**：部分内置子代理提示词默认传 `Model="flash"`。
3. **客户端重启后模型下拉框回退**：窗口重启或新建无目录会话时，左下角选择器可能意外回退到 `Carbon (Low)`。

## 五重硬约束机制

1. **`/teamwork` 自动透明改写为 `Carbon (High)` 多代理编排（`PreToolUse` `overwrite`）**
   - 拦截所有 `invoke_subagent(TypeName="teamwork" | "teamwork_*")`，在执行前自动改写为 `TypeName="self"` + `Model="inherit"`，并注入完整的多代理并行调研、方案拆解、无冲突并行编码与独立审查验收协议。
2. **模型推理前 `0-Token` 熔断门禁（`PreInvocation` `terminate`）**
   - 每次调用大模型前校验会话 `modelName`，仅放行 `gemini-4-carbon-high`（`Gemini 4 Carbon (High)`）与 `gemini-pro-orchestrator`（`Orchestrator (Carbon)`）。任何残留 Hydrogen 定时任务或误选 `Low` / `Medium` / `Flash` / `Argon` 的调用在消耗 `0 Token` 时即刻终止，同时将实时在途模型写入 `in_flight/` 供实时监控面板直读。
3. **禁止唤醒历史非 Carbon 旧子代理（`PreToolUse` `send_message` 拦截）**
   - 毫秒级只读检查目标子代理的 `conversations/<id>.db`，若发现其历史运行在 Hydrogen 等非 Carbon 模型，直接拒绝唤醒并引导主控销毁旧代理、重新派生 `Carbon (High)` 子代理。
4. **普通子代理强制继承（`PreToolUse` `invoke_subagent`）**
   - 所有子代理调用强制覆写 `Model="inherit"`，并禁止非 `Carbon (High)` 父会话派生子代理。
5. **`swarm` 与 `run_workflow` 全量锁死**
   - `swarm` 的 `add` / `model` 动作强制固定 `model="gemini-4-carbon-high"`；
   - `run_workflow` 脚本若指定非 `inherit` 模型或 `teamwork*` 类型会被直接拦截。

## 安装位置与审计日志

- **全局插件目录**：`~/.gemini/config/plugins/carbon-high-lock/`（放入即自动启用，无需重启）
- **拦截与改写审计日志**：`~/.gemini/antigravity/plugin_data/carbon-high-lock/audit.jsonl`
- **自定义放行模型（可选环境变量）**：可通过 `CARBON_LOCK_MODELS="gemini-4-carbon-high,gemini-pro-orchestrator"` 自定义允许的模型标识列表。
