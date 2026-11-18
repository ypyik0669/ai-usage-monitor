"""Local live usage dashboard: recomputes usage every minute and serves 2s real-time stream at http://127.0.0.1:8797
No Claude involved. Git sync with the other computer runs every 15 minutes.
Start: pythonw -X utf8 serve.py   (a logon task starts it automatically)"""
import http.server, json, os, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import live_feed

PORT = int(os.environ.get('USAGE_DASHBOARD_PORT', '8797'))
PY = sys.executable.replace('pythonw.exe', 'python.exe')
NOWIN = 0x08000000 if os.name == 'nt' else 0  # CREATE_NO_WINDOW on Windows


def loop():
    last_git = 0
    while True:
        start = time.time()
        args = [PY, '-X', 'utf8', os.path.join(HERE, 'agg.py')]
        if start - last_git < 15 * 60:
            args.append('--no-git')
        else:
            last_git = start
        tmp_log = os.path.join(HERE, 'serve.log.tmp')
        fin_log = os.path.join(HERE, 'serve.log')
        try:
            # Write child stdout/stderr directly to file instead of PIPE so Windows grandchild handles never deadlock
            with open(tmp_log, 'w', encoding='utf-8', errors='replace') as lf:
                lf.write(time.strftime('%Y-%m-%d %H:%M:%S') + '\n')
                lf.flush()
                p = subprocess.Popen(
                    args,
                    cwd=HERE,
                    stdin=subprocess.DEVNULL,
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    creationflags=NOWIN,
                )
                try:
                    p.wait(timeout=180)
                except subprocess.TimeoutExpired:
                    p.kill()
                    lf.write('\n[timeout] agg.py killed after 180s\n')
            os.replace(tmp_log, fin_log)
        except Exception as e:
            try:
                open(fin_log, 'w', encoding='utf-8').write(time.strftime('%Y-%m-%d %H:%M:%S') + '\n' + repr(e))
            except Exception:
                pass
        time.sleep(max(5, 60 - (time.time() - start)))


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=HERE, **k)

    def do_GET(self):
        clean_path = self.path.split('?')[0]
        if clean_path == '/live.json':
            try:
                payload = json.dumps(live_feed.get_live_feed(1.2), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except Exception as e:
                err = json.dumps({'error': str(e), 'items': [], 'active_count': 0}).encode('utf-8')
                self.send_response(500)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(err)))
                self.end_headers()
                self.wfile.write(err)
            return

        if clean_path in ('/', '/index.html'):
            self.path = '/dashboard.html'
            clean_path = '/dashboard.html'
        if clean_path not in ('/dashboard.html', '/usage.json'):
            return self.send_error(404)
        return super().do_GET()

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    threading.Thread(target=loop, daemon=True).start()
    http.server.ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
