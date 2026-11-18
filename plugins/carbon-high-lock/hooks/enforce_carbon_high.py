import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time

HOME = os.path.expanduser("~")
CONVS_DIR = os.path.join(HOME, ".gemini", "antigravity", "conversations")
PLUGIN_DATA_DIR = os.path.join(
    HOME, ".gemini", "antigravity", "plugin_data", "carbon-high-lock"
)
AUDIT_LOG = os.path.join(PLUGIN_DATA_DIR, "audit.jsonl")
IN_FLIGHT_DIR = os.path.join(PLUGIN_DATA_DIR, "in_flight")

# Strictly permitted Carbon (High) runtime model slugs in Antigravity (overridable via CARBON_LOCK_MODELS)
_DEFAULT_MODELS = {"gemini-4-carbon-high", "gemini-pro-orchestrator"}
_ENV_MODELS = {
    m.strip()
    for m in os.environ.get("CARBON_LOCK_MODELS", "").split(",")
    if m.strip()
}
ALLOWED_CARBON_HIGH_MODELS = _ENV_MODELS or _DEFAULT_MODELS


def log_audit(entry):
    try:
        os.makedirs(PLUGIN_DATA_DIR, exist_ok=True)
        entry["ts"] = dt.datetime.now(dt.timezone.utc).isoformat()
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def record_in_flight(payload, model_name):
    conv_id = str(payload.get("conversationId") or "").strip()
    if not conv_id or not re.match(r"^[0-9a-fA-F-]{36}$", conv_id):
        return
    try:
        os.makedirs(IN_FLIGHT_DIR, exist_ok=True)
        path = os.path.join(IN_FLIGHT_DIR, f"{conv_id}.json")
        tmp = f"{path}.tmp"
        data = {
            "conv": conv_id,
            "modelName": model_name,
            "cwd": payload.get("cwd", ""),
            "workspacePaths": payload.get("workspacePaths") or [],
            "initialNumSteps": payload.get("initialNumSteps", 0),
            "invocationNum": payload.get("invocationNum", 0),
            "ts": time.time(),
        }
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def _rv(b, i):
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if x < 0x80:
            return r, i


def _pb(b):
    out = {}
    i = 0
    try:
        while i < len(b):
            k, i = _rv(b, i)
            f, ty = k >> 3, k & 7
            if ty == 0:
                v, i = _rv(b, i)
            elif ty == 2:
                n, i = _rv(b, i)
                v = b[i : i + n]
                i += n
            elif ty == 1:
                v = None
                i += 8
            elif ty == 5:
                v = None
                i += 4
            else:
                return None
            out.setdefault(f, []).append(v)
    except Exception:
        return None
    return out


def _sub(d, *path):
    for f in path:
        if not d or f not in d:
            return None
        v = d[f][0]
        d = _pb(v) if isinstance(v, (bytes, bytearray)) else v
    return d


def _str(d, f):
    v = (d or {}).get(f, [b""])[0]
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else ""


def detect_session_non_carbon_model(conv_id):
    """Return non-Carbon-High model label if conv_id previously ran on Hydrogen/Flash/Low, else None."""
    if not conv_id or not re.match(r"^[0-9a-fA-F-]{36}$", conv_id):
        return None
    db_path = os.path.join(CONVS_DIR, f"{conv_id}.db")
    if not os.path.exists(db_path):
        return None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        rows = con.execute("SELECT data FROM gen_metadata LIMIT 10").fetchall()
        con.close()
        for (g,) in rows:
            gm = _sub(_pb(g), 1) or {}
            m19 = _str(gm, 19).strip().lower()
            m21 = _str(gm, 21).strip()
            if m19 and m19 not in ALLOWED_CARBON_HIGH_MODELS and m19 != "gemini-4-carbon":
                return m21 or m19
            if m21 and "Carbon (High)" not in m21 and "Orchestrator (Carbon)" not in m21:
                return m21
    except Exception:
        return None
    return None


def build_carbon_teamwork_prompt(original_prompt, parent_cid, original_type):
    if original_type == "teamwork":
        return (
            f"[CARBON-HIGH TEAMWORK COORDINATOR | Parent Conversation ID: {parent_cid}]\n"
            "You have been launched to execute a `/teamwork` multi-agent request strictly on "
            "**Gemini 4 Carbon (High)** (replacing the legacy Hydrogen-pinned built-in `teamwork` agent).\n\n"
            "Mandatory Coordination Rules:\n"
            "1. **Model Lock**: Every subagent you spawn MUST use `invoke_subagent` with `TypeName: \"self\"` and `Model: \"inherit\"` (never `teamwork*`, never `flash`/`flash_lite`/`pro`).\n"
            "2. **Multi-Agent Workflow** (unless the task below explicitly instructs a lightweight/single-turn probe):\n"
            "   - **Step 0 (Alignment)**: If `[skip-interview]` is present or the prompt asks for defaults / no questions, proceed directly to execution. Otherwise, if critical requirements need user confirmation, send one concise question summary to your parent (`"
            + parent_cid
            + "`) via `send_message` and wait.\n"
            "   - **Phase 1 (Parallel Survey)**: Spawn parallel read-only Explorer subagents (`TypeName: \"self\"`, `Model: \"inherit\"`) to analyze the codebase, architecture/tests, and competitor benchmarks.\n"
            "   - **Phase 2 (Plan & Partition)**: Synthesize an evidence-backed plan and partition implementation into strictly disjoint file scopes.\n"
            "   - **Phase 3 (Parallel Implementation)**: Spawn parallel Worker subagents (`TypeName: \"self\"`, `Model: \"inherit\"`) to implement all planned workstreams without file collisions.\n"
            "   - **Phase 4 (Independent Verification)**: Spawn independent Reviewer / Challenger subagents (`TypeName: \"self\"`, `Model: \"inherit\"`) to run build, lint, typecheck, and automated test suites and verify all acceptance criteria before finishing.\n"
            f"3. **Parent Reporting**: Send concise milestone updates and your final completion report to `{parent_cid}` using `send_message`.\n\n"
            "=== ORIGINAL TEAMWORK TASK ===\n"
            f"{original_prompt}"
        )

    readonly_hint = ""
    if any(k in original_type for k in ("explorer", "reviewer", "challenger", "auditor", "critic", "spec_miner")):
        readonly_hint = (
            "Maintain strict read-only discipline on project source files (only write reports/handoffs in your assigned state/scratch folder if requested).\n"
        )
    return (
        f"[Carbon-High Teamwork Role: `{original_type}` | Parent Conversation ID: {parent_cid}]\n"
        "Run strictly on **Gemini 4 Carbon (High)**. If you spawn any child subagents, always use `TypeName: \"self\"` and `Model: \"inherit\"`.\n"
        f"{readonly_hint}\n"
        f"{original_prompt}"
    )


def handle_pre_invocation(payload):
    model_name = str(payload.get("modelName") or "").strip()
    conv_id = str(payload.get("conversationId") or "").strip()
    if model_name in ALLOWED_CARBON_HIGH_MODELS or not model_name:
        if model_name:
            record_in_flight(payload, model_name)
        return {}

    log_audit({
        "event": "PreInvocation",
        "action": "terminate_non_carbon_high",
        "conv": conv_id,
        "model": model_name,
        "cwd": payload.get("cwd", ""),
    })
    return {
        "injectSteps": [
            {
                "errorMessage": {
                    "userMessage": (
                        f"⛔ [Carbon High 全局锁] 已拦截非 Carbon (High) 模型调用（当前检测到: `{model_name}`，本次 0 Token 消耗）。"
                        " 请在输入框左下角模型选择器切换为 `Gemini 4 Carbon (High)`；若是旧的 Hydrogen 后台子代理已被自动终止。"
                    )
                }
            }
        ],
        "terminationBehavior": "terminate",
    }


def handle_pre_tool_use(payload):
    tc = payload.get("toolCall") or {}
    name = str(tc.get("name") or "").strip()
    args = tc.get("args") or {}
    parent_cid = str(payload.get("conversationId") or "").strip()
    caller_model = str(payload.get("modelName") or "").strip()

    if name == "invoke_subagent":
        if caller_model and caller_model not in ALLOWED_CARBON_HIGH_MODELS:
            log_audit({
                "event": "PreToolUse",
                "tool": name,
                "action": "deny_subagent_from_non_carbon_high_caller",
                "conv": parent_cid,
                "callerModel": caller_model,
            })
            return {
                "decision": "deny",
                "reason": (
                    f"[carbon-high-lock] Current session model is `{caller_model}` (not Gemini 4 Carbon High). "
                    "Switch the session to `Gemini 4 Carbon (High)` before spawning subagents."
                ),
            }
        tn = str(args.get("TypeName") or "").strip()
        md = str(args.get("Model") or "").strip()
        overwrite = {}

        if tn == "teamwork" or tn.startswith("teamwork_"):
            overwrite["TypeName"] = "self"
            overwrite["Model"] = "inherit"
            overwrite["Prompt"] = build_carbon_teamwork_prompt(
                str(args.get("Prompt") or ""), parent_cid, tn
            )
            log_audit({
                "event": "PreToolUse",
                "tool": name,
                "action": "rewrite_teamwork_to_carbon_self",
                "conv": parent_cid,
                "fromType": tn,
                "fromModel": md,
            })
            return {"decision": "allow", "overwrite": overwrite}

        if md != "inherit":
            overwrite["Model"] = "inherit"
            log_audit({
                "event": "PreToolUse",
                "tool": name,
                "action": "force_model_inherit",
                "conv": parent_cid,
                "typeName": tn,
                "fromModel": md,
            })
            return {"decision": "allow", "overwrite": overwrite}

        return {"decision": "allow"}

    if name == "swarm":
        act = str(args.get("action") or "").strip()
        m = str(args.get("model") or "").strip()
        if act in ("add", "model") and m != "gemini-4-carbon-high":
            log_audit({
                "event": "PreToolUse",
                "tool": name,
                "action": "force_swarm_carbon_high",
                "conv": parent_cid,
                "swarmAction": act,
                "fromModel": m,
            })
            return {"decision": "allow", "overwrite": {"model": "gemini-4-carbon-high"}}
        return {"decision": "allow"}

    if name == "run_workflow":
        script_src = str(args.get("Script") or "")
        sp = str(args.get("ScriptPath") or "").strip()
        if sp and os.path.exists(sp):
            try:
                with open(sp, encoding="utf-8", errors="replace") as fh:
                    script_src += "\n" + fh.read()
            except Exception:
                pass
        bad_model = re.search(r"""model\s*=\s*['"](?!inherit['"])([^'"]+)['"]""", script_src)
        bad_type = re.search(r"""type_name\s*=\s*['"](teamwork[^'"]*)['"]""", script_src)
        if bad_model or bad_type:
            detail = f"model={bad_model.group(1)!r}" if bad_model else f"type_name={bad_type.group(1)!r}"
            log_audit({
                "event": "PreToolUse",
                "tool": name,
                "action": "deny_non_carbon_workflow",
                "conv": parent_cid,
                "detail": detail,
            })
            return {
                "decision": "deny",
                "reason": (
                    f"[carbon-high-lock] Blocked workflow containing `{detail}`. "
                    "All workflow agents must use `model='inherit'` (or omit `model`) and `type_name='self'` so everything runs on Gemini 4 Carbon (High)."
                ),
            }
        return {"decision": "allow"}

    if name == "send_message":
        recipient = str(args.get("Recipient") or "").strip()
        if recipient:
            non_carbon = detect_session_non_carbon_model(recipient)
            if non_carbon:
                log_audit({
                    "event": "PreToolUse",
                    "tool": name,
                    "action": "deny_revive_legacy_non_carbon_subagent",
                    "conv": parent_cid,
                    "recipient": recipient,
                    "recipientModel": non_carbon,
                })
                return {
                    "decision": "deny",
                    "reason": (
                        f"[carbon-high-lock] Target subagent `{recipient}` previously ran on `{non_carbon}` (not Gemini 4 Carbon High). "
                        f"Do NOT message or revive `{recipient}`. Call `manage_subagents(Action='kill', ConversationIds=['{recipient}'])` and spawn a fresh Carbon (High) subagent with `invoke_subagent(TypeName='self', Model='inherit')` instead."
                    ),
                }
        return {"decision": "allow"}

    return {"decision": "allow"}


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    ev = payload.get("hookEventName", "")
    if ev == "PreInvocation":
        out = handle_pre_invocation(payload)
    elif ev == "PreToolUse":
        out = handle_pre_tool_use(payload)
    else:
        out = {}

    sys.stdout.write(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
