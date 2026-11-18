"""Aggregate local Claude Code, Codex, and Antigravity session logs into day-level fact tables (UTC+8)."""
import re, json, os, sys, glob, collections, urllib.parse, datetime as dt

HOME = os.path.expanduser('~')
HERE = os.path.dirname(os.path.abspath(__file__))
import socket
HOST = socket.gethostname()
ARGS = [a for a in sys.argv[1:] if not a.startswith('--')]
NO_GIT = '--no-git' in sys.argv   # serve.py: merge what is already pulled, skip pull/push
OUT = ARGS[0] if ARGS else os.path.join(HERE, 'usage.json')
# USD per 1M tokens: input, output, cache read. Cache writes: 1.25x input (5 min), 2x input (1 hour).
# Claude: Anthropic list prices (cached 2026-11-03). GPT: third-party price trackers, 2026-11. No long-context surcharge.
PRICES = {
    'claude-fable-5-2': (8, 40, 0.20), 'claude-fable-5-1': (10, 50, 1.0), 'claude-fable-5': (10, 50, 1.0),
    'claude-opus-5-5': (4, 20, 0.20), 'claude-opus-5': (5, 25, 0.5), 'claude-opus-4-8': (5, 25, 0.5), 'claude-opus-4-5': (5, 25, 0.5),
    'claude-sonnet-5-5': (2, 10, 0.10), 'claude-sonnet-5': (2, 10, 0.2), 'claude-sonnet-4-6': (3, 15, 0.3),
    'claude-haiku-5-5': (0.10, 0.50, 0.01), 'claude-haiku-4-5': (1, 5, 0.1),
    'gpt-6-astra': (10, 50, 1.0), 'gpt-6.1-sol': (2, 10, 0.10),
    'claude-opus-4-6': (5, 25, 0.5),
    # Gemini: third-party write-ups of Google's price page, 2026-11 (Carbon/Argon double on 2026-12-01).
    'gemini-4-hydrogen': (1.5, 7.5, 0.075), 'gemini-4-carbon': (2, 10, 0.1), 'gemini-4-argon': (2, 10, 0.1),
    'gemini-pro-orchestrator': (2, 10, 0.1), 'gemini-pro-orchestrator-argon': (2, 10, 0.1),
    'gemini-3.8-flash': (0.30, 2.50, 0.03), 'gemini-3.6-flash': (0.30, 2.50, 0.03),
    'gemini-3.1-pro': (1.25, 10.0, 0.125), 'gemini-pro-default': (1.25, 10.0, 0.125),
}
def norm_model_key(model):
    s = str(model or '').split('::')[-1].strip()
    sl = s.lower()
    if sl.startswith('orchestrator'):
        return 'gemini-4-argon' if 'argon' in sl else 'gemini-4-carbon'
    m = re.sub(r'\s*\(.*\)$', '', s).strip().lower().replace(' ', '-').removesuffix('-thinking')
    m = re.sub(r'-(tiered-)?(xhigh|high|medium|low)$', '', m)
    return m
def price_of(model):
    m = norm_model_key(model)
    if m in PRICES: return PRICES[m]
    head, _, tail = m.rpartition('-')
    return PRICES.get(head) if len(tail) == 8 and tail.isdigit() else None
def cost_of(model, u):
    p = price_of(model)
    if not p: return 0.0
    w1h = u.get('cw1h', 0); w5m = max(u['cache_write'] - w1h, 0)
    return (u['input'] * p[0] + u['output'] * p[1] + u['cache_read'] * p[2] + w5m * p[0] * 1.25 + w1h * p[0] * 2) / 1e6
TZ = dt.timezone(dt.timedelta(hours=8))
D = collections.defaultdict
hour = D(lambda: [0] * 7)   # (provider,date,hour,model,project) -> input,output,cache_read,cache_write,reasoning,messages,cost
dim = D(lambda: [0] * 3)    # (provider,date,model,dimension,key) -> messages,total,output
tool = D(int)               # (provider,date,model,tool) -> calls
sess = D(lambda: [0] * 4)   # (provider,date,model,session,project) -> messages,output,total,cost

def ts(s):
    try: return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(TZ)
    except Exception: return None
def base(p):
    p = (p or '').strip()
    if p.startswith('['):
        try:
            arr = json.loads(p)
            p = arr[0] if arr else ''
        except Exception: pass
    if p.startswith('file:///'):
        p = urllib.parse.unquote(p[8:])
    p = p.rstrip('\\/')
    return (p.replace('\\', '/').split('/')[-1] or p or '(unknown)')[:60]
def rec(pv, t, model, project, sid, u, dims):
    day = t.strftime('%Y-%m-%d'); total = u['input'] + u['output'] + u['cache_read'] + u['cache_write']
    h = hour[(pv, day, t.hour, model, project)]
    for i, k in enumerate(('input', 'output', 'cache_read', 'cache_write', 'reasoning')): h[i] += u[k]
    h[5] += 1; c = cost_of(model, u); h[6] += c
    for k, v in dict(dims, **{'电脑': HOST}).items():
        d = dim[(pv, day, model, k, str(v))]; d[0] += 1; d[1] += total; d[2] += u['output']
    s = sess[(pv, day, model, str(sid)[:8], project)]; s[0] += 1; s[1] += u['output']; s[2] += total; s[3] += c

def small_lines(path, cap=4 << 20):
    """Yield the file's lines as text, skipping any line longer than cap (pasted images etc.) without holding it in memory."""
    with open(path, 'rb') as fh:
        while True:
            ln = fh.readline(cap)
            if not ln: return
            if not ln.endswith(b'\n') and len(ln) >= cap:
                while True:
                    rest = fh.readline(cap)
                    if not rest or rest.endswith(b'\n'): break
                continue
            yield ln.decode('utf-8', 'ignore')

# ---------------- Claude Code ----------------
seen = {}; tool_seen = set()
files = glob.glob(os.path.join(HOME, '.claude', 'projects', '**', '*.jsonl'), recursive=True)
for f in files:
    if True:
        for line in small_lines(f):
            if '"usage"' not in line or '"assistant"' not in line: continue
            try: d = json.loads(line)
            except Exception: continue
            if d.get('type') != 'assistant': continue
            m = d.get('message') or {}
            us = m.get('usage'); model = m.get('model') or '?'
            if not us or model.startswith('<'): continue
            t = ts(d.get('timestamp') or '')
            if not t: continue
            for c in m.get('content') or []:
                if isinstance(c, dict) and c.get('type') == 'tool_use':
                    tid = c.get('id')
                    if tid and tid in tool_seen: continue
                    tool_seen.add(tid); tool[('claude', t.strftime('%Y-%m-%d'), model, c.get('name', '?'))] += 1
            mid = m.get('id') or d.get('uuid'); out = us.get('output_tokens') or 0
            prev = seen.get(mid)
            if prev is not None and prev[0] >= out: continue
            seen[mid] = (out, t, model, base(d.get('cwd')), d.get('sessionId') or os.path.basename(f),
                         dict(input=us.get('input_tokens') or 0, output=out, cache_read=us.get('cache_read_input_tokens') or 0,
                              cache_write=us.get('cache_creation_input_tokens') or 0,
                              reasoning=(us.get('output_tokens_details') or {}).get('thinking_tokens') or 0,
                              cw1h=(us.get('cache_creation') or {}).get('ephemeral_1h_input_tokens') or 0),
                         {'入口': d.get('entrypoint') or '未知', '推理强度': d.get('effort') or '未记录',
                          '主线程 / 子代理': '子代理' if d.get('isSidechain') else '主线程', '速度': us.get('speed') or '未记录'})
for r in seen.values(): rec('claude', *r[1:])
del seen, tool_seen

# ---------------- Codex (ChatGPT plan) ----------------
lim_hist = {}; resp_seen = set(); codex_last = None
cfiles = glob.glob(os.path.join(HOME, '.codex', 'sessions', '**', '*.jsonl'), recursive=True) + \
         glob.glob(os.path.join(HOME, '.codex', 'archived_sessions', '**', '*.jsonl'), recursive=True)
for f in cfiles:
    model = '?'; cwd = ''; sid = os.path.basename(f); effort = '未记录'; origin = '未知'
    has_rec = False; prev_tot = None; pend = []
    if True:
        for line in small_lines(f):
            if '"type":"response_item"' in line[:120]: continue
            try: d = json.loads(line)
            except Exception: continue
            ty = d.get('type'); p = d.get('payload') or {}
            if not isinstance(p, dict): continue
            if ty == 'session_meta':
                cwd = p.get('cwd') or cwd; sid = p.get('id') or sid; origin = p.get('originator') or origin
            elif ty == 'turn_context':
                model = p.get('model') or model; cwd = p.get('cwd') or cwd; effort = p.get('effort') or effort
            elif ty == 'token_usage_record':
                us = p.get('usage') or {}; rid = p.get('response_id'); t = ts(d.get('timestamp') or '')
                if not t or (rid and rid in resp_seen): continue
                if rid: resp_seen.add(rid)
                has_rec = True; ci = us.get('cached_input_tokens') or 0
                rec('chatgpt', t, model, base(cwd), sid, dict(input=max((us.get('input_tokens') or 0) - ci, 0), output=us.get('output_tokens') or 0,
                    cache_read=ci, cache_write=us.get('cache_write_input_tokens') or 0, reasoning=us.get('reasoning_output_tokens') or 0),
                    {'推理强度': effort, '入口': origin})
            elif ty == 'event_msg' and p.get('type') == 'token_count':
                t = ts(d.get('timestamp') or '')
                if not t: continue
                rl = p.get('rate_limits') or {}
                if rl.get('primary') and (codex_last is None or codex_last[0] <= t): codex_last = (t, rl)
                for nm in ('primary', 'secondary'):
                    w = rl.get(nm)
                    if w and w.get('used_percent') is not None:
                        row = dict(provider='chatgpt', at=t.strftime('%Y-%m-%dT%H:%M'), day=t.strftime('%Y-%m-%d'),
                                   window_hours=round((w.get('window_minutes') or 0) / 60), used_percent=w['used_percent'], plan=rl.get('plan_type') or '')
                        k = (row['day'], t.hour, row['window_hours'])
                        if k not in lim_hist or lim_hist[k]['at'] < row['at']: lim_hist[k] = row
                tot = (p.get('info') or {}).get('total_token_usage')
                if tot:
                    delta = tot if prev_tot is None else {k: (tot.get(k) or 0) - (prev_tot.get(k) or 0) for k in tot}
                    prev_tot = tot
                    if (delta.get('total_tokens') or 0) > 0:
                        ci = max(delta.get('cached_input_tokens') or 0, 0)
                        pend.append(('chatgpt', t, model, base(cwd), sid, dict(input=max((delta.get('input_tokens') or 0) - ci, 0),
                                     output=max(delta.get('output_tokens') or 0, 0), cache_read=ci,
                                     cache_write=max(delta.get('cache_write_input_tokens') or 0, 0),
                                     reasoning=max(delta.get('reasoning_output_tokens') or 0, 0)), {'推理强度': effort, '入口': origin}))
    if not has_rec:
        for a in pend: rec(*a)

# ---------------- Antigravity (Gemini plan), this computer only ----------------
import sqlite3
def _rv(b, i):
    r = s = 0
    while True:
        x = b[i]; i += 1; r |= (x & 0x7f) << s; s += 7
        if x < 0x80: return r, i
def _pb(b, depth=0):
    out = {}; i = 0
    try:
        while i < len(b):
            k, i = _rv(b, i); f, ty = k >> 3, k & 7
            if ty == 0: v, i = _rv(b, i)
            elif ty == 2:
                n, i = _rv(b, i); v = b[i:i + n]; i += n
            elif ty == 1: v = None; i += 8
            elif ty == 5: v = None; i += 4
            else: return None
            out.setdefault(f, []).append(v)
    except Exception: return None
    return out
def _sub(d, *path):
    for f in path:
        if not d or f not in d: return None
        v = d[f][0]
        d = _pb(v) if isinstance(v, (bytes, bytearray)) else v
    return d
def _str(d, f):
    v = (d or {}).get(f, [b''])[0]
    return v.decode('utf-8', 'replace') if isinstance(v, (bytes, bytearray)) else ''

# Map Antigravity conversation -> (project_name, is_subagent) from conversation_summaries.db
agy_meta = {}
cs_path = os.path.join(HOME, '.gemini', 'antigravity', 'conversation_summaries.db')
if os.path.exists(cs_path):
    try:
        cs_con = sqlite3.connect('file:%s?mode=ro' % cs_path, uri=True, timeout=2.0)
        raw_cs = {}
        for cid, ws, ag_name, par_id, depth in cs_con.execute(
            'SELECT conversation_id, workspace_uris, agent_name, parent_conversation_id, nesting_depth FROM conversation_summaries'
        ):
            raw_cs[cid] = (ws or '', ag_name or '', par_id or '', int(depth or 0))
        cs_con.close()
        for cid, (ws, ag_name, par_id, depth) in raw_cs.items():
            cur = cid; seen_c = set(); proj_name = ''
            while cur and cur in raw_cs and cur not in seen_c and len(seen_c) < 10:
                seen_c.add(cur)
                w_cur = raw_cs[cur][0]
                if w_cur and w_cur != '[]':
                    b = base(w_cur)
                    if b and b != '(unknown)':
                        proj_name = b; break
                cur = raw_cs[cur][2]
            is_sub = (depth > 0) or bool(ag_name)
            agy_meta[cid] = (proj_name or 'Antigravity (未绑定目录)', '子代理' if is_sub else '主线程')
    except Exception as e:
        print('conversation_summaries warning:', e)

def _agy_effort(disp, raw_slug):
    m = re.search(r'\((XHigh|High|Medium|Low)\)', disp or '', re.I)
    if m: return m.group(1).lower()
    m2 = re.search(r'-(xhigh|high|medium|low)(?:$|-)', raw_slug or '', re.I)
    if m2: return m2.group(1).lower()
    return 'high' if 'Orchestrator' in (disp or '') else '未记录'

def _agy_raw_slug(gm, disp):
    s = _str(gm, 19).strip()
    if s: return s
    enum_id = (gm.get(3) or [None])[0]
    if enum_id == 1427 or 'Carbon' in (disp or ''): return 'gemini-4-carbon'
    if enum_id == 1530 or 'Hydrogen' in (disp or ''): return 'gemini-4-hydrogen-tiered-high'
    return norm_model_key(disp) or '?'

for f in glob.glob(os.path.join(HOME, '.gemini', 'antigravity', 'conversations', '*.db')):
    sid = os.path.basename(f)[:-3]
    proj_name, thread_kind = agy_meta.get(sid, ('Antigravity (未绑定目录)', '未知'))
    try:
        con = sqlite3.connect('file:%s?mode=ro' % f, uri=True, timeout=2.0)
        when = {}; first = None
        for (m,) in con.execute('select metadata from steps where metadata is not null'):
            md = _pb(m); u = _sub(md, 9); t1 = _sub(md, 1, 1)
            key = _str(_sub(u, 7), 12) if u else ''
            if u and t1:
                if key: when[key] = t1
                when[(u.get(2, [0])[0], u.get(3, [0])[0])] = t1
            if t1: first = min(first, t1) if first else t1
        models = collections.Counter()
        for (g,) in con.execute('select data from gen_metadata'):
            gm = _sub(_pb(g), 1); u = _sub(gm, 4)
            if not u: continue
            key = _str(_sub(u, 7), 12)
            t1 = when.get(key) or when.get((u.get(2, [0])[0], u.get(3, [0])[0])) or first
            if not t1: continue
            model = _str(gm, 21) or _str(gm, 19) or '?'
            models[model] += 1
            raw_slug = _agy_raw_slug(gm, model)
            eff = _agy_effort(model, raw_slug)
            out = u.get(3, [0])[0]; think = u.get(9, [0])[0]
            rec('gemini', dt.datetime.fromtimestamp(t1, TZ), model, proj_name, sid,
                dict(input=u.get(2, [0])[0], output=out, cache_read=u.get(5, [0])[0], cache_write=0, reasoning=think),
                {'入口': 'Antigravity', '主线程 / 子代理': thread_kind, '推理强度': eff, '底层模型 ID': raw_slug})
        cm = models.most_common(1)[0][0] if models else '?'
        for (m, pl) in con.execute('select substr(metadata,1,4000), substr(step_payload,1,600) from steps where step_payload is not null'):
            tm = re.search(rb'(?:call_|toolu_)[\w-]{6,}.{1,6}?([a-z][a-z_]{2,40})\b', pl or b'', re.S)
            t1 = _sub(_pb(m), 1, 1) if m else None
            if tm and t1:
                tool[('gemini', dt.datetime.fromtimestamp(t1, TZ).strftime('%Y-%m-%d'), cm, tm.group(1).decode())] += 1
        con.close()
    except Exception as e:
        print('antigravity skipped', sid[:8], e)

# ---------------- write ----------------
def table(cols, d):
    return {'columns': cols, 'rows': [list(k) + [round(x, 4) if isinstance(x, float) else x for x in (v if isinstance(v, list) else [v])] for k, v in sorted(d.items())]}

# Plan limits. Claude: limits.json next to this script holds the `plan` object of the app's usage card (written by the refresh task).
# Codex: the newest rate-limit record in its own logs.
limits = []
lp = os.path.join(HERE, 'limits.json')
if os.path.exists(lp):
    try:
        pl = json.load(open(lp, encoding='utf-8'))
        if isinstance(pl.get('plan'), dict): pl = pl['plan']
        cap = dt.datetime.fromtimestamp(os.path.getmtime(lp), TZ).isoformat(timespec='minutes')
        ex = pl.get('extraUsage') or {}
        extra = ('额外用量已开启，已花 ' if ex.get('enabled') else '额外用量未开启，已花 ') + '$' + str(ex.get('spent', '0.00'))
        for w in pl.get('windows') or []:
            label = str(w.get('label', '')).replace('Weekly', '每周').replace('all models', '全部模型').replace('5-hour', '5 小时').replace('Session', '当前会话')
            limits.append(dict(provider='claude', plan=pl.get('plan', ''), label=label, percent_used=w.get('percentUsed'),
                               resets_at=ts(w['resetsAt']).isoformat(timespec='minutes') if w.get('resetsAt') else '', captured_at=cap, extra=extra))
    except Exception as e:
        print('limits.json unreadable:', e)
if codex_last:
    t0, rl = codex_last; cr = rl.get('credits') or {}
    for nm in ('primary', 'secondary'):
        w = rl.get(nm)
        if not w: continue
        hrs = round((w.get('window_minutes') or 0) / 60)
        limits.append(dict(provider='chatgpt', plan=rl.get('plan_type') or '', label=('每周' if hrs >= 168 else str(hrs) + ' 小时') + ' · Codex', percent_used=w.get('used_percent'),
                           resets_at=dt.datetime.fromtimestamp(w['resets_at'], TZ).isoformat(timespec='minutes') if w.get('resets_at') else '',
                           captured_at=t0.isoformat(timespec='minutes'), extra=('有 credits 余额 ' + str(cr.get('balance'))) if cr.get('has_credits') else '没有 credits 余额'))
# Other computers: this folder is a git clone of a private repo. Every machine commits its own tables to
# data/usage-<HOST>.json and adds in the tables the other machines pushed there.
import subprocess
def git(*a):
    try:
        r = subprocess.run(['git', '-C', HERE] + list(a), stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        return r.returncode, ((r.stdout or '') + (r.stderr or '')).strip()
    except Exception as e:
        return 1, str(e)
if os.path.isdir(os.path.join(HERE, '.git')):
    SYNC = os.path.join(HERE, 'data'); os.makedirs(SYNC, exist_ok=True)
    rc, out = (0, '') if NO_GIT else git('pull', '--rebase', '--autostash', '-q')
    if rc: print('git pull failed:', out[-200:])
    own = 'usage-%s.json' % HOST
    def lines(d):   # one row per line, sorted, so each commit is a small diff
        return '[\n' + ',\n'.join(json.dumps([list(k), [round(x, 4) if isinstance(x, float) else x for x in v] if isinstance(v, list) else v],
                                               ensure_ascii=False, separators=(',', ':')) for k, v in sorted(d.items())) + '\n]'
    with open(os.path.join(SYNC, own), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('{"host":%s,\n"hour":%s,\n"dim":%s,\n"sess":%s,\n"tool":%s}\n' % (json.dumps(HOST), lines(hour), lines(dim), lines(sess), lines(tool)))
    if not NO_GIT: git('add', 'data/' + own)
    rc, out = (1, '') if NO_GIT else git('commit', '-q', '-m', 'usage ' + HOST, '--', 'data/' + own)
    if rc == 0:
        rc, out = git('push', '-q')
        print('pushed own usage' if rc == 0 else 'git push failed: ' + out[-200:])
    for f in sorted(glob.glob(os.path.join(SYNC, 'usage-*.json'))):
        if os.path.basename(f) == own: continue
        try: other = json.load(open(f, encoding='utf-8'))
        except Exception as e:
            print('skipped', os.path.basename(f), e); continue
        for name, tgt in (('hour', hour), ('dim', dim), ('sess', sess)):
            for k, v in other.get(name, []):
                row = tgt[tuple(k)]
                for i, x in enumerate(v): row[i] += x
        for k, v in other.get('tool', []): tool[tuple(k)] += v
        print('merged', other.get('host'))
# Antigravity quota: ask the running Antigravity app's local server (same call its Models & Usage page makes).
def antigravity_call(rpc):
    import ssl, urllib.request
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    candidates = []
    try:
        if os.name == 'nt':
            out = subprocess.run(['powershell', '-NoProfile', '-Command', "Get-CimInstance Win32_Process | ? { $_.Name -like 'language_server*' } | % { $_.ProcessId.ToString() + '|' + $_.CommandLine }"],
                                 stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20).stdout
            ns = subprocess.run(['netstat', '-ano'], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15).stdout
            for line in out.splitlines():
                if '|' not in line or 'antigravity' not in line.lower(): continue
                pid, cmd = line.split('|', 1); tok = re.search(r'--csrf_token\s+(\S+)', cmd)
                if not tok: continue
                ports = sorted({m.group(1) for m in re.finditer(r'127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING\s+' + pid.strip() + r'\b', ns)})
                candidates.append((tok.group(1), ports))
        else:
            out = subprocess.run(['ps', '-ax', '-o', 'pid=,command='], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                if 'language_server' not in line or 'antigravity' not in line.lower(): continue
                m_pid = re.match(r'\s*(\d+)\s+(.*)', line)
                if not m_pid: continue
                pid, cmd = m_pid.group(1), m_pid.group(2)
                tok = re.search(r'--csrf_token\s+(\S+)', cmd)
                if not tok: continue
                ls = subprocess.run(['lsof', '-nP', '-iTCP', '-sTCP:LISTEN', '-a', '-p', pid], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10).stdout
                ports = sorted({m.group(1) for m in re.finditer(r'127\.0\.0\.1:(\d+)', ls)})
                candidates.append((tok.group(1), ports))
    except Exception:
        return None
    for csrf_tok, ports in candidates:
        for port in ports:
            for scheme in ('https', 'http'):
                try:
                    req = urllib.request.Request('%s://127.0.0.1:%s/exa.language_server_pb.LanguageServerService/%s' % (scheme, port, rpc), data=b'{}',
                                                 headers={'Content-Type': 'application/json', 'X-Codeium-Csrf-Token': csrf_tok, 'Connect-Protocol-Version': '1'})
                    return json.loads(urllib.request.urlopen(req, context=ctx if scheme == 'https' else None, timeout=4).read())
                except Exception:
                    continue
    return None
gq = (antigravity_call('RetrieveUserQuotaSummary') or {}).get('response'); qh_path = os.path.join(HERE, 'antigravity_quota.json')
if gq:
    us = (antigravity_call('GetUserStatus') or {}).get('userStatus') or {}
    tier = us.get('userTier') or {}
    cred = sum(int(c.get('creditAmount') or c.get('amount') or 0) for c in tier.get('availableCredits') or [])
    gq = {'at': dt.datetime.now(TZ).isoformat(timespec='minutes'), 'quota': gq, 'plan': tier.get('name') or (us.get('planStatus') or {}).get('planInfo', {}).get('planName', ''), 'credits': cred}
    hist = []
    try: hist = json.load(open(qh_path, encoding='utf-8')).get('history', [])
    except Exception: pass
    for g in gq['quota'].get('groups', []):
        for b in g.get('buckets', []):
            hist.append({'at': gq['at'][:16], 'bucket': b.get('bucketId'), 'used': round((1 - b.get('remainingFraction', 1)) * 100, 1)})
    json.dump({'latest': gq, 'history': hist[-5000:]}, open(qh_path, 'w', encoding='utf-8'), ensure_ascii=False)
else:
    try: gq = json.load(open(qh_path, encoding='utf-8')).get('latest')
    except Exception: gq = None
if gq:
    names = {'Gemini Models': 'Gemini', 'Claude and GPT models': 'Claude 与 GPT'}
    for g in gq['quota'].get('groups', []):
        for b in g.get('buckets', []):
            limits.append(dict(provider='gemini', plan=gq.get('plan') or 'Google AI', label=('每周' if b.get('window') == 'weekly' else '5 小时') + ' · ' + names.get(g.get('displayName'), g.get('displayName', '')),
                               percent_used=round((1 - b.get('remainingFraction', 1)) * 100, 1), resets_at=ts(b['resetTime']).isoformat(timespec='minutes') if b.get('resetTime') else '',
                               captured_at=gq['at'], extra='AI Credits 余额 %s（额度用完后才动用）' % gq.get('credits', 0)))
    try:
        for r in json.load(open(qh_path, encoding='utf-8')).get('history', []):
            if r['bucket'] == 'gemini-weekly':
                lim_hist[('g', r['at'])] = dict(provider='gemini', at=r['at'], day=r['at'][:10], window_hours=168, used_percent=r['used'], plan='Google AI')
    except Exception: pass

live_snap = None
try:
    sys.path.insert(0, HERE)
    import live_feed
    live_snap = live_feed.get_live_feed(0)
except Exception:
    pass

gen_at = dt.datetime.now(TZ).isoformat(timespec='seconds')
o = {
    'generated_at': gen_at,
    'live_snapshot': live_snap,
    'hour': table(['provider', 'date', 'hour', 'model', 'project', 'input', 'output', 'cache_read', 'cache_write', 'reasoning', 'messages', 'cost'],
                  {k: v for k, v in hour.items() if sum(v[:4]) > 0}),
    'dim': table(['provider', 'date', 'model', 'dimension', 'key', 'messages', 'total', 'output'], {k: v for k, v in dim.items() if v[1] > 0}),
    'tool': table(['provider', 'date', 'model', 'tool', 'calls'], tool),
    'sess': table(['provider', 'date', 'model', 'session', 'project', 'messages', 'output', 'total', 'cost'], {k: v for k, v in sess.items() if v[2] > 0}),
    'limit_history': sorted(lim_hist.values(), key=lambda r: r['at']),
    'limits': limits,
    'prices': [dict(model=k, input=v[0], output=v[1], cache_read=v[2], cache_write_5m=round(v[0] * 1.25, 4), cache_write_1h=v[0] * 2) for k, v in PRICES.items()],
    'meta': [{'label': 'agg.py', 'value': open(__file__, encoding='utf-8').read(), 'type': 'code', 'lang': 'python'},
             {'label': 'Input', 'value': '~/.claude/projects/**/*.jsonl（Claude Code 会话日志）；~/.codex/sessions/**/*.jsonl（Codex 会话日志）；~/.gemini/antigravity/conversations/*.db 与 conversation_summaries.db（Antigravity 对话记录、项目归属与真实模型）。时间按 UTC+8 分桶。'}],
}
tmp_out = OUT + '.tmp'
with open(tmp_out, 'w', encoding='utf-8') as fh:
    json.dump(o, fh, ensure_ascii=False, separators=(',', ':'))
os.replace(tmp_out, OUT)
cost = D(float)
for k, v in hour.items(): cost[k[0]] += v[6]
print('wrote', OUT, os.path.getsize(OUT), 'bytes;', {k: len(v['rows']) for k, v in o.items() if isinstance(v, dict) and 'rows' in v}, '; limits', [(l['label'], l['percent_used']) for l in limits], '; cost USD', {k: round(v) for k, v in cost.items()})
