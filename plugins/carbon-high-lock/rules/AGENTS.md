# Global Gemini 4 Carbon (High) Mandatory Lock (`carbon-high-lock`)

1. **Exclusive Model Requirement (`Gemini 4 Carbon (High)` / `effort: high`)**:
   - Every main session, subagent, Teamwork multi-agent workflow, dynamic `run_workflow` script, and `swarm` worker MUST run exclusively on **`Gemini 4 Carbon (High)`** (`gemini-4-carbon-high`, `effort: high`).
   - Never invoke, select, or fall back to `Hydrogen`, `Argon`, `Flash`, `Flash Lite`, `Carbon (Low)`, or `Carbon (Medium)`.

2. **Subagent & `/teamwork` Rules (`invoke_subagent`)**:
   - Always pass `Model: "inherit"` on **every** `invoke_subagent` call — including `browser` (override `browser`'s default `"flash"` with `Model: "inherit"`), `self`, `DeepInvestigator`, `DeepCoder`, and `research`.
   - Built-in `teamwork` / `teamwork_*` types are automatically rewritten at runtime by `carbon-high-lock`'s `PreToolUse` hook to `TypeName: "self"` with `Model: "inherit"` so `/teamwork` runs 100% on `Gemini 4 Carbon (High)` rather than `Hydrogen`. When orchestrating multi-agent teamwork directly, prefer `TypeName: "self"` (`Model: "inherit"`), `/boost` (`DeepInvestigator` + `DeepCoder`), or native `swarm`.
   - Never revive legacy `Hydrogen` subagents via `send_message`; kill them via `manage_subagents` and spawn fresh `Carbon (High)` subagents.

3. **Swarm & Workflow Rules (`swarm`, `run_workflow`)**:
   - In `swarm` (`action: "add"` or `action: "model"`), always keep workers on `"gemini-4-carbon-high"` and never switch models away from Carbon (High).
   - In `run_workflow` Python scripts, never pass `model="flash"`, `model="flash_lite"`, `model="pro"`, or `type_name="teamwork*"`; always use `type_name="self"` and `model="inherit"`.
