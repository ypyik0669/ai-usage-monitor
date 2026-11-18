"""Real-time active conversation & ground-truth model collector for Claude Code, Codex, and Antigravity."""
import datetime as dt
import glob
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse

HOME = os.path.expanduser("~")
TZ = dt.timezone(dt.timedelta(hours=8))

AGY_DIR = os.path.join(HOME, ".gemini", "antigravity")
CONVS_DIR = os.path.join(AGY_DIR, "conversations")
CS_DB = os.path.join(AGY_DIR, "conversation_summaries.db")
PLUGIN_DATA = os.path.join(AGY_DIR, "plugin_data", "carbon-high-lock")
IN_FLIGHT_DIR = os.path.join(PLUGIN_DATA, "in_flight")
AUDIT_LOG = os.path.join(PLUGIN_DATA, "audit.jsonl")

_LOCK = threading.Lock()
_CACHE = {"ts": 0.0, "data": None}
_DB_CACHE = {}     # sid -> (sig, parsed_dict)
_JSONL_CACHE = {}  # filepath -> (sig, parsed_list)


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


def _base_dir(p):
    if not p:
        return ""
    p = str(p).strip()
    if p.startswith("["):
        try:
            arr = json.loads(p)
            p = arr[0] if arr else ""
        except Exception:
            pass
    if p.startswith("file:///"):
        p = urllib.parse.unquote(p[8:])
    p = p.rstrip("\\/")
    return (p.replace("\\", "/").split("/")[-1] or p)[:48]


def _parse_iso_ts(s):
    if not s:
        return 0.0
    try:
        s2 = re.sub(r"(\.\d{6})\d+", r"\1", str(s).strip().replace("Z", "+00:00"))
        if " " in s2 and "T" not in s2:
            s2 = s2.replace(" ", "T", 1)
        return dt.datetime.fromisoformat(s2).timestamp()
    except Exception:
        return 0.0


def _fmt_hms(epoch):
    d = dt.datetime.fromtimestamp(epoch, TZ)
    return d.strftime("%H:%M:%S"), d.strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d %H:%M:%S")


def _extract_effort(display_model, raw_model="", explicit_effort=""):
    if explicit_effort and explicit_effort not in ("未记录", "unknown"):
        return explicit_effort.lower()
    m = re.search(r"\((XHigh|High|Medium|Low|Max)\)", display_model or "", re.I)
    if m:
        return m.group(1).lower()
    m2 = re.search(r"-(xhigh|high|medium|low|max)(?:$|-)", raw_model or "", re.I)
    if m2:
        return m2.group(1).lower()
    if "orchestrator" in (raw_model or "").lower() or "Orchestrator" in (display_model or ""):
        return "high"
    return "default"


def _normalize_agy_models(model_enum, raw_slug, display_model, hook_model=""):
    raw = (raw_slug or "").strip()
    disp = (display_model or "").strip()
    hk = (hook_model or "").strip()

    if not raw:
        if hk:
            raw = hk
        elif model_enum == 1427 or "Carbon" in disp:
            raw = "gemini-4-carbon"
        elif model_enum == 1530 or "Hydrogen" in disp:
            raw = "gemini-4-hydrogen-tiered-high"
        elif "Argon" in disp:
            raw = "gemini-4-argon"
        elif disp:
            raw = re.sub(r"\s*\(.*\)$", "", disp).strip().lower().replace(" ", "-")

    if not disp:
        if "carbon-high" in (hk or raw):
            disp = "Gemini 4 Carbon (High)"
        elif "orchestrator" in (hk or raw):
            disp = "Orchestrator (Carbon)"
        elif "hydrogen" in (hk or raw):
            disp = "Gemini 4 Hydrogen (High)"
        else:
            disp = raw or "?"

    fam = re.sub(r"-(tiered-)?(xhigh|high|medium|low)$", "", (raw or "").lower())
    if fam == "gemini-pro-orchestrator":
        fam = "gemini-4-carbon"
    effort = _extract_effort(disp, hk or raw)
    return raw or "?", disp, fam or raw or "?", effort


def _mask_email(s):
    if not s or "@" not in s or os.environ.get("USAGE_SHOW_FULL_EMAIL") == "1":
        return s
    local, dom = s.split("@", 1)
    if len(local) <= 3:
        return local[:1] + "***@" + dom
    return local[:2] + "***" + local[-2:] + "@" + dom


def _google_account():
    p = os.path.join(HOME, ".gemini", "google_accounts.json")
    try:
        if os.path.exists(p):
            d = json.load(open(p, encoding="utf-8"))
            if isinstance(d, dict) and d.get("active"):
                return _mask_email(str(d["active"]))
    except Exception:
        pass
    return "Antigravity"


def _file_sig(path):
    try:
        st = os.stat(path)
        wal = path + "-wal"
        wst = os.stat(wal) if os.path.exists(wal) else None
        return (st.st_mtime_ns, st.st_size, wst.st_mtime_ns if wst else 0, wst.st_size if wst else 0)
    except Exception:
        return None


def _read_in_flight(sid):
    p = os.path.join(IN_FLIGHT_DIR, f"{sid}.json")
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def _inspect_agy_conv_db(sid):
    dbf = os.path.join(CONVS_DIR, f"{sid}.db")
    sig = _file_sig(dbf)
    if not sig:
        return None
    cached = _DB_CACHE.get(sid)
    if cached and cached[0] == sig:
        return cached[1]

    info = {"responses": [], "top_step": None}
    try:
        con = sqlite3.connect(f"file:{dbf}?mode=ro", uri=True, timeout=1.0)
        when = {}
        latest_step_ts = 0.0
        top_step = None
        for idx, st_type, status, m in con.execute(
            "SELECT idx, step_type, status, metadata FROM steps ORDER BY idx DESC LIMIT 40"
        ):
            if top_step is None:
                top_step = (idx, st_type, status)
            if m:
                md = _pb(m)
                u = _sub(md, 9)
                t1 = _sub(md, 1, 1)
                if t1:
                    latest_step_ts = max(latest_step_ts, float(t1))
                if u and t1:
                    key = _str(_sub(u, 7), 12)
                    if key:
                        when[key] = float(t1)
                    when[(u.get(2, [0])[0], u.get(3, [0])[0])] = float(t1)

        resps = []
        for idx, g in con.execute("SELECT idx, data FROM gen_metadata ORDER BY idx DESC LIMIT 4"):
            gm = _sub(_pb(g), 1) or {}
            u = _sub(gm, 4)
            if not u:
                continue
            key = _str(_sub(u, 7), 12)
            inp = u.get(2, [0])[0]
            out = u.get(3, [0])[0]
            cr = u.get(5, [0])[0]
            think = u.get(9, [0])[0]
            t1 = when.get(key) or when.get((inp, out)) or latest_step_ts
            resps.append({
                "idx": idx,
                "ts": float(t1) if t1 else 0.0,
                "enum": (gm.get(3) or [None])[0],
                "m19": _str(gm, 19),
                "m21": _str(gm, 21),
                "input": inp,
                "output": out,
                "cache_read": cr,
                "reasoning": think,
            })
        con.close()
        info["responses"] = resps
        info["top_step"] = top_step
        info["latest_step_ts"] = latest_step_ts
        _DB_CACHE[sid] = (sig, info)
        return info
    except Exception:
        return None


def _collect_antigravity(now):
    items = []
    if not os.path.exists(CS_DB):
        return items
    acct = _google_account()
    try:
        con = sqlite3.connect(f"file:{CS_DB}?mode=ro", uri=True, timeout=1.5)
        all_meta = {}
        for r in con.execute(
            """SELECT conversation_id, title, workspace_uris, status, agent_name,
                      parent_conversation_id, nesting_depth, last_modified_time, killed
               FROM conversation_summaries"""
        ):
            all_meta[r[0]] = {
                "title": r[1] or "",
                "ws": r[2] or "",
                "status": r[3] or "",
                "agent": r[4] or "",
                "parent": r[5] or "",
                "depth": int(r[6] or 0),
                "mtime": _parse_iso_ts(r[7]),
                "killed": bool(r[8]),
            }
        con.close()
    except Exception:
        return items

    def resolve_lineage(sid):
        cur = sid
        seen = set()
        proj = ""
        title = ""
        while cur and cur in all_meta and cur not in seen and len(seen) < 10:
            seen.add(cur)
            m = all_meta[cur]
            if not proj and m["ws"]:
                proj = _base_dir(m["ws"])
            if not title and m["title"]:
                title = m["title"]
            cur = m["parent"]
        own = all_meta.get(sid, {})
        depth = own.get("depth", 0)
        agent = own.get("agent", "")
        if depth == 0 and not agent:
            role = "主会话"
        elif agent:
            role = f"子代理 · {agent}"
        else:
            role = f"L{depth} 子代理"
        return proj or "未绑定目录", title, role

    # Pick all running conversations + top 30 most recent
    candidates = sorted(
        [
            (sid, m)
            for sid, m in all_meta.items()
            if not m["killed"] and m["mtime"] > 0
        ],
        key=lambda x: (1 if x[1]["status"] == "CASCADE_RUN_STATUS_RUNNING" and (now - x[1]["mtime"] < 1200) else 0, x[1]["mtime"]),
        reverse=True,
    )[:36]

    for sid, m in candidates:
        proj, title, role = resolve_lineage(sid)
        inflight = _read_in_flight(sid)
        hook_model = (inflight or {}).get("modelName", "")
        hook_ts = float((inflight or {}).get("ts") or 0.0)
        if proj == "未绑定目录" and (inflight or {}).get("cwd"):
            proj = _base_dir(inflight["cwd"]) or proj

        db_info = _inspect_agy_conv_db(sid)
        resps = (db_info or {}).get("responses") or []
        top_step = (db_info or {}).get("top_step")
        is_running = m["status"] == "CASCADE_RUN_STATUS_RUNNING" and (now - m["mtime"] < 1200)

        if is_running:
            top_r = resps[0] if resps else {}
            raw_m, disp_m, fam_m, effort = _normalize_agy_models(
                top_r.get("enum"), top_r.get("m19"), top_r.get("m21"), hook_model
            )
            if hook_model:
                raw_m = hook_model
            ev_ts = max(m["mtime"], hook_ts, top_r.get("ts") or 0.0)
            hms, ymd, full_dt = _fmt_hms(ev_ts)
            if top_step and top_step[1] != 15 and top_step[2] == 2:
                st_txt = "正在对话 · 执行工具中..."
            else:
                st_txt = "正在回答..."
            items.append({
                "id": f"agy-live-{sid}",
                "provider": "gemini",
                "ts": ev_ts,
                "time": hms,
                "date": ymd,
                "full_time": full_dt,
                "group": f"group/{fam_m}",
                "model_family": fam_m,
                "raw_model": raw_m,
                "display_model": disp_m,
                "model_enum": top_r.get("enum") or (1427 if "carbon" in raw_m else None),
                "effort": effort,
                "is_active": True,
                "state": "answering",
                "state_text": st_txt,
                "account": acct,
                "project": proj,
                "session": sid[:8],
                "full_session": sid,
                "role": role,
                "title": title[:42],
                "output_tokens": top_r.get("output") or 0,
                "total_tokens": (top_r.get("input") or 0) + (top_r.get("output") or 0) + (top_r.get("cache_read") or 0),
            })

        for r in resps[: (1 if is_running else 2)]:
            if not r["ts"]:
                continue
            # If currently running and this response is within 1s of live row, skip duplicate timestamp clutter
            if is_running and abs(r["ts"] - m["mtime"]) < 1.5 and len(resps) == 1:
                continue
            raw_m, disp_m, fam_m, effort = _normalize_agy_models(
                r.get("enum"), r.get("m19"), r.get("m21"), ""
            )
            hms, ymd, full_dt = _fmt_hms(r["ts"])
            items.append({
                "id": f"agy-{sid[:8]}-{r['idx']}",
                "provider": "gemini",
                "ts": r["ts"],
                "time": hms,
                "date": ymd,
                "full_time": full_dt,
                "group": f"group/{fam_m}",
                "model_family": fam_m,
                "raw_model": raw_m,
                "display_model": disp_m,
                "model_enum": r.get("enum"),
                "effort": effort,
                "is_active": False,
                "state": "done",
                "state_text": "",
                "account": acct,
                "project": proj,
                "session": sid[:8],
                "full_session": sid,
                "role": role,
                "title": title[:42],
                "output_tokens": r.get("output") or 0,
                "total_tokens": (r.get("input") or 0) + (r.get("output") or 0) + (r.get("cache_read") or 0),
            })

    # Include recent blocked attempts from carbon-high-lock audit.jsonl (last 15 min) so user can verify enforcement
    if os.path.exists(AUDIT_LOG):
        try:
            with open(AUDIT_LOG, encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-20:]
            seen_blk = set()
            for ln in reversed(tail):
                e = json.loads(ln)
                if e.get("action") != "terminate_non_carbon_high":
                    continue
                bts = _parse_iso_ts(e.get("ts"))
                cid = e.get("conv", "")
                if not bts or (now - bts > 900) or cid in seen_blk:
                    continue
                seen_blk.add(cid)
                proj, title, role = resolve_lineage(cid) if cid in all_meta else (_base_dir(e.get("cwd")) or "后台任务", "", "旧子代理")
                bm = e.get("model", "unknown")
                hms, ymd, full_dt = _fmt_hms(bts)
                items.append({
                    "id": f"blk-{cid[:8]}-{int(bts)}",
                    "provider": "gemini",
                    "ts": bts,
                    "time": hms,
                    "date": ymd,
                    "full_time": full_dt,
                    "group": f"group/{re.sub(r'-(tiered-)?(xhigh|high|medium|low)$', '', bm)}",
                    "raw_model": bm,
                    "display_model": bm,
                    "model_enum": 1530 if "hydrogen" in bm else None,
                    "effort": _extract_effort("", bm),
                    "is_active": False,
                    "state": "blocked",
                    "state_text": "已被全局锁拦截 (0 tok)",
                    "account": acct,
                    "project": proj,
                    "session": cid[:8],
                    "full_session": cid,
                    "role": role,
                    "title": title[:42],
                    "output_tokens": 0,
                    "total_tokens": 0,
                })
        except Exception:
            pass

    return items


def _tail_lines(path, max_bytes=262144):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            sz = fh.tell()
            fh.seek(max(0, sz - max_bytes))
            chunk = fh.read().decode("utf-8", "ignore")
        lines = chunk.splitlines()
        return lines[1:] if sz > max_bytes and len(lines) > 1 else lines
    except Exception:
        return []


def _collect_codex(now):
    items = []
    files = sorted(
        glob.glob(os.path.join(HOME, ".codex", "sessions", "**", "*.jsonl"), recursive=True),
        key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
        reverse=True,
    )[:10]
    for f in files:
        sig = _file_sig(f)
        if not sig:
            continue
        cached = _JSONL_CACHE.get(f)
        if cached and cached[0] == sig:
            parsed = cached[1]
        else:
            cwd = ""
            sid = os.path.basename(f)[-42:-6]
            model = "gpt-6-astra"
            effort = "default"
            in_task = False
            last_start_ts = 0.0
            recs = []
            # Read session_meta from first 3 lines + tail lines
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    head = [fh.readline() for _ in range(5)]
            except Exception:
                head = []
            for ln in head + _tail_lines(f):
                if not ln or not ln.strip():
                    continue
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                ty = d.get("type")
                p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
                t = _parse_iso_ts(d.get("timestamp"))
                if ty == "session_meta":
                    cwd = p.get("cwd") or cwd
                    sid = p.get("id") or sid
                elif ty == "turn_context":
                    model = p.get("model") or model
                    cwd = p.get("cwd") or cwd
                    effort = p.get("effort") or effort
                elif ty == "event_msg":
                    ety = p.get("type")
                    if ety == "task_started":
                        in_task = True
                        last_start_ts = t or last_start_ts
                    elif ety in ("task_complete", "turn_aborted"):
                        in_task = False
                elif ty == "token_usage_record" and t:
                    us = p.get("usage") or {}
                    recs.append((t, model, effort, cwd, sid, us.get("output_tokens") or 0, us.get("total_tokens") or us.get("input_tokens") or 0))
            parsed = {"in_task": in_task, "start_ts": last_start_ts, "model": model, "effort": effort, "cwd": cwd, "sid": sid, "mtime": sig[0] / 1e9, "recs": recs[-3:]}
            _JSONL_CACHE[f] = (sig, parsed)

        proj = _base_dir(parsed["cwd"]) or "Codex"
        sid = str(parsed["sid"])
        if parsed["in_task"] and (now - parsed["mtime"] < 600):
            ev_ts = max(parsed["start_ts"], parsed["mtime"])
            hms, ymd, full_dt = _fmt_hms(ev_ts)
            m = parsed["model"]
            items.append({
                "id": f"cx-live-{sid[:8]}",
                "provider": "chatgpt",
                "ts": ev_ts,
                "time": hms,
                "date": ymd,
                "full_time": full_dt,
                "group": f"group/{m.split('::')[-1]}",
                "raw_model": m,
                "display_model": m,
                "model_enum": None,
                "effort": _extract_effort("", m, parsed["effort"]),
                "is_active": True,
                "state": "answering",
                "state_text": "正在回答...",
                "account": "Codex",
                "project": proj,
                "session": sid[:8],
                "full_session": sid,
                "role": "Codex 会话",
                "title": "",
                "output_tokens": 0,
                "total_tokens": 0,
            })
        for t, m, eff, cwd, s_id, out_tok, tot_tok in parsed["recs"]:
            hms, ymd, full_dt = _fmt_hms(t)
            items.append({
                "id": f"cx-{str(s_id)[:8]}-{int(t * 1000)}",
                "provider": "chatgpt",
                "ts": t,
                "time": hms,
                "date": ymd,
                "full_time": full_dt,
                "group": f"group/{m.split('::')[-1]}",
                "raw_model": m,
                "display_model": m,
                "model_enum": None,
                "effort": _extract_effort("", m, eff),
                "is_active": False,
                "state": "done",
                "state_text": "",
                "account": "Codex",
                "project": _base_dir(cwd) or proj,
                "session": str(s_id)[:8],
                "full_session": str(s_id),
                "role": "Codex 会话",
                "title": "",
                "output_tokens": out_tok,
                "total_tokens": tot_tok,
            })
    return items


def _collect_claude(now):
    items = []
    files = sorted(
        glob.glob(os.path.join(HOME, ".claude", "projects", "**", "*.jsonl"), recursive=True),
        key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
        reverse=True,
    )[:10]
    for f in files:
        sig = _file_sig(f)
        if not sig:
            continue
        cached = _JSONL_CACHE.get(f)
        if cached and cached[0] == sig:
            parsed = cached[1]
        else:
            cwd = ""
            sid = os.path.basename(f)[:-6]
            last_model = "claude"
            last_effort = "default"
            waiting_reply = False
            last_user_ts = 0.0
            seen_m = {}
            for ln in _tail_lines(f):
                if not ln or not ln.strip():
                    continue
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                ty = d.get("type")
                t = _parse_iso_ts(d.get("timestamp"))
                cwd = d.get("cwd") or cwd
                sid = d.get("sessionId") or sid
                if ty == "user" and t:
                    waiting_reply = True
                    last_user_ts = t
                elif ty == "assistant":
                    m = d.get("message") or {}
                    mod = m.get("model") or ""
                    if mod.startswith("<"):
                        continue
                    if mod:
                        last_model = mod
                    eff = d.get("effort") or last_effort
                    last_effort = eff
                    us = m.get("usage") or {}
                    out_tok = us.get("output_tokens") or 0
                    tot_tok = (us.get("input_tokens") or 0) + out_tok + (us.get("cache_read_input_tokens") or 0) + (us.get("cache_creation_input_tokens") or 0)
                    mid = m.get("id") or d.get("uuid") or str(t)
                    if m.get("stop_reason"):
                        waiting_reply = False
                    if t:
                        prev = seen_m.get(mid)
                        if not prev or out_tok >= prev[5]:
                            seen_m[mid] = (t, last_model, eff, cwd, sid, out_tok, tot_tok, bool(d.get("isSidechain")))
            recs = sorted(seen_m.values(), key=lambda x: x[0])[-3:]
            parsed = {"waiting": waiting_reply, "user_ts": last_user_ts, "model": last_model, "effort": last_effort, "cwd": cwd, "sid": sid, "mtime": sig[0] / 1e9, "recs": recs}
            _JSONL_CACHE[f] = (sig, parsed)

        proj = _base_dir(parsed["cwd"]) or "Claude Code"
        sid = str(parsed["sid"])
        if parsed["waiting"] and (now - parsed["mtime"] < 300):
            ev_ts = max(parsed["user_ts"], parsed["mtime"])
            hms, ymd, full_dt = _fmt_hms(ev_ts)
            m = parsed["model"]
            items.append({
                "id": f"cl-live-{sid[:8]}",
                "provider": "claude",
                "ts": ev_ts,
                "time": hms,
                "date": ymd,
                "full_time": full_dt,
                "group": f"group/{m.split('::')[-1]}",
                "raw_model": m,
                "display_model": m,
                "model_enum": None,
                "effort": _extract_effort("", m, parsed["effort"]),
                "is_active": True,
                "state": "answering",
                "state_text": "正在回答...",
                "account": "Claude Code",
                "project": proj,
                "session": sid[:8],
                "full_session": sid,
                "role": "Claude 会话",
                "title": "",
                "output_tokens": 0,
                "total_tokens": 0,
            })
        for t, m, eff, cwd, s_id, out_tok, tot_tok, is_sub in parsed["recs"]:
            hms, ymd, full_dt = _fmt_hms(t)
            items.append({
                "id": f"cl-{str(s_id)[:8]}-{int(t * 1000)}",
                "provider": "claude",
                "ts": t,
                "time": hms,
                "date": ymd,
                "full_time": full_dt,
                "group": f"group/{m.split('::')[-1]}",
                "raw_model": m,
                "display_model": m,
                "model_enum": None,
                "effort": _extract_effort("", m, eff),
                "is_active": False,
                "state": "done",
                "state_text": "",
                "account": "Claude Code",
                "project": _base_dir(cwd) or proj,
                "session": str(s_id)[:8],
                "full_session": str(s_id),
                "role": "子代理" if is_sub else "主会话",
                "title": "",
                "output_tokens": out_tok,
                "total_tokens": tot_tok,
            })
    return items


def get_live_feed(ttl=1.2):
    now = time.time()
    with _LOCK:
        if _CACHE["data"] is not None and (now - _CACHE["ts"] < ttl):
            return _CACHE["data"]

    all_items = _collect_antigravity(now) + _collect_codex(now) + _collect_claude(now)
    # Active conversations pinned at top (newest first), followed by chronological feed of recent responses
    all_items.sort(key=lambda x: (1 if x["is_active"] else 0, x["ts"]), reverse=True)
    trimmed = all_items[:50]

    active_by_prov = {"gemini": 0, "chatgpt": 0, "claude": 0}
    active_models = {}
    for it in all_items:
        if "model_family" not in it:
            it["model_family"] = str(it.get("group") or "").removeprefix("group/") or it.get("raw_model", "?")
        if it["is_active"]:
            p = it["provider"]
            active_by_prov[p] = active_by_prov.get(p, 0) + 1
            mk = f"{it['model_family']} ({it['effort']})" if it.get("effort") and it["effort"] != "default" else it["model_family"]
            active_models[mk] = active_models.get(mk, 0) + 1

    res = {
        "at": dt.datetime.fromtimestamp(now, TZ).isoformat(timespec="seconds"),
        "active_count": sum(active_by_prov.values()),
        "active_by_provider": active_by_prov,
        "active_models": active_models,
        "items": trimmed,
    }
    with _LOCK:
        _CACHE["ts"] = now
        _CACHE["data"] = res
    return res


if __name__ == "__main__":
    print(json.dumps(get_live_feed(0), ensure_ascii=False, indent=2)[:3500])
